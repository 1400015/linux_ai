"""The tray is the only entry point while the window is hidden.

Nothing else exercises `TrayIcon`: the suite builds `MainWindow` with
`tray_icon=None`, so both construction paths (AppIndicator3 and the
StatusIcon fallback) and every menu action were untested.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TrayIconTests(unittest.TestCase):
    def setUp(self):
        from src import tray_icon as tray_module
        from src.i18n import _, get_language, set_language
        self.tray_module = tray_module
        self._ = _
        self.original_language = get_language()
        self.addCleanup(set_language, self.original_language)
        set_language('en')

        self.app = SimpleNamespace(quit=Mock())
        self.window = SimpleNamespace(
            toggle_visibility=Mock(),
            on_expert_mode_toggled=Mock(),
            _show_config_dialog=Mock(),
            _show_history_dialog=Mock(),
            _show_stats_dialog=Mock(),
            expert_mode=False,
        )
        self.config = SimpleNamespace(get=Mock(return_value=False))

    def build(self, indicator=True):
        """Construct a tray; `indicator=False` forces the StatusIcon path."""
        if not indicator:
            # Attribute access on None raises AttributeError, which is the
            # exact signal _create_tray_icon() treats as "no AppIndicator".
            patcher = patch.object(self.tray_module, 'AppIndicator3', None)
            patcher.start()
            self.addCleanup(patcher.stop)
        tray = self.tray_module.TrayIcon(self.app, self.config, self.window)
        self.addCleanup(setattr, tray, 'menu', None)
        return tray

    def labels(self, tray):
        labels = []
        for child in tray.menu.get_children():
            if isinstance(child, Gtk.SeparatorMenuItem):
                labels.append(None)
            elif isinstance(child, Gtk.CheckMenuItem):
                labels.append(child.get_label())
            else:
                labels.append(child.get_label())
        return labels

    def test_menu_lists_the_documented_actions_in_order(self):
        for indicator in (True, False):
            with self.subTest(indicator=indicator):
                tray = self.build(indicator=indicator)
                self.assertIsNotNone(getattr(tray, 'menu', None),
                                     'the tray menu must exist on both backends')
                expected = [
                    self._('Show Window'), None,
                    self._('Expert Mode'), None,
                    self._('Settings'),
                    self._('Conversation History'),
                    self._('Statistics'), None,
                    self._('Quit'),
                ]
                self.assertEqual(self.labels(tray), expected)

    def test_menu_actions_delegate_to_the_window_and_the_app(self):
        tray = self.build()
        items = [child for child in tray.menu.get_children()]
        activate = [item for item in items if isinstance(item, Gtk.MenuItem)
                    and not isinstance(item, Gtk.CheckMenuItem)]
        # Show Window, Settings, Conversation History, Statistics, Quit
        for item in activate:
            item.emit('activate')
        self.window.toggle_visibility.assert_called_once()
        self.window._show_config_dialog.assert_called_once()
        self.window._show_history_dialog.assert_called_once()
        self.window._show_stats_dialog.assert_called_once()
        self.app.quit.assert_called_once()

    def test_expert_mode_sync_does_not_bounce_back_into_the_window(self):
        tray = self.build()
        self.assertFalse(tray.expert_item.get_active())

        # Programmatic sync: the guard must swallow the "toggled" signal.
        tray.update_expert_mode(True)
        self.assertTrue(tray.expert_item.get_active())
        self.window.on_expert_mode_toggled.assert_not_called()

        # A real user click has to reach the window exactly once.
        tray.expert_item.set_active(False)
        self.window.on_expert_mode_toggled.assert_called_once_with(None)

        # Re-syncing to the same value is a no-op.
        tray.update_expert_mode(False)
        self.window.on_expert_mode_toggled.assert_called_once_with(None)

    def test_toggle_label_tracks_window_visibility(self):
        tray = self.build()
        self.assertEqual(tray.toggle_item.get_label(), self._('Show Window'))
        tray.update_toggle_label(True)
        self.assertEqual(tray.toggle_item.get_label(), self._('Hide Window'))
        tray.update_toggle_label(False)
        self.assertEqual(tray.toggle_item.get_label(), self._('Show Window'))

    def test_status_icon_backend_still_receives_clicks(self):
        tray = self.build(indicator=False)
        if getattr(tray, 'status_icon', None) is None:
            self.skipTest('Gtk.StatusIcon unavailable in this session')
        tray.on_tray_clicked(tray.status_icon)
        self.window.toggle_visibility.assert_called_once()

    def test_construction_never_raises_when_no_backend_is_usable(self):
        # Both backends failing must not abort startup: the window and the
        # float button still work without a tray.
        with patch.object(self.tray_module, 'AppIndicator3', None), \
                patch.object(self.tray_module.Gtk, 'StatusIcon',
                             side_effect=RuntimeError('no tray here')):
            tray = self.tray_module.TrayIcon(self.app, self.config, self.window)
        self.assertIsNone(getattr(tray, 'status_icon', None))
        # The menu is what the window and the handlers rely on existing.
        self.assertIsNotNone(getattr(tray, 'menu', None),
                             'the menu must be built even when the icon fails')


if __name__ == '__main__':
    unittest.main()
