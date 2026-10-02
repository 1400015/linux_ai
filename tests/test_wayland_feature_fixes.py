"""Regression checks for keyboard input and configured feature visibility."""

from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class TestDockKeyboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src import dock
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.dock = dock

    def shell(self, protocol=4):
        return SimpleNamespace(
            init_for_window=Mock(), set_layer=Mock(), set_anchor=Mock(),
            is_layer_window=Mock(return_value=True),
            set_keyboard_mode=Mock(), get_protocol_version=Mock(return_value=protocol),
            KeyboardMode=SimpleNamespace(ON_DEMAND='on-demand', EXCLUSIVE='exclusive'),
            Layer=SimpleNamespace(TOP='top'),
            Edge=SimpleNamespace(LEFT='left', RIGHT='right', TOP='top', BOTTOM='bottom'),
        )

    def apply(self, shell, expected='layer-shell'):
        window = Mock()
        window.get_size.return_value = (400, 500)
        window.get_screen.return_value.get_width.return_value = 1920
        window.get_screen.return_value.get_height.return_value = 1080
        window.get_window.return_value = None
        with patch.object(self.dock, 'HAS_LAYER_SHELL', True), \
                patch.object(self.dock, 'is_wayland', return_value=True), \
                patch.object(self.dock, 'GtkLayerShell', shell, create=True):
            self.assertEqual(self.dock.apply_dock(window, 'right', 400), expected)
        return window

    def test_dock_allows_keyboard_without_exclusive_focus_on_protocol_four(self):
        shell = self.shell()
        window = self.apply(shell)
        shell.set_keyboard_mode.assert_called_once_with(window, 'on-demand')

    def test_older_protocol_preserves_an_ordinary_focusable_window(self):
        for version in (1, 2, 3):
            with self.subTest(protocol=version):
                shell = self.shell(version)
                self.apply(shell, expected='window')
                shell.init_for_window.assert_not_called()
                shell.set_keyboard_mode.assert_not_called()

    def test_older_library_does_not_enable_exclusive_keyboard_interactivity(self):
        shell = self.shell()
        del shell.set_keyboard_mode
        del shell.KeyboardMode
        del shell.get_protocol_version
        shell.set_keyboard_interactivity = Mock()
        self.apply(shell, expected='window')
        shell.init_for_window.assert_not_called()
        shell.set_keyboard_interactivity.assert_not_called()

    def test_missing_protocol_query_does_not_request_unsupported_on_demand(self):
        shell = self.shell()
        del shell.get_protocol_version
        self.apply(shell, expected='window')
        shell.init_for_window.assert_not_called()
        shell.set_keyboard_mode.assert_not_called()

    def test_older_dock_to_float_never_retains_exclusive_keyboard_focus(self):
        shell = self.shell(protocol=3)
        shell.is_layer_window.return_value = False
        window = self.apply(shell, expected='window')
        with patch.object(self.dock, 'HAS_LAYER_SHELL', True), \
                patch.object(self.dock, 'GtkLayerShell', shell, create=True):
            self.dock.apply_float(window, always_on_top=False)
        shell.init_for_window.assert_not_called()
        shell.set_keyboard_mode.assert_not_called()
        window.set_keep_above.assert_called_with(False)

    def test_helper_button_does_not_take_keyboard_focus(self):
        shell = self.shell()
        shell.set_namespace = Mock()
        shell.set_margin = Mock()
        with patch.object(self.dock, 'HAS_LAYER_SHELL', True), \
                patch.object(self.dock, 'is_wayland', return_value=True), \
                patch.object(self.dock, 'GtkLayerShell', shell, create=True):
            self.assertTrue(self.dock.apply_float_button(Mock(), 'right'))
        shell.set_keyboard_mode.assert_not_called()


class TestFeatureVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src.main_window import MainWindow
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.MainWindow = MainWindow

    @contextmanager
    def window(self):
        from src.ai_client import AIClient
        from src.config_manager import ConfigManager
        from src.system_utils import SystemUtils
        with tempfile.TemporaryDirectory() as directory, \
                patch('pathlib.Path.home', return_value=Path(directory)):
            config = ConfigManager(str(Path(directory) / 'config.json'))
            config.set('features.screen_capture', False)
            config.set('features.expert_mode', False)
            config.set_assistance_mode('offline')
            client = AIClient(config)
            window = self.MainWindow(SimpleNamespace(tray_icon=None), config, client,
                                     SystemUtils(config))
            try:
                yield window
            finally:
                window.history_store.close()
                client.flush_usage()
                config.flush()
                window.destroy()

    def test_disabled_features_stay_hidden_on_startup_and_repeated_toggle(self):
        with self.window() as window:
            window.show_all()  # The startup path uses the same GTK operation.
            for _ in range(3):
                self.assertFalse(window.capture_btn.get_visible())
                self.assertFalse(window.expert_btn.get_visible())
                window.toggle_visibility()
                window.toggle_visibility()
                self.assertTrue(window.get_visible())

    def test_changing_preferences_controls_buttons_after_show_all(self):
        with self.window() as window:
            window.config.set('features.screen_capture', True)
            window._apply_feature_toggles()
            window.show_all()
            self.assertTrue(window.capture_btn.get_visible())
            self.assertFalse(window.expert_btn.get_visible())
            window.config.set('features.screen_capture', False)
            window.config.set('features.expert_mode', True)
            window._apply_feature_toggles()
            window.hide()
            window.toggle_visibility()
            self.assertFalse(window.capture_btn.get_visible())
            self.assertTrue(window.expert_btn.get_visible())


if __name__ == '__main__':
    unittest.main()
