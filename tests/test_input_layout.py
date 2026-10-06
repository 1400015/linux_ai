"""The narrow desktop window leaves a usable, initially focused message field."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class TestInputLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, GLib
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src.main_window import MainWindow
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.Gtk, cls.GLib, cls.MainWindow = Gtk, GLib, MainWindow

    def setUp(self):
        from src.ai_client import AIClient
        from src.config_manager import ConfigManager
        from src.history_store import HistoryStore
        from src.system_utils import SystemUtils

        self.directory = tempfile.TemporaryDirectory()
        self.home = patch('pathlib.Path.home', return_value=Path(self.directory.name))
        self.home.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.config.set_assistance_mode('offline')
        self.config.set('app.width', 400)
        self.config.set('app.height', 650)
        self.client = AIClient(self.config)
        self.store = HistoryStore(Path(self.directory.name) / 'history.json')
        self.window = self.MainWindow(
            SimpleNamespace(tray_icon=None), self.config, self.client,
            SystemUtils(self.config), history_store=self.store,
        )

    def tearDown(self):
        self.window.close_history_writer()
        self.Gtk.StyleContext.remove_provider_for_screen(self.window.get_screen(), self.window._style_provider)
        self.window.destroy()
        self.client.flush_usage()
        self.config.flush()
        self.home.stop()
        self.directory.cleanup()

    def settle_layout(self):
        # X11 resize/map notifications arrive asynchronously; let GTK allocate
        # its actual widgets rather than testing only requested dimensions.
        loop = self.GLib.MainLoop()
        self.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()

    def assert_full_width(self, width):
        # Set the size before mapping: this also works on the headless X
        # server used by CI, where no window manager handles resize requests.
        self.window.set_default_size(width, 650)
        self.window.show_all()
        self.settle_layout()
        field = self.window.input_entry
        area = field.get_parent()
        self.assertAlmostEqual(self.window.get_allocated_width(), width, delta=2)
        self.assertGreaterEqual(field.get_allocated_width(), width - 70)
        # Button allocations must be below the typing field, not
        # consume the same horizontal row at narrow widths.
        field_y = field.translate_coordinates(self.window, 0, 0)[1]
        cancel_y = self.window.cancel_btn.translate_coordinates(self.window, 0, 0)[1]
        self.assertGreaterEqual(cancel_y, field_y + field.get_allocated_height())
        padding = area.get_style_context().get_padding(self.Gtk.StateFlags.NORMAL)
        self.assertEqual(
            field.get_allocated_width(),
            area.get_allocated_width() - padding.left - padding.right,
        )

    def test_400px_window_leaves_full_width_message_field(self):
        self.assert_full_width(400)

    def test_500px_window_leaves_full_width_message_field(self):
        self.assert_full_width(500)

    def test_first_map_focuses_editable_message_field(self):
        self.window.show_all()
        self.settle_layout()
        self.assertIs(self.window.get_focus(), self.window.input_entry)
        self.assertTrue(self.window.input_entry.get_editable())
        self.assertTrue(self.window.input_entry.get_sensitive())
        self.window.input_entry.insert_text('Olá Ubuntu', 0)
        self.assertEqual(self.window.input_entry.get_text(), 'Olá Ubuntu')

    def test_remapping_and_modal_dialog_do_not_reset_user_focus(self):
        self.window.show_all()
        self.settle_layout()
        self.window.session_combo.grab_focus()
        previous_focus = self.window.get_focus()
        self.assertIsNot(previous_focus, self.window.input_entry)
        self.window.hide()
        self.window.show_all()
        self.settle_layout()
        self.assertIs(self.window.get_focus(), previous_focus)

        dialog = self.Gtk.Dialog(transient_for=self.window, modal=True)
        dialog_entry = self.Gtk.Entry()
        dialog.get_content_area().add(dialog_entry)
        try:
            dialog.show_all()
            dialog_entry.grab_focus()
            self.settle_layout()
            self.assertIs(dialog.get_focus(), dialog_entry)
            self.assertIs(self.window.get_focus(), previous_focus)
        finally:
            dialog.destroy()
