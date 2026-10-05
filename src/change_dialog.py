"""Review file changes and explicitly confirm a guarded recovery."""

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

from .file_actions import MAX_DIFF_BYTES, preview_diff
from .i18n import _


class ChangeDialog(Gtk.Dialog):
    def __init__(self, window):
        super().__init__(title=_("File changes"), transient_for=window, modal=True)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self.set_default_size(760, 540)
        self.owner = window
        self.journal = window.change_journal
        self.session_id = window.history_store.active_session_id
        self.records = {}
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

    def _refresh(self, widget=None):
        try:
            records = self.journal.list_changes(
                None if self.all_sessions.get_active() else self.session_id,
                include_archived=self.archived.get_active())
            self.records = {item['id']: item for item in records}
            self.selector.remove_all()
            for item in records:
                self.selector.append(item['id'], item['created_at'][:19] + ' — ' + _(item['status']) + ' — ' + item['path'])
            if records:
                self.selector.set_active(0)
            else:
                self.view.get_buffer().set_text(_("No file changes recorded."))
                self.restore_button.set_sensitive(False)
        except (OSError, ValueError) as error:
            self.notice.set_text(str(error))
            self.restore_button.set_sensitive(False)

    def _display(self, combo):
        item = self.records.get(combo.get_active_id())
        self.restore_button.set_sensitive(bool(item and item['status'] in ('applied', 'pending', 'uncertain')))
        if item:
            labels = (('path', 'Path'), ('status', 'Status'), ('created_at', 'Created'),
                      ('session_id', 'Conversation'), ('before', 'Original SHA256'), ('after', 'Written SHA256'),
                      ('backup', 'Original backup'), ('restored_at', 'Recovered'),
                      ('recovery_backup', 'Recovery backup'), ('archive_id', 'Archive'))
            self.view.get_buffer().set_text('\n'.join(_(label) + ': ' + (str(item.get(key) or '—') if key != 'status' else _(item[key]))
                                                     for key, label in labels))

    def _restore(self, button):
        identifier = self.selector.get_active_id()
        try:
            item = self.journal.preview_restore(identifier, self._allowed)
            if item.get('preview_truncated'):
                raise ValueError(_("Backup exceeds the recovery preview limit (1 MiB). Review it manually."))
            if item['before'] is None:
                preview = _("Remove this newly created file and retain a recovery copy.")
                if 'preview_content' in item:
                    preview += '\n\n' + item['preview_content']
            else:
                if 'preview_content' in item:
                    # The preview helper verified the original backup and current hash.
                    preview = _('Original backup') + '\n\n' + item['preview_content']
                else:
                    with open(item['backup'], 'rb') as stream:
                        data = stream.read(MAX_DIFF_BYTES + 1)
                    if len(data) > MAX_DIFF_BYTES:
                        raise ValueError(_("Backup exceeds the recovery preview limit (1 MiB). Review it manually."))
                    preview = preview_diff(item['path'], data.decode('utf-8', errors='replace'))
            confirmation = Gtk.Dialog(title=_("Confirm file recovery"), transient_for=self, modal=True)
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
            if response != Gtk.ResponseType.OK:
                return
            restored = self.journal.restore(identifier, self._allowed, item['parent_identity'])
            self._refresh()
            self.notice.set_text(_("File recovered. Recovery backup: {path}").format(path=restored['recovery_backup']))
        except (OSError, ValueError, KeyError) as error:
            self.notice.set_text(str(error))


def show_file_changes(window):
    dialog = ChangeDialog(window)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
