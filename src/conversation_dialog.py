"""Conversation management UI, kept separate from the chat window."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .conversation_io import read_conversation, write_conversation
from .i18n import _


def show_conversations(window):
    store = window.history_store
    dialog = Gtk.Dialog(title=_("Conversations"), transient_for=window,
                        buttons=(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE))
    dialog.set_default_size(680, 500)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)
    selector = Gtk.ComboBoxText()
    box.pack_start(selector, False, False, 0)
    title = Gtk.Entry()
    title.set_placeholder_text(_("Conversation name"))
    box.pack_start(title, False, False, 0)
    search = Gtk.SearchEntry()
    search.set_max_length(500)
    search.set_placeholder_text(_("Search all conversations"))
    box.pack_start(search, False, False, 0)
    grid = Gtk.Grid(column_spacing=6, row_spacing=6)
    box.pack_start(grid, False, False, 0)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
    scroll.add(view)
    box.pack_start(scroll, True, True, 0)
    notice = Gtk.Label(xalign=0, wrap=True)
    box.pack_start(notice, False, False, 0)
    metadata = {}

    def refresh(selected=None):
        sessions = store.list_sessions(include_archived=True)
        metadata.clear()
        selector.remove_all()
        for session in sessions:
            metadata[session['id']] = session
            suffix = " ({})".format(_("Archived")) if session['archived'] else ""
            selector.append(session['id'], session['title'] + suffix)
        selector.set_active_id(selected or store.active_session_id)
        window._refresh_session_controls()

    def display(combo):
        identifier = combo.get_active_id()
        if not identifier or identifier not in metadata:
            return
        title.set_text(metadata[identifier]['title'])
        view.get_buffer().set_text(store.export_session(identifier, 'markdown'))

    def perform(callback):
        def clicked(button):
            try:
                callback()
                notice.set_text("")
            except (OSError, ValueError, KeyError) as error:
                notice.set_text(str(error))
        return clicked

    def create():
        session = store.create_session(title.get_text().strip() or _("New conversation"))
        window._switch_session(session['id'])
        refresh()

    def rename():
        store.rename_session(selector.get_active_id(), title.get_text())
        refresh(selector.get_active_id())

    def activate():
        window._switch_session(selector.get_active_id())
        refresh()

    def archive():
        identifier = selector.get_active_id()
        # Invalidate any pending response before mutating the active session.
        window._cancel_for_session_change()
        store.archive_session(identifier, not metadata[identifier]['archived'])
        window._switch_session(store.active_session_id)
        refresh()

    def delete():
        identifier = selector.get_active_id()
        confirmation = Gtk.MessageDialog(transient_for=dialog, modal=True,
                                         message_type=Gtk.MessageType.QUESTION,
                                         buttons=Gtk.ButtonsType.OK_CANCEL,
                                         text=_("Delete this conversation?"))
        response = confirmation.run()
        confirmation.destroy()
        if response == Gtk.ResponseType.OK:
            window._cancel_for_session_change()
            store.delete_session(identifier)
            window._switch_session(store.active_session_id)
            refresh()

    def export(format):
        chooser = Gtk.FileChooserDialog(title=_("Export conversation"), transient_for=dialog,
                                        action=Gtk.FileChooserAction.SAVE,
                                        buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                                 Gtk.STOCK_SAVE, Gtk.ResponseType.OK))
        chooser.set_current_name('conversation.' + ('json' if format == 'json' else 'md'))
        try:
            if chooser.run() == Gtk.ResponseType.OK:
                write_conversation(chooser.get_filename(), store.export_session(selector.get_active_id(), format))
        finally:
            chooser.destroy()

    def imported():
        chooser = Gtk.FileChooserDialog(title=_("Import JSON conversation"), transient_for=dialog,
                                        action=Gtk.FileChooserAction.OPEN,
                                        buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                                 Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        try:
            if chooser.run() == Gtk.ResponseType.OK:
                session = store.import_session(read_conversation(chooser.get_filename()), select=False)
                refresh(session['id'])
        finally:
            chooser.destroy()

    actions = [
        (_("New conversation"), create), (_("Resume"), activate), (_("Rename"), rename),
        (_("Archive / restore"), archive), (_("Delete"), delete),
        (_("Export Markdown"), lambda: export('markdown')),
        (_("Export JSON"), lambda: export('json')), (_("Import JSON"), imported),
    ]
    for index, (label, callback) in enumerate(actions):
        button = Gtk.Button(label=label)
        button.connect('clicked', perform(callback))
        grid.attach(button, index % 4, index // 4, 1, 1)

    def search_changed(entry):
        query = entry.get_text().strip()
        if not query:
            display(selector)
            return
        matches = store.search(query, include_archived=True)
        text = []
        for match in matches[:50]:
            identifier = match.get('session_id', '')
            name = match.get('title') or metadata.get(identifier, {}).get('title', identifier)
            content = match.get('preview', '')
            text.append('{} [{}]\n{}'.format(name, identifier, content))
        view.get_buffer().set_text('\n\n'.join(text) or _("No matches"))

    selector.connect('changed', display)
    search.connect('search-changed', search_changed)
    refresh()
    dialog.show_all()
    dialog.run()
    dialog.destroy()
