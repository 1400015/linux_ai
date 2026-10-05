"""Visible, explicitly approved conversation recovery without blocking GTK.

The same dialog handles startup validation and a failed live writer. Recovery
uses HistoryStore's byte-for-byte backup transaction, never a schema downgrade.
"""

from dataclasses import dataclass
from pathlib import Path
import threading

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError):
    GLib = Gtk = None

from .history_store import HistoryStore
from .i18n import _
from .storage import JsonLimitError


@dataclass
class HistoryRecoveryResult:
    status: str
    store: object = None
    backup: object = None
    error: object = None


def history_error_message(error):
    """Explain a failed history without exposing untrusted conversation data."""
    if isinstance(error, JsonLimitError):
        return _("The conversation history exceeds the size or nesting limit (16 MiB / 128 levels).")
    if isinstance(error, ValueError):
        return _("The conversation history has an invalid or unsupported format.")
    return _("The conversation history could not be read or saved.")


def _open_validated_store(path):
    store = HistoryStore(path)
    try:
        store.list_sessions()
    except Exception:
        store.close()
        raise
    return store


class HistoryRecoveryDialog(Gtk.Dialog if Gtk is not None else object):
    """Run the confirmation on GTK and the complete backup transaction outside it."""

    def __init__(self, parent=None, path=None, store=None, error=None, validate=False):
        if Gtk is None:
            raise RuntimeError("GTK 3 is required for graphical history recovery")
        super().__init__(title=_("Recover conversation history"), transient_for=parent, modal=True)
        self.path = Path(path) if path is not None else (
            store.path if store is not None
            else Path.home() / ".config/linux_ai_assistant/history.json")
        self.store = store
        self.error = error
        self.result = None
        self.busy = False
        self.validate_first = validate
        self._validation_loop = None
        self._cancel_requested = False
        self.set_default_size(560, 240)
        self.set_border_width(10)
        box = self.get_content_area()
        box.set_spacing(10)
        self.explanation = Gtk.Label(xalign=0, wrap=True)
        self.explanation.set_max_width_chars(72)
        box.pack_start(self.explanation, False, False, 0)
        self.detail = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.detail.set_max_width_chars(72)
        box.pack_start(self.detail, False, False, 0)
        self.notice = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.notice.set_max_width_chars(72)
        box.pack_start(self.notice, False, False, 0)
        self.spinner = Gtk.Spinner()
        box.pack_start(self.spinner, False, False, 0)
        self.cancel_button = self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.recover_button = self.add_button(_("Back up and start new history"), Gtk.ResponseType.APPLY)
        self.set_default_response(Gtk.ResponseType.CANCEL)
        self.connect("delete-event", lambda *_args: self.busy)
        if validate:
            self.explanation.set_text(_("Opening conversation history…"))
        else:
            self._offer_recovery(error)

    def _offer_recovery(self, error):
        self.error = error
        self.explanation.set_text(history_error_message(error))
        self.detail.set_text(_("The original file will be preserved in a private backup before a new "
                               "history is created. Unknown formats are not converted. "
                               "Cancelling keeps the original file unchanged."))
        self.notice.set_text(str(self.path))
        self.recover_button.set_sensitive(True)
        self.cancel_button.set_sensitive(True)

    def request_cancel(self):
        """Dismiss unapproved recovery, or wait for an approved transaction."""
        self._cancel_requested = True
        if not self.busy and self._validation_loop is None:
            self.response(Gtk.ResponseType.CANCEL)

    def _start(self, operation, completed):
        self.busy = True
        self.recover_button.set_sensitive(False)
        self.cancel_button.set_sensitive(False)
        self.spinner.show()
        self.spinner.start()

        def work():
            value, error = None, None
            try:
                value = operation()
            except Exception as caught:
                error = caught
            GLib.idle_add(self._finished, completed, value, error)

        # An approved backup/reset must finish even if process shutdown closes
        # GTK's event loop. It is bounded by the local regular history file.
        threading.Thread(target=work, name="history-recovery", daemon=False).start()

    def _finished(self, completed, value, error):
        self.busy = False
        self.spinner.stop()
        self.spinner.hide()
        completed(value, error)
        return GLib.SOURCE_REMOVE

    def _validation_finished(self, store, error):
        if error is not None:
            self._offer_recovery(error)
        else:
            self.result = HistoryRecoveryResult("ready", store=store)
        if self._validation_loop is not None:
            self._validation_loop.quit()

    def _recover(self):
        self.explanation.set_text(_("Preserving the original history and starting a new one…"))
        self.notice.set_text("")

        def operation():
            backup = None
            try:
                if self.store is not None:
                    backup = self.store.recover(confirmed=True)
                    store = self.store
                    store.list_sessions()
                else:
                    backup = HistoryStore.recover_file(self.path, confirmed=True)
                    store = _open_validated_store(self.path)
                return HistoryRecoveryResult("recovered", store=store, backup=backup)
            except Exception as error:
                # A post-backup failure must keep its durable original visible,
                # including a failure opening the freshly reset history.
                if backup is not None and not getattr(error, "recovery_backup", None):
                    error.recovery_backup = backup
                raise

        self._start(operation, self._recovery_finished)

    def _recovery_finished(self, result, error):
        if error is None:
            self.result = result
            self.response(Gtk.ResponseType.OK)
            return
        backup = getattr(error, "recovery_backup", None)
        self.result = HistoryRecoveryResult("failed", store=self.store, backup=backup, error=error)
        self.explanation.set_text(_("Conversation history recovery failed."))
        self.detail.set_text(history_error_message(error))
        self.notice.set_text(_("The original history was preserved at: {path}").format(path=backup)
                             if backup else _("The history could not be backed up. It was not reset."))
        self.recover_button.hide()
        self.cancel_button.set_label(_("Close"))
        self.cancel_button.set_sensitive(True)
        if self._cancel_requested:
            self.response(Gtk.ResponseType.CANCEL)

    def run_result(self):
        """Wait in GTK's nested event loop; all disk operations run in a worker."""
        try:
            if self.validate_first:
                # Background startup should stay invisible when history is
                # healthy, while the GLib loop can still dispatch activation.
                self._validation_loop = GLib.MainLoop()
                self._start(lambda: _open_validated_store(self.path), self._validation_finished)
                self._validation_loop.run()
                self._validation_loop = None
                if self.result is not None:
                    return self.result
                if self._cancel_requested:
                    return HistoryRecoveryResult("cancelled", store=self.store, error=self.error)
            self.show_all()
            self.spinner.hide()
            while True:
                response = self.run()
                # Escape and window-manager close must not abandon an approved
                # backup/reset transaction while its disk worker is running.
                if self.busy:
                    continue
                if response == Gtk.ResponseType.APPLY and self.result is None:
                    self._recover()
                    continue
                if self.result is not None:
                    return self.result
                return HistoryRecoveryResult("cancelled", store=self.store, error=self.error)
        finally:
            self.destroy()


def open_history_store(parent=None, path=None):
    """Validate startup history and offer byte-preserving recovery if necessary."""
    if Gtk is None:
        return HistoryRecoveryResult("failed", error=RuntimeError(
            "GTK 3 is required for graphical history recovery"))
    return HistoryRecoveryDialog(parent=parent, path=path, validate=True).run_result()


def recover_history(parent, store, error=None):
    """Recover an existing writer, retaining messages queued before the failure."""
    if Gtk is None:
        return HistoryRecoveryResult("failed", store=store, error=RuntimeError(
            "GTK 3 is required for graphical history recovery"))
    return HistoryRecoveryDialog(parent=parent, store=store,
                                 error=error or store.last_error).run_result()


def cancel_history_recovery_dialogs():
    """Application shutdown must also close GTK's nested confirmation loops."""
    active = False
    if Gtk is not None:
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, HistoryRecoveryDialog):
                active = True
                window.request_cancel()
    return active


def history_recovery_active():
    """Whether a recovery/validation caller still owns a nested dialog loop."""
    return Gtk is not None and any(isinstance(window, HistoryRecoveryDialog)
                                  for window in Gtk.Window.list_toplevels())
