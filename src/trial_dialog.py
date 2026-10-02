"""Optional local trial recording, independent from AI and action execution."""

import threading

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from .i18n import _
from .trial_recorder import make_recorder


RESULTS = ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'N/A')
TEST_TYPES = (
    ('unknown', 'Not specified'), ('vm', 'Virtual machine'),
    ('physical', 'Physical machine'), ('wsl', 'WSL smoke test'),
    ('fixture', 'Simulated fixture'),
)


class TrialDialog(Gtk.Dialog):
    """A non-modal panel: operators can exercise the main program concurrently."""

    def __init__(self, window):
        super().__init__(title=_("Trials / Debug"), transient_for=window, modal=False)
        self.owner = window
        self.recorder = getattr(window, 'trial_recorder', None)
        self.run_data = None
        self.closed = False
        self.busy = False
        self._rendering = False
        self._preview_ready = False
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self.set_default_size(800, 760)
        self.connect('destroy', self._destroyed)
        self.connect('response', self._response)
        self.connect('delete-event', lambda *args: self.busy)

        outer = self.get_content_area()
        outer.set_border_width(10)
        outer.set_spacing(8)
        introduction = Gtk.Label(
            label=_("Local collection only. Start a case, test in the main window, then record its result. Closing this panel keeps an open case."),
            xalign=0, wrap=True,
        )
        introduction.set_max_width_chars(70)
        outer.pack_start(introduction, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.content_scroll = scroll
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(scroll, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(box)

        selection = Gtk.Box(spacing=6)
        self.runs = Gtk.ComboBoxText()
        self.runs.set_hexpand(True)
        for renderer in self.runs.get_cells():
            renderer.set_property('max-width-chars', 28)
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
        selection.pack_start(self.runs, True, True, 0)
        self.select_button = self._button('Open selected trial', self._select, selection)
        self.refresh_button = self._button('Refresh', self._refresh, selection)
        box.pack_start(selection, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.title = self._entry(grid, 0, 'Trial title', 200)
        self.environment = self._entry(grid, 1, 'Environment ID', 120)
        self.build_ref = self._entry(grid, 2, 'Build revision', 120)
        self.test_type = Gtk.ComboBoxText()
        for key, text in TEST_TYPES:
            self.test_type.append(key, _(text))
        self.test_type.set_active_id('unknown')
        grid.attach(Gtk.Label(label=_("Test environment"), xalign=0), 0, 3, 1, 1)
        grid.attach(self.test_type, 1, 3, 1, 1)
        box.pack_start(grid, False, False, 0)
        campaign_buttons = Gtk.Box(spacing=6)
        self.start_button = self._button('Start new trial', self._start, campaign_buttons)
        self.finish_button = self._button('Finish trial', self._finish, campaign_buttons)
        box.pack_start(campaign_buttons, False, False, 0)
        self.state = Gtk.Label(xalign=0, wrap=True)
        self.state.set_max_width_chars(70)
        box.pack_start(self.state, False, False, 0)

        case_grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.case_id = self._entry(case_grid, 0, 'Case ID', 120)
        self.variant = self._entry(case_grid, 1, 'Case variant / attempt', 200)
        self.operation = self._entry(case_grid, 2, 'Operation ID (optional)', 120)
        box.pack_start(case_grid, False, False, 0)
        box.pack_start(self._wrapped_label("Explicit notes only; conversations are not copied automatically."),
                       False, False, 0)
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        notes_scroll = Gtk.ScrolledWindow()
        notes_scroll.set_min_content_height(70)
        notes_scroll.add(self.notes)
        box.pack_start(notes_scroll, False, False, 0)
        case_buttons = Gtk.Box(spacing=6)
        self.begin_button = self._button('Begin case', self._begin, case_buttons)
        self.result = Gtk.ComboBoxText()
        for result in RESULTS:
            self.result.append(result, result)
        self.result.set_active_id('PASS')
        case_buttons.pack_start(self.result, False, False, 0)
        self.end_button = self._button('Record result', self._end, case_buttons)
        box.pack_start(case_buttons, False, False, 0)
        self.collect_logs = Gtk.CheckButton(label=_("Include bounded app log excerpt (may include other conversations)"))
        self.collect_logs.get_child().set_max_width_chars(70)
        self.collect_logs.get_child().set_line_wrap(True)
        self.collect_logs.set_active(False)
        box.pack_start(self.collect_logs, False, False, 0)
        box.pack_start(self._wrapped_label("Log excerpts and automatic redaction are partial. They may contain sensitive data."),
                       False, False, 0)

        self.cases = Gtk.ListStore(str, str, str, str)
        case_tree = Gtk.TreeView(model=self.cases)
        self.case_tree = case_tree
        for index, text in enumerate(('Case ID', 'Case variant / attempt', 'Result', 'Conversation ID')):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            renderer.set_property('max-width-chars', (16, 20, 9, 24)[index])
            case_tree.append_column(Gtk.TreeViewColumn(_(text), renderer, text=index))
        case_scroll = Gtk.ScrolledWindow()
        case_scroll.set_min_content_height(85)
        case_scroll.add(case_tree)
        box.pack_start(case_scroll, False, False, 0)

        self.attachments = Gtk.ListStore(str, str, str)
        self.attachment_tree = Gtk.TreeView(model=self.attachments)
        for index, text in ((1, 'Attachment'), (2, 'Type')):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            renderer.set_property('max-width-chars', 40 if index == 1 else 18)
            self.attachment_tree.append_column(Gtk.TreeViewColumn(_(text), renderer, text=index))
        attachment_scroll = Gtk.ScrolledWindow()
        attachment_scroll.set_min_content_height(70)
        attachment_scroll.add(self.attachment_tree)
        box.pack_start(attachment_scroll, False, False, 0)
        attachment_buttons = Gtk.Box(spacing=6)
        self.attach_button = self._button('Add local evidence', self._attach, attachment_buttons)
        self.view_button = self._button('View attachment', self._view_attachment, attachment_buttons)
        self.remove_button = self._button('Exclude attachment', self._exclude, attachment_buttons)
        box.pack_start(attachment_buttons, False, False, 0)
        box.pack_start(self._wrapped_label("Choose only relevant text or PNG/JPEG images. Images are not redacted automatically and are never sent to AI by this panel."),
                       False, False, 0)

        self.preview_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        self.preview_view.set_monospace(True)
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_min_content_height(180)
        preview_scroll.add(self.preview_view)
        box.pack_start(preview_scroll, True, True, 0)
        self.reviewed = Gtk.CheckButton(label=_("I reviewed the exported content and all attachments"))
        self.reviewed.get_child().set_max_width_chars(70)
        self.reviewed.get_child().set_line_wrap(True)
        box.pack_start(self.reviewed, False, False, 0)
        export_buttons = Gtk.Box(spacing=6)
        self.preview_button = self._button('Refresh export preview', self._refresh, export_buttons)
        self.export_button = self._button('Export reviewed ZIP', self._export, export_buttons)
        box.pack_start(export_buttons, False, False, 0)
        self.notice = Gtk.Label(label=_("Opening local trials…"), xalign=0, wrap=True)
        self.notice.set_max_width_chars(70)
        outer.pack_start(self.notice, False, False, 0)

        self._controls = (
            self.runs, self.select_button, self.refresh_button, self.start_button,
            self.finish_button, self.begin_button, self.end_button, self.result,
            self.operation, self.collect_logs, self.attach_button, self.view_button,
            self.remove_button, self.reviewed, self.preview_button, self.export_button,
            self.title, self.environment, self.build_ref, self.test_type,
            self.case_id, self.variant, self.notes,
        )
        for entry in (self.title, self.environment, self.build_ref, self.case_id, self.variant, self.operation):
            entry.connect('changed', self._invalidate_review)
        self.notes.get_buffer().connect('changed', self._invalidate_review)
        for combo in (self.test_type, self.result):
            combo.connect('changed', self._invalidate_review)
        self.collect_logs.connect('toggled', self._invalidate_review)
        self.reviewed.connect('toggled', lambda *args: self._update_controls())
        self.attachment_tree.get_selection().connect('changed', lambda *args: self._update_controls())
        self._launch(_("Opening local trials…"), self._initialize)

    @staticmethod
    def _wrapped_label(text):
        label = Gtk.Label(label=_(text), xalign=0, wrap=True)
        label.set_max_width_chars(70)
        return label

    @staticmethod
    def _button(text, callback, box):
        button = Gtk.Button(label=_(text))
        button.connect('clicked', callback)
        box.pack_start(button, False, False, 0)
        return button

    def _entry(self, grid, row, text, maximum):
        entry = Gtk.Entry()
        entry.set_max_length(maximum)
        entry.set_hexpand(True)
        grid.attach(Gtk.Label(label=_(text), xalign=0), 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
        return entry

    def _initialize(self):
        if self.recorder is None:
            self.recorder = make_recorder(self.owner.history_store, self.owner.config, self.owner.offline.distro)

    def _open_case(self):
        return next((case for case in (self.run_data or {}).get('cases', []) if case.get('result') is None), None)

    def _invalidate_review(self, *args):
        if not self._rendering:
            self.reviewed.set_active(False)

    def _update_controls(self):
        if self.closed:
            return
        available = self.recorder is not None and not self.busy
        run = self.run_data
        active = bool(run and run.get('finished_at') is None)
        open_case = self._open_case()
        for widget in self._controls:
            widget.set_sensitive(available)
        self.get_widget_for_response(Gtk.ResponseType.CLOSE).set_sensitive(not self.busy)
        self.start_button.set_sensitive(available and not active)
        self.finish_button.set_sensitive(available and active and not open_case)
        self.begin_button.set_sensitive(available and active and not open_case)
        self.end_button.set_sensitive(available and active and bool(open_case))
        self.case_id.set_sensitive(available and active and not open_case)
        self.variant.set_sensitive(available and active and not open_case)
        self.notes.set_sensitive(available and active)
        for widget in (self.result, self.operation, self.collect_logs):
            widget.set_sensitive(available and active and bool(open_case))
        for widget in (self.title, self.environment, self.build_ref, self.test_type):
            widget.set_sensitive(available and not active)
        self.attach_button.set_sensitive(available and bool(run))
        self.view_button.set_sensitive(available and self._attachment_id() is not None)
        self.remove_button.set_sensitive(available and self._attachment_id() is not None)
        self.preview_button.set_sensitive(available and bool(run))
        self.reviewed.set_sensitive(available and bool(run) and self._preview_ready)
        self.export_button.set_sensitive(available and bool(run) and not open_case
                                         and self._preview_ready and self.reviewed.get_active())

    def _launch(self, notice, operation, after=None):
        if self.busy or self.closed:
            return
        self.busy = True
        self._preview_ready = False
        self._invalidate_review()
        self.notice.set_text(notice)
        self._update_controls()

        def worker():
            value, error, run, runs, preview = None, '', None, [], ''
            try:
                value = operation()
            except Exception as exc:
                error = str(exc)
            try:
                run = self.recorder.current() if self.recorder is not None else None
                runs = self.recorder.list_runs() if self.recorder is not None else []
                open_case = run and any(case.get('result') is None for case in run.get('cases', []))
                # Open cases are deliberately not exportable. Keep their state
                # visible so the operator can record the result and recover.
                if run and not open_case:
                    preview = self.recorder.preview()
            except Exception as exc:
                error = error or str(exc)
            GLib.idle_add(self._completed, run, runs, preview, error, value, after)

        threading.Thread(target=worker, daemon=False).start()

    def _completed(self, run, runs, preview, error, value, after):
        if self.closed:
            return False
        self.owner.trial_recorder = self.recorder
        self.run_data = run
        self._rendering = True
        self.runs.remove_all()
        for item in runs:
            self.runs.append(item['id'], '{} — {}'.format(item.get('title', ''), item['id']))
        if run:
            self.runs.set_active_id(run['id'])
        self.cases.clear()
        for case in (run or {}).get('cases', []):
            self.cases.append((case.get('case_id', ''), case.get('variant', ''),
                               case.get('result') or _("Open case"), case.get('session_id', '')))
        self.attachments.clear()
        for attachment in (run or {}).get('attachments', []):
            self.attachments.append((attachment['id'], attachment.get('name', ''), attachment.get('media_type', '')))
        self.preview_view.get_buffer().set_text('')
        open_case = self._open_case()
        if open_case:
            self.case_id.set_text(open_case.get('case_id', ''))
            self.variant.set_text(open_case.get('variant', ''))
            self.state.set_text(_("Case {case} is tied to conversation {session}; switching conversations does not change it.").format(
                case=open_case.get('case_id', ''), session=open_case.get('session_id', '')))
        elif run:
            self.state.set_text(_("Trial {id} — {state}").format(
                id=run['id'], state=_("Finished") if run.get('finished_at') is not None else _("Ready to begin a case")))
        else:
            self.state.set_text(_("No trial selected. Start a new trial or open an existing one."))
        trial_button = getattr(self.owner, 'trials_button', None)
        if trial_button is not None:
            style = trial_button.get_style_context()
            if open_case:
                style.add_class('suggested-action')
                trial_button.set_tooltip_text(_("Trials / Debug — open case {case}").format(
                    case=open_case.get('case_id', '')))
            else:
                style.remove_class('suggested-action')
                trial_button.set_tooltip_text(_("Trials / Debug"))
        self._rendering = False
        message = (_("Record the open case result to prepare an export preview.") if open_case
                   else _("Review all exported texts below and view every image before confirming export. Nothing is uploaded."))
        ready = bool(run and preview and not error)
        if len(preview) > 16384:
            self.notice.set_text(_("Rendering export preview…"))
            GLib.idle_add(self._preview_chunk, preview, [0], error, value, after, message, ready)
        else:
            self.preview_view.get_buffer().set_text(preview)
            self._complete_render(error, value, after, message, ready)
        return False

    def _preview_chunk(self, preview, offset, error, value, after, message, ready):
        if self.closed:
            return False
        buffer = self.preview_view.get_buffer()
        end = min(offset[0] + 16384, len(preview))
        buffer.insert(buffer.get_end_iter(), preview[offset[0]:end])
        offset[0] = end
        if end < len(preview):
            return True
        self._complete_render(error, value, after, message, ready)
        return False

    def _complete_render(self, error, value, after, message, ready):
        self.busy = False
        self._preview_ready = ready
        self.notice.set_text(error or message)
        self._update_controls()
        if after is not None and not error:
            after(value)

    def _refresh(self, button=None):
        self._launch(_("Refreshing local preview…"), lambda: None)

    def _select(self, button=None):
        run_id = self.runs.get_active_id()
        if run_id:
            self._launch(_("Opening selected trial…"), lambda: self.recorder.select(run_id))

    def _start(self, button=None):
        title, environment, build_ref = self.title.get_text(), self.environment.get_text(), self.build_ref.get_text()
        config = self.owner.config
        mode = config.get_assistance_mode()
        provider = config.get('api.default_provider', '') if mode in ('auto', 'remote') else ('local_llm' if mode == 'local' else '')
        model = config.get('api.providers.{}.model'.format(provider), '') if provider else ''
        test_type = self.test_type.get_active_id() or 'unknown'
        self._launch(_("Starting local trial…"), lambda: self.recorder.start(
            title, environment, build_ref=build_ref, mode=mode, provider=provider,
            model=model, test_type=test_type))

    def _notes(self):
        buffer = self.notes.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _begin(self, button=None):
        case_id, variant, notes = self.case_id.get_text(), self.variant.get_text(), self._notes()
        session_id = self.owner.history_store.active_session_id
        self._launch(_("Beginning case…"), lambda: self.recorder.begin_case(
            case_id, session_id, variant=variant, interface='gui', notes=notes), self._clear_notes)

    def _clear_notes(self, value=None):
        self.notes.get_buffer().set_text('')
        self.operation.set_text('')
        self.collect_logs.set_active(False)

    def _end(self, button=None):
        result, notes = self.result.get_active_id(), self._notes()
        operation_id = self.operation.get_text().strip() or None
        collect_logs = self.collect_logs.get_active()
        self._launch(_("Recording result and local evidence…"), lambda: self.recorder.end_case(
            result, notes=notes, operation_id=operation_id, collect_logs=collect_logs), self._clear_notes)

    def _finish(self, button=None):
        self._launch(_("Finishing trial…"), self.recorder.finish)

    def _attachment_id(self):
        model, iterator = self.attachment_tree.get_selection().get_selected()
        return model[iterator][0] if iterator is not None else None

    def _attach(self, button=None):
        chooser = Gtk.FileChooserDialog(title=_("Add local evidence"), transient_for=self,
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        chooser.add_button(_("Open"), Gtk.ResponseType.OK)
        chooser.set_default_response(Gtk.ResponseType.CANCEL)
        response = chooser.run()
        path = chooser.get_filename()
        chooser.destroy()
        if response == Gtk.ResponseType.OK and path:
            self._launch(_("Adding private local evidence…"), lambda: self.recorder.attach(path))

    def _exclude(self, button=None):
        attachment_id = self._attachment_id()
        if attachment_id:
            self._launch(_("Excluding attachment from export…"), lambda: self.recorder.exclude_attachment(attachment_id))

    def _view_attachment(self, button=None):
        attachment_id = self._attachment_id()
        if attachment_id:
            self._launch(_("Opening attachment preview…"), lambda: self._load_attachment(attachment_id),
                         self._show_attachment)

    def _load_attachment(self, attachment_id):
        value = self.recorder.attachment_preview(attachment_id)
        if value['kind'] == 'image':
            # Reading and decoding the file also stay off the GTK main loop.
            value['pixbuf'] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(value['path']), 2048, 2048, True)
        return value

    def _show_attachment(self, value):
        dialog = Gtk.Dialog(title=_("Attachment preview"), transient_for=self, modal=False)
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        dialog.set_default_size(700, 500)
        dialog.set_destroy_with_parent(True)
        dialog.connect('response', lambda widget, response: widget.destroy())
        if value['kind'] == 'image':
            dialog.get_content_area().pack_start(self._wrapped_label(
                "Scaled image preview. Review the original pixels and image metadata before export; neither is redacted automatically."),
                False, False, 8)
        scroll = Gtk.ScrolledWindow()
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        if value['kind'] == 'image':
            scroll.add(Gtk.Image.new_from_pixbuf(value['pixbuf']))
        else:
            text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            text.set_monospace(True)
            text.get_buffer().set_text(value['text'])
            scroll.add(text)
        dialog.show_all()

    def _export(self, button=None):
        if not self.reviewed.get_active() or not self._preview_ready or self._open_case():
            return
        chooser = Gtk.FileChooserDialog(title=_("Export reviewed ZIP"), transient_for=self,
                                        action=Gtk.FileChooserAction.SAVE)
        chooser.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        chooser.add_button(_("Save"), Gtk.ResponseType.OK)
        chooser.set_default_response(Gtk.ResponseType.CANCEL)
        chooser.set_current_name('trial-{}.zip'.format(self.run_data['id']))
        response = chooser.run()
        path = chooser.get_filename()
        chooser.destroy()
        if response == Gtk.ResponseType.OK and path:
            self._launch(_("Exporting reviewed local package…"),
                         lambda: self.recorder.export(path, reviewed=True), self._exported)

    def _exported(self, path):
        self.notice.set_text(_("Trial exported locally: {path}").format(path=path))

    def _response(self, widget, response):
        if not self.busy:
            self.destroy()

    def _destroyed(self, widget):
        self.closed = True
        if getattr(self.owner, 'trial_dialog', None) is self:
            self.owner.trial_dialog = None


def show_trials(window):
    dialog = getattr(window, 'trial_dialog', None)
    if dialog is not None and not dialog.closed:
        dialog.present()
        return dialog
    dialog = TrialDialog(window)
    window.trial_dialog = dialog
    dialog.show_all()
    return dialog
