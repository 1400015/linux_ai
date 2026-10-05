"""Review and manage explicitly imported local document snapshots."""

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

from .document_store import DocumentError, SUPPORTED_SUFFIXES
from .i18n import _


class DocumentDialog(Gtk.Dialog):
    def __init__(self, parent, store, on_change=None):
        super().__init__(title=_("Local documents"), transient_for=parent, modal=True)
        self.store = store
        self.on_change = on_change
        self.records = {}
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self.set_default_size(760, 540)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        box.pack_start(Gtk.Label(label=_("Only files you choose are indexed. The index contains private snapshots; source changes are read only when you reindex."),
                                xalign=0, wrap=True), False, False, 0)
        self.selector = Gtk.ComboBoxText()
        for cell in self.selector.get_cells():
            cell.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            cell.set_property('max-width-chars', 80)
        self.selector.connect('changed', self._display)
        box.pack_start(self.selector, False, False, 0)
        buttons = Gtk.Box(spacing=6)
        self.add_document_button = Gtk.Button(label=_("Add documents"))
        self.add_document_button.connect('clicked', self._add)
        self.reindex_button = Gtk.Button(label=_("Reindex selected document"))
        self.reindex_button.connect('clicked', self._reindex)
        self.remove_button = Gtk.Button(label=_("Remove selected document"))
        self.remove_button.connect('clicked', self._remove)
        self.clear_button = Gtk.Button(label=_("Clear document index"))
        self.clear_button.connect('clicked', self._clear)
        for button in (self.add_document_button, self.reindex_button, self.remove_button, self.clear_button):
            buttons.pack_start(button, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.details = Gtk.Label(xalign=0, selectable=True, wrap=True)
        box.pack_start(self.details, False, False, 0)
        self.preview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        self.preview.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.preview)
        box.pack_start(scroll, True, True, 0)
        self.notice = Gtk.Label(label=_("Search works locally. Sending excerpts to an AI provider requires enabling documents for the conversation and reviewing the excerpts before sending."),
                                xalign=0, wrap=True)
        box.pack_start(self.notice, False, False, 0)
        self._refresh()

    def _refresh(self, selected=None):
        try:
            records = self.store.list_documents()
            self.records = {item['id']: item for item in records}
            self.selector.remove_all()
            for item in records:
                self.selector.append(item['id'], item['name'])
            if records:
                self.selector.set_active_id(selected or records[0]['id'])
                if self.selector.get_active_id() is None:
                    self.selector.set_active(0)
            else:
                self.details.set_text(_("No documents indexed."))
                self.preview.get_buffer().set_text('')
            self._display(self.selector)
            self.clear_button.set_sensitive(bool(records))
        except (DocumentError, OSError) as error:
            self.notice.set_text(str(error))
            self.remove_button.set_sensitive(False)
            self.reindex_button.set_sensitive(False)
            self.clear_button.set_sensitive(False)

    def _display(self, selector):
        item = self.records.get(selector.get_active_id())
        self.remove_button.set_sensitive(bool(item))
        self.reindex_button.set_sensitive(bool(item))
        if item:
            self.details.set_text('{}\n{}: {} — {}: {} — SHA256: {}\n{}: {}'.format(
                item['path'], _('Document ID'), item['id'], _('Lines'), item['lines'], item['sha256'],
                _('Snapshot imported'), item['imported_at']))
            try:
                self.preview.get_buffer().set_text(self.store.preview(item['id']))
            except (DocumentError, OSError) as error:
                self.notice.set_text(str(error))

    def _changed(self, selected=None):
        self._refresh(selected)
        if callable(self.on_change):
            self.on_change()

    def _add(self, button):
        chooser = Gtk.FileChooserDialog(title=_("Choose local documents"), transient_for=self,
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Add documents"), Gtk.ResponseType.OK)
        chooser.set_local_only(True)
        chooser.set_select_multiple(True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name(_("UTF-8 text documents"))
        for suffix in sorted(SUPPORTED_SUFFIXES):
            file_filter.add_pattern('*' + suffix)
        chooser.add_filter(file_filter)
        response = chooser.run()
        paths = chooser.get_filenames() if response == Gtk.ResponseType.OK else []
        chooser.destroy()
        if paths:
            try:
                imported = self.store.add(paths)
                self.notice.set_text(_("Document snapshots imported. Sources will not refresh automatically."))
                self._changed(imported[-1]['id'] if imported else None)
            except (DocumentError, OSError) as error:
                self.notice.set_text(str(error))

    def _reindex(self, button):
        identifier = self.selector.get_active_id()
        if identifier:
            try:
                self.store.reindex([identifier])
                self.notice.set_text(_("Selected document snapshot refreshed."))
                self._changed(identifier)
            except (DocumentError, OSError) as error:
                self.notice.set_text(str(error))

    def _remove(self, button):
        identifier = self.selector.get_active_id()
        if identifier:
            try:
                self.store.remove(identifier)
                self.notice.set_text(_("Document removed from the local index. The source file was preserved."))
                self._changed()
            except (DocumentError, OSError) as error:
                self.notice.set_text(str(error))

    def _clear(self, button):
        confirmation = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                         buttons=Gtk.ButtonsType.OK_CANCEL,
                                         text=_("Delete all document snapshots from the local index?"))
        confirmation.format_secondary_text(_("The original files will be preserved."))
        confirmation.set_default_response(Gtk.ResponseType.CANCEL)
        response = confirmation.run()
        confirmation.destroy()
        if response == Gtk.ResponseType.OK:
            try:
                self.store.clear()
                self.notice.set_text(_("Document index cleared. Original files were preserved."))
                self._changed()
            except (DocumentError, OSError) as error:
                self.notice.set_text(str(error))
