"""Review file changes and explicitly confirm a guarded recovery."""

import threading

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk, Pango

from .file_actions import MAX_DIFF_BYTES, preview_diff
from .i18n import _


class ChangeDialog(Gtk.Dialog):
    def __init__(self, window):
        super().__init__(title=_("File changes"), transient_for=window, modal=True)
        self.set_destroy_with_parent(True)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self.set_default_size(760, 540)
        self.owner = window
        self.journal = window.change_journal
        self.session_id = window.history_store.active_session_id
        self.records = {}
        self._closed = False
        self._busy = False
        self._generation = 0
        self.connect('destroy', self._destroyed)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        self.all_sessions = Gtk.CheckButton(label=_("Show changes from all conversations"))
        self.all_sessions.connect('toggled', self._refresh)
        box.pack_start(self.all_sessions, False, False, 0)
        self.archived = Gtk.CheckButton(label=_("Include archived file changes"))
        self.archived.connect('toggled', self._refresh)
        box.pack_start(self.archived, False, False, 0)
        self.selector = Gtk.ComboBoxText()
        for cell in self.selector.get_cells():
            cell.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            cell.set_property('max-width-chars', 80)
        self.selector.connect('changed', self._display)
        box.pack_start(self.selector, False, False, 0)
        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        self.view.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)
        self.restore_button = Gtk.Button(label=_("Review recovery"))
        self.restore_button.connect('clicked', self._restore)
        box.pack_start(self.restore_button, False, False, 0)
        self.notice = Gtk.Label(label=_("Only approved file writes are recorded. Command effects are not automatically reversible."),
                                xalign=0, wrap=True)
        box.pack_start(self.notice, False, False, 0)
        self._refresh()

    def _allowed(self):
        return self.owner.config.get('permissions.allowed_edit_dirs', [])

    def _destroyed(self, widget):
        self._closed = True
        self._generation += 1
        self._busy = False

    def _set_busy(self, busy, notice=None):
        self._busy = busy
        if self._closed:
            return
        for widget in (self.all_sessions, self.archived, self.selector):
            widget.set_sensitive(not busy)
        self.restore_button.set_sensitive(not busy and self._can_restore())
        if notice is not None:
            self.notice.set_text(notice)

    def _can_restore(self):
        item = self.records.get(self.selector.get_active_id())
        return bool(item and item['status'] in ('applied', 'pending', 'uncertain'))

    def _worker(self, operation, completion, approved=False, supervise=False):
        def run():
            try:
                result, error = operation(), None
            except Exception as exception:
                result, error = None, str(exception)
            GLib.idle_add(completion, result, error)
        # Approved operations must finalize the journal even if GTK exits.
        # Recovery preview can also start pkexec: keep its bounded supervisor
        # alive until deadline/cleanup instead of orphaning an elevated helper.
        try:
            name = 'file-recovery' if approved else ('file-recovery-preview' if supervise else 'file-change-review')
            threading.Thread(target=run, name=name, daemon=not (approved or supervise)).start()
        except RuntimeError as exception:
            GLib.idle_add(completion, None, str(exception))

    def _refresh(self, widget=None, completion_notice=None):
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        session_id = None if self.all_sessions.get_active() else self.session_id
        include_archived = self.archived.get_active()
        self._set_busy(True, _("Loading file changes…"))
        self._worker(lambda: self.journal.list_changes(session_id, include_archived=include_archived),
                     lambda records, error: self._listed(generation, records, error, completion_notice))

    def _listed(self, generation, records, error, completion_notice):
        if self._closed or generation != self._generation:
            return False
        self._set_busy(False)
        if error is not None:
            self.notice.set_text(error)
            self.restore_button.set_sensitive(False)
        else:
            self.records = {item['id']: item for item in records}
            self.selector.remove_all()
            for item in records:
                self.selector.append(item['id'], item['created_at'][:19] + ' — ' + _(item['status']) + ' — ' + item['path'])
            if records:
                self.selector.set_active(0)
            else:
                self.view.get_buffer().set_text(_("No file changes recorded."))
                self.restore_button.set_sensitive(False)
            self.notice.set_text(completion_notice or _(
                "Only approved file writes are recorded. Command effects are not automatically reversible."))
        return False

    def _display(self, combo):
        item = self.records.get(combo.get_active_id())
        self.restore_button.set_sensitive(not self._busy and self._can_restore())
        if item:
            labels = (('path', 'Path'), ('status', 'Status'), ('created_at', 'Created'),
                      ('session_id', 'Conversation'), ('before', 'Original SHA256'), ('after', 'Written SHA256'),
                      ('backup', 'Original backup'), ('restored_at', 'Recovered'),
                      ('recovery_backup', 'Recovery backup'), ('archive_id', 'Archive'))
            self.view.get_buffer().set_text('\n'.join(_(label) + ': ' + (str(item.get(key) or '—') if key != 'status' else _(item[key]))
                                                     for key, label in labels))

    def _restore(self, button):
        if self._closed or self._busy:
            return
        identifier = self.selector.get_active_id()
        if not identifier:
            return
        generation = self._generation
        self._set_busy(True, _("Preparing recovery review…"))
        self._worker(lambda: self._prepare_recovery(identifier),
                     lambda prepared, error: self._reviewed(generation, identifier, prepared, error),
                     supervise=True)

    def _prepare_recovery(self, identifier):
        item = self.journal.preview_restore(identifier, self._allowed)
        if item.get('preview_truncated'):
            raise ValueError(_("Backup exceeds the recovery preview limit (1 MiB). Review it manually."))
        if item['before'] is None:
            preview = _("Remove this newly created file and retain a recovery copy.")
            if 'preview_content' in item:
                preview += '\n\n' + item['preview_content']
        else:
            if 'preview_content' in item:
                # The preview helper verified the original backup/current hash.
                preview = _('Original backup') + '\n\n' + item['preview_content']
            else:
                with open(item['backup'], 'rb') as stream:
                    data = stream.read(MAX_DIFF_BYTES + 1)
                if len(data) > MAX_DIFF_BYTES:
                    raise ValueError(_("Backup exceeds the recovery preview limit (1 MiB). Review it manually."))
                preview = preview_diff(item['path'], data.decode('utf-8', errors='replace'))
        return item, preview

    def _current(self):
        return (not self._closed and not getattr(self.owner, '_audit_closed', False)
                and not getattr(self.owner, '_history_recovering', False)
                and self.owner.history_store.active_session_id == self.session_id)

    def _reviewed(self, generation, identifier, prepared, error):
        if generation != self._generation:
            return False
        if not self._current():
            self._set_busy(False)
            return False
        if error is not None:
            self._set_busy(False, error)
            return False
        item, preview = prepared
        response = self._show_recovery_confirmation(item, preview)
        if (response != Gtk.ResponseType.OK or generation != self._generation
                or not self._current()):
            if generation == self._generation:
                self._set_busy(False)
            return False
        self._set_busy(True, _("Recovering approved file…"))
        self.owner._file_recovery_active = getattr(self.owner, '_file_recovery_active', 0) + 1
        self._worker(lambda: self.journal.restore(identifier, self._allowed, item['parent_identity']),
                     lambda restored, error: self._restored(item, restored, error), approved=True)
        return False

    def _show_recovery_confirmation(self, item, preview):
        confirmation = Gtk.Dialog(title=_("Confirm file recovery"), transient_for=self, modal=True)
        confirmation.set_destroy_with_parent(True)
        confirmation.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        confirmation.add_button(_("Restore file"), Gtk.ResponseType.OK)
        confirmation.set_default_response(Gtk.ResponseType.CANCEL)
        content = confirmation.get_content_area()
        content.pack_start(Gtk.Label(label=item['path'], selectable=True, wrap=True), False, False, 6)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_width(620)
        scroll.set_min_content_height(300)
        view = Gtk.TextView(editable=False, cursor_visible=False)
        view.set_monospace(True)
        view.get_buffer().set_text(preview or '')
        scroll.add(view)
        content.pack_start(scroll, True, True, 6)
        confirmation.show_all()
        response = confirmation.run()
        confirmation.destroy()
        return response

    def _restored(self, item, restored, error):
        self.owner._file_recovery_active = max(0, getattr(self.owner, '_file_recovery_active', 0) - 1)
        message = (_("Recovery failed: {detail}").format(detail=error) if error is not None
                   else _("File recovered. Recovery backup: {path}").format(path=restored['recovery_backup']))
        # Approved recovery belongs to the record's originating conversation,
        # including when this dialog was closed during authentication.
        record_result = getattr(self.owner, '_record_file_action_result', None)
        if record_result is not None:
            record_result(item.get('session_id') or self.session_id,
                          'error' if error is not None else 'restored', message)
        if not self._closed:
            self._refresh(completion_notice=message)
        return False


def show_file_changes(window):
    dialog = ChangeDialog(window)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
