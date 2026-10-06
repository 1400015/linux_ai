"""GTK controls operate on private snapshots, preserving chosen source files."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    from src.document_dialog import DocumentDialog
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False

from src.document_store import DocumentStore


@unittest.skipUnless(GTK_AVAILABLE, 'GTK is unavailable')
class TestDocumentDialog(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'manual.md'
        self.source.write_text('original network configuration')
        self.store = DocumentStore(self.root / 'index' / 'documents.sqlite3')
        self.addCleanup(self.store.close)
        self.window = Gtk.Window()
        self.addCleanup(self.window.destroy)
        self.changed = Mock()
        self.dialog = DocumentDialog(self.window, self.store, self.changed)
        self.addCleanup(self.dialog.destroy)

    def test_chooser_adds_only_selected_file_and_shows_snapshot_preview(self):
        chooser = Mock()
        chooser.run.return_value = Gtk.ResponseType.OK
        chooser.get_filenames.return_value = [str(self.source)]
        with patch('src.document_dialog.Gtk.FileChooserDialog', return_value=chooser):
            self.dialog._add(None)
        chooser.set_local_only.assert_called_once_with(True)
        self.assertEqual(len(self.store.list_documents()), 1)
        text = self.dialog.preview.get_buffer().get_text(self.dialog.preview.get_buffer().get_start_iter(),
                                                         self.dialog.preview.get_buffer().get_end_iter(), True)
        self.assertEqual(text, self.source.read_text())
        self.changed.assert_called_once_with()
        self.assertTrue(self.dialog.remove_button.get_sensitive())
        self.assertTrue(self.dialog.reindex_button.get_sensitive())

    def test_cancelled_chooser_does_not_import_anything(self):
        chooser = Mock()
        chooser.run.return_value = Gtk.ResponseType.CANCEL
        chooser.get_filenames.return_value = [str(self.source)]
        with patch('src.document_dialog.Gtk.FileChooserDialog', return_value=chooser):
            self.dialog._add(None)
        self.assertEqual(self.store.list_documents(), [])
        self.changed.assert_not_called()

    def test_reindex_is_explicit_and_remove_preserves_source(self):
        record = self.store.add([self.source])[0]
        self.dialog._refresh(record['id'])
        self.source.write_text('updated printer configuration')
        self.assertTrue(self.store.search('original'))
        self.dialog._reindex(None)
        self.assertTrue(self.store.search('updated'))
        self.assertEqual(self.store.list_documents()[0]['id'], record['id'])
        self.dialog._remove(None)
        self.assertEqual(self.store.list_documents(), [])
        self.assertEqual(self.source.read_text(), 'updated printer configuration')
        self.assertEqual(self.changed.call_count, 2)

    def test_clear_requires_confirmation_and_keeps_sources(self):
        self.store.add([self.source])
        self.dialog._refresh()
        confirmation = Mock()
        confirmation.run.return_value = Gtk.ResponseType.CANCEL
        with patch('src.document_dialog.Gtk.MessageDialog', return_value=confirmation):
            self.dialog._clear(None)
        self.assertEqual(len(self.store.list_documents()), 1)
        confirmation.run.return_value = Gtk.ResponseType.OK
        with patch('src.document_dialog.Gtk.MessageDialog', return_value=confirmation):
            self.dialog._clear(None)
        self.assertEqual(self.store.list_documents(), [])
        self.assertTrue(self.source.exists())
        self.changed.assert_called_once_with()

    def test_main_window_opening_shows_document_controls_before_modal_run(self):
        from src.main_window import MainWindow

        self.window._get_document_store = lambda: self.store
        self.window._add_system_message = Mock()
        for populated in (False, True):
            with self.subTest(populated=populated):
                if populated:
                    self.store.add([self.source])
                observed, failures = [], []
                deadline = time.monotonic() + 5

                def inspect_and_close():
                    visible = [dialog for dialog in Gtk.Window.list_toplevels()
                               if isinstance(dialog, DocumentDialog) and dialog.get_visible()]
                    if not visible and time.monotonic() < deadline:
                        return True
                    try:
                        self.assertTrue(visible, 'Document dialog did not appear')
                        opened = visible[0]
                        for widget in (opened.selector, opened.add_document_button, opened.reindex_button,
                                       opened.remove_button, opened.clear_button, opened.details,
                                       opened.preview, opened.notice):
                            self.assertTrue(widget.get_visible(), type(widget).__name__ + ' is hidden')
                            self.assertTrue(widget.get_mapped(), type(widget).__name__ + ' is not mapped')
                        self.assertTrue(opened.add_document_button.get_sensitive())
                        self.assertEqual(opened.remove_button.get_sensitive(), populated)
                        self.assertEqual(opened.reindex_button.get_sensitive(), populated)
                        if populated:
                            buffer = opened.preview.get_buffer()
                            self.assertEqual(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
                                             self.source.read_text())
                        else:
                            self.assertTrue(opened.details.get_text())
                        observed.append(opened)
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        for dialog in visible:
                            dialog.response(Gtk.ResponseType.CLOSE)
                    return False

                timer = GLib.timeout_add(20, inspect_and_close)
                try:
                    MainWindow.on_documents_clicked(self.window)
                finally:
                    context = GLib.MainContext.default()
                    if context.find_source_by_id(timer) is not None:
                        GLib.source_remove(timer)
                if failures:
                    raise failures[0]
                self.assertEqual(len(observed), 1)
                self.window._add_system_message.assert_not_called()
