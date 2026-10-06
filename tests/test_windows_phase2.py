"""Phase 2 drawer toggle: tray icon click shows and hides the main window."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


def _install_fake_gi():
    """Import src.tray_icon without GTK: the module degrades to StatusIcon."""
    gi = SimpleNamespace(
        require_version=Mock(),
        repository=SimpleNamespace(
            Gtk=Mock(),
            Gdk=Mock(),
            GdkPixbuf=Mock(),
        ),
    )
    installed = {"gi": gi, "gi.repository": gi.repository}
    saved = {name: sys.modules.get(name) for name in installed}
    sys.modules.update(installed)
    try:
        import src.tray_icon as tray_icon
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return tray_icon


def config(value=True):
    manager = Mock()
    manager.get = Mock(return_value=value)
    return manager


class TestToggleDecision(unittest.TestCase):
    def setUp(self):
        self.tray_icon = _install_fake_gi()

    def test_toggle_enabled_by_default(self):
        manager = config()
        manager.get = Mock(side_effect=KeyError("missing"))
        self.assertTrue(self.tray_icon.toggle_on_click_enabled(manager))
        self.assertTrue(self.tray_icon.toggle_on_click_enabled(config(True)))

    def test_toggle_can_be_disabled_by_config(self):
        self.assertFalse(self.tray_icon.toggle_on_click_enabled(config(False)))

    def test_broken_config_keeps_the_default(self):
        broken = Mock()
        broken.get = Mock(side_effect=RuntimeError("unavailable"))
        self.assertTrue(self.tray_icon.toggle_on_click_enabled(broken, default=True))
        self.assertFalse(self.tray_icon.toggle_on_click_enabled(broken, default=False))


class TestDrawerToggle(unittest.TestCase):
    def setUp(self):
        self.tray_icon = _install_fake_gi()

    def tray(self, value=True):
        main_window = Mock()
        tray = self.tray_icon.TrayIcon(
            SimpleNamespace(quit=Mock()), config(value), main_window)
        return tray, main_window

    def test_click_toggles_window_when_enabled(self):
        tray, main_window = self.tray(True)
        tray.on_tray_clicked(None)
        main_window.toggle_visibility.assert_called_once_with()

    def test_click_is_ignored_when_disabled(self):
        tray, main_window = self.tray(False)
        tray.on_tray_clicked(None)
        main_window.toggle_visibility.assert_not_called()

    def test_menu_toggle_item_still_works_when_click_is_disabled(self):
        tray, main_window = self.tray(False)
        tray.on_toggle_window(None)
        main_window.toggle_visibility.assert_called_once_with()

    def test_toggle_label_follows_visibility(self):
        tray, main_window = self.tray(True)
        tray.toggle_item = Mock()
        tray.update_toggle_label(True)
        label_visible = tray.toggle_item.set_label.call_args[0][0]
        tray.update_toggle_label(False)
        label_hidden = tray.toggle_item.set_label.call_args[0][0]
        self.assertNotEqual(label_visible, label_hidden)


if __name__ == "__main__":
    unittest.main()
