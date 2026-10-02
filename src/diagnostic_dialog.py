"""Explicit local collection, report preview and private export in GTK."""

import threading
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from .conversation_io import write_conversation
from .diagnostics import PROBES, build_report, export_report
from .i18n import _, get_language


class DiagnosticDialog(Gtk.Dialog):
    def __init__(self, window):
        super().__init__(title=_("Diagnostic report"), transient_for=window, modal=True)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self.set_default_size(760, 650)
        self.owner = window
        # A report remains attached to the conversation from which it was opened.
        self.session_id = window.history_store.active_session_id
        self.distro = window.offline.distro
        self.lang = get_language()
        self.report = None
        self.closed = False
        self.cancel_event = threading.Event()
        self.connect('destroy', self._destroyed)
        box = self.get_content_area()
        box.set_border_width(10)
        box.set_spacing(8)
        self.symptom = Gtk.Entry()
        self.symptom.set_max_length(2000)
        self.symptom.set_placeholder_text(_("Describe the symptom"))
        box.pack_start(self.symptom, False, False, 0)
        self.pasted = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.pasted.set_monospace(True)
        self.pasted.get_buffer().set_text('')
        box.pack_start(Gtk.Label(label=_("Paste a log excerpt (up to 64 KiB)"), xalign=0), False, False, 0)
        input_scroll = Gtk.ScrolledWindow()
        input_scroll.set_min_content_height(110)
        input_scroll.add(self.pasted)
        box.pack_start(input_scroll, False, True, 0)
        box.pack_start(Gtk.Label(label=_("Select local read checks to collect. No checks are selected by default."),
                                xalign=0, wrap=True), False, False, 0)
        grid = Gtk.Grid(column_spacing=10, row_spacing=4)
        self.checks = {}
        for index, (key, argv) in enumerate(PROBES.items()):
            check = Gtk.CheckButton(label=' '.join(argv))
            self.checks[key] = check
            grid.attach(check, index % 3, index // 3, 1, 1)
        box.pack_start(grid, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.generate = Gtk.Button(label=_("Prepare report"))
        self.generate.connect('clicked', self._generate)
        actions.pack_start(self.generate, False, False, 0)
        self.format = Gtk.ComboBoxText()
        for value in ('markdown', 'json'):
            self.format.append(value, value)
        self.format.set_active_id('markdown')
        self.format.connect('changed', self._render)
        actions.pack_start(self.format, False, False, 0)
        self.export = Gtk.Button(label=_("Export report"))
        self.export.set_sensitive(False)
        self.export.connect('clicked', self._export)
        actions.pack_start(self.export, False, False, 0)
        box.pack_start(actions, False, False, 0)
        self.preview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        self.preview.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.preview)
        box.pack_start(scroll, True, True, 0)
        self.notice = Gtk.Label(label=_("Reports stay on this machine. Review redacted data before sharing; unknown secrets may remain."),
                                xalign=0, wrap=True)
        box.pack_start(self.notice, False, False, 0)

    def _destroyed(self, widget):
        self.closed = True
        self.cancel_event.set()

    def _generate(self, button):
        buffer = self.pasted.get_buffer()
        pasted = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        symptom = self.symptom.get_text()
        if len((symptom + pasted).encode('utf-8')) > 65536:
            self.notice.set_text(_("Diagnostic input exceeds 64 KiB"))
            return
        keys = tuple(key for key, check in self.checks.items() if check.get_active())
        self.report = None
        self.preview.get_buffer().set_text('')
        self.generate.set_sensitive(False)
        self.export.set_sensitive(False)
        self.notice.set_text(_("Preparing local report…"))

        def worker():
            try:
                report = build_report(symptom, pasted, self.distro, self.lang, keys,
                                      self.owner.system_utils, session_id=self.session_id,
                                      cancel_event=self.cancel_event)
                error = ''
            except Exception as exc:
                report, error = None, str(exc)
            GLib.idle_add(self._completed, report, error)
        threading.Thread(target=worker, daemon=True).start()

    def _completed(self, report, error):
        if self.closed:
            return False
        self.report = report
        self.generate.set_sensitive(True)
        self.export.set_sensitive(report is not None)
        self.notice.set_text(error or _("Review the preview before export. Automatic redaction is partial."))
        self._render()
        return False

    def _render(self, combo=None):
        if self.report is not None and not self.closed:
            self.preview.get_buffer().set_text(export_report(self.report, self.format.get_active_id()))

    def _export(self, button):
        if self.report is None:
            return
        chooser = Gtk.FileChooserDialog(title=_("Export report"), transient_for=self,
                                        action=Gtk.FileChooserAction.SAVE)
        chooser.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        chooser.add_button(_("Save"), Gtk.ResponseType.OK)
        chooser.set_default_response(Gtk.ResponseType.CANCEL)
        format = self.format.get_active_id()
        chooser.set_current_name('diagnostic.' + ('json' if format == 'json' else 'md'))
        response = chooser.run()
        filename = chooser.get_filename()
        chooser.destroy()
        if response == Gtk.ResponseType.OK and filename:
            try:
                write_conversation(filename, export_report(self.report, format))
                self.notice.set_text(_("Report exported: {path}").format(path=filename))
            except (OSError, ValueError) as error:
                self.notice.set_text(str(error))


def show_diagnostic_report(window):
    dialog = DiagnosticDialog(window)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
