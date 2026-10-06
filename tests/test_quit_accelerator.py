"""Ctrl+Q must stop GTK accelerator dispatch before its group is destroyed."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


QUIT_FROM_ACCELERATOR = r'''
import faulthandler
from pathlib import Path
import tempfile
from unittest.mock import patch

faulthandler.enable()
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
from src.app import LinuxAIAssistant
from src.main_window import MainWindow
from src.config_manager import ConfigManager
from src.ai_client import AIClient
from src.system_utils import SystemUtils
from src.history_store import HistoryStore

assert Gtk.init_check()[0]
with tempfile.TemporaryDirectory() as directory, \
        patch('pathlib.Path.home', return_value=Path(directory)), \
        patch('requests.sessions.Session.request', side_effect=AssertionError('unexpected network request')):
    root = Path(directory)
    config = ConfigManager(str(root / 'config.json'))
    config.set_assistance_mode('offline')
    client = AIClient(config)
    app = LinuxAIAssistant.__new__(LinuxAIAssistant)
    app._quitting = False
    app._shutdown_pending = False
    app._history_shutdown_source = None
    app._global_shortcut = None
    app.config = config
    app.float_button_window = None
    app.tray_icon = None
    app.main_window = MainWindow(app, config, client, SystemUtils(config),
                                 history_store=HistoryStore(root / 'history.json'))
    window = app.main_window
    window.show_all()
    key, modifiers = Gtk.accelerator_parse('<Control>q')
    result = {}

    def activate():
        result['handled'] = Gtk.accel_groups_activate(window, key, modifiers)
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(80, activate)
    watchdog = GLib.timeout_add(2000, lambda: (app.quit(), GLib.SOURCE_REMOVE)[1])
    Gtk.main()
    GLib.source_remove(watchdog)
    assert result.get('handled') is True, 'Ctrl+Q was not marked as handled'
    assert app._quitting, 'the actual application did not quit'
    assert not window.get_visible(), 'the application window was not destroyed'
    client.flush_usage()
    client.session.close()
    config.flush()
'''


class TestQuitAccelerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable')
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))

    def test_ctrl_q_is_handled_and_closes_real_application_without_accelerator_crash(self):
        environment = os.environ.copy()
        environment['PYTHONNOUSERSITE'] = '1'
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', QUIT_FROM_ACCELERATOR], cwd=str(root),
                                env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('gtk_accel_group_activate', result.stderr)
        self.assertNotIn('Segmentation fault', result.stderr)


if __name__ == '__main__':
    unittest.main()
