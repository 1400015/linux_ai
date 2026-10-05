"""Desktop activation, portal lifecycle and real X11 passive grabs."""

import ctypes
import ctypes.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.global_shortcuts import (DesktopActivation, GlobalShortcut, PortalShortcut,
                                  X11Shortcut, validate_accelerator, portal_trigger)


class TestDisabledShortcut(unittest.TestCase):
    def test_disabled_never_requires_a_desktop(self):
        manager = GlobalShortcut(Mock())
        with patch('src.global_shortcuts._bindings', side_effect=AssertionError('unexpected GTK import')):
            self.assertEqual(manager.configure(False, '<Ctrl><Alt>space'), 'Global shortcut disabled')


class FakePortal:
    def __init__(self):
        self.signals = {}
        self.calls = []
        self.next_id = 0

    def get_unique_name(self):
        return ':1.42'

    def signal_subscribe(self, sender, interface, signal, path, argument, flags, callback):
        self.next_id += 1
        self.signals[self.next_id] = (interface, signal, path, callback)
        return self.next_id

    def signal_unsubscribe(self, identifier):
        self.signals.pop(identifier, None)

    def call(self, *args):
        self.calls.append(args)

    def call_finish(self, result):
        return result

    def emit(self, interface, signal, path, parameters):
        for (_interface, _signal, _path, callback) in tuple(self.signals.values()):
            if (_interface, _signal, _path) == (interface, signal, path):
                callback(self, 'org.freedesktop.portal.Desktop', path, interface, signal, parameters)


class TestPortalShortcut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import GLib
        except (ImportError, ValueError):
            raise unittest.SkipTest('GTK3 bindings unavailable')
        cls.GLib = GLib

    def setUp(self):
        self.connection = FakePortal()
        self.callback = Mock()
        self.status = Mock()
        self.shortcut = PortalShortcut(self.callback, self.status, self.connection)

    def finish_request(self, code, values):
        request_path = next(iter(self.shortcut.pending_requests))
        self.connection.emit('org.freedesktop.portal.Request', 'Response', request_path,
                             self.GLib.Variant('(ua{sv})', (code, values)))

    def test_accelerator_requires_explicit_modifier_and_portal_notation(self):
        self.assertEqual(portal_trigger('<Ctrl><Alt>space'), 'CTRL+ALT+space')
        for accelerator in ('space', '', '<Shift>a', 'a' * 129):
            with self.assertRaises(ValueError):
                validate_accelerator(accelerator)

    def test_user_approval_then_only_own_shortcut_activates(self):
        self.shortcut.start('<Ctrl><Alt>space')
        self.assertEqual(self.connection.calls[-1][3], 'CreateSession')
        session = self.shortcut.session
        self.finish_request(0, {'session_handle': self.GLib.Variant('s', session)})
        bind = self.connection.calls[-1]
        self.assertEqual(bind[3], 'BindShortcuts')
        self.assertEqual(bind[4].unpack()[1][0][1]['preferred_trigger'], 'CTRL+ALT+space')
        self.finish_request(0, {'shortcuts': self.GLib.Variant('a(sa{sv})', [
            ('show-assistant', {'trigger_description': self.GLib.Variant('s', 'Ctrl Alt Space')})])})
        self.assertIn('active', self.status.call_args[0][0])
        for session_path, identifier in ((session + '_other', 'show-assistant'),
                                         (session, 'another-shortcut')):
            self.shortcut._activated(None, None, None, None, None,
                                     self.GLib.Variant('(osta{sv})', (session_path, identifier, 1, {})))
        self.callback.assert_not_called()
        self.shortcut._activated(None, None, None, None, None,
                                 self.GLib.Variant('(osta{sv})', (session, 'show-assistant', 1, {})))
        self.callback.assert_called_once_with()
        self.shortcut.close()
        self.assertFalse(self.connection.signals)
        self.assertTrue(self.shortcut.cancellable.is_cancelled())
        self.assertTrue(any(call[1] == session and call[3] == 'Close' for call in self.connection.calls))

    def test_denied_request_reports_fallback_and_releases_signals(self):
        self.shortcut.start('<Ctrl><Alt>space')
        self.finish_request(1, {})
        self.assertTrue(self.shortcut.closed)
        self.assertFalse(self.connection.signals)
        self.assertIn('linux-ai-assistant --show', self.status.call_args[0][0])
        self.callback.assert_not_called()

    def test_disable_closes_pending_portal_request(self):
        self.shortcut.start('<Ctrl><Alt>space')
        pending = next(iter(self.shortcut.pending_requests))
        self.shortcut.close()
        self.shortcut.close()
        close_calls = [call for call in self.connection.calls if call[3] == 'Close']
        self.assertEqual(len(close_calls), 2)
        self.assertTrue(any(call[1] == pending for call in close_calls))
        self.assertTrue(any(call[2] == 'org.freedesktop.portal.Session' for call in close_calls))
        self.assertFalse(self.connection.signals)

    def test_missing_portal_interface_reports_failure_without_callback(self):
        self.shortcut.start('<Ctrl><Alt>space')
        method_callback = self.connection.calls[-1][-1]
        with patch.object(self.connection, 'call_finish', side_effect=self.GLib.Error('unknown interface')):
            method_callback(self.connection, None)
        self.assertTrue(self.shortcut.closed)
        self.assertIn('unavailable', self.status.call_args[0][0])
        self.callback.assert_not_called()

    def test_wrong_session_handle_is_rejected_and_expected_session_closed(self):
        self.shortcut.start('<Ctrl><Alt>space')
        expected = self.shortcut.session
        self.finish_request(0, {'session_handle': self.GLib.Variant('o', expected + '_wrong')})
        self.assertTrue(self.shortcut.closed)
        self.assertTrue(any(call[1] == expected and call[3] == 'Close' for call in self.connection.calls))
        self.callback.assert_not_called()


class TestDesktopActivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
        except (ImportError, ValueError):
            raise unittest.SkipTest('GTK3 unavailable')

    def test_show_command_cross_process_activates_primary_without_a_new_app(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
import subprocess, sys
from pathlib import Path
from gi.repository import GLib
from src.global_shortcuts import APPLICATION_ID, DesktopActivation
marker = Path(sys.argv[1])
loop = GLib.MainLoop()
def show():
    marker.write_text('activated', encoding='utf-8')
    loop.quit()
activation = DesktopActivation(show, APPLICATION_ID)
assert activation.register()
child = subprocess.Popen([sys.executable, '-m', 'src.app', '--show'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
GLib.timeout_add_seconds(8, lambda: loop.quit() or False)
loop.run()
stdout, stderr = child.communicate(timeout=5)
assert child.returncode == 0, (stdout, stderr)
assert marker.read_text(encoding='utf-8') == 'activated'
activation.close()
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['dbus-run-session', '--', sys.executable, '-c', script,
                                     str(Path(directory) / 'activation')], cwd=str(root),
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_session_bus_refuses_nonunique_activation(self):
        endpoint = DesktopActivation(Mock(), 'org.linux_ai_assistant.NoBusTest')
        with patch.object(endpoint, 'application') as application:
            application.register.return_value = True
            application.get_dbus_connection.return_value = None
            with self.assertRaisesRegex(RuntimeError, 'session D-Bus'):
                endpoint.register()

    def test_real_x11_conflict_and_release(self):
        first = X11Shortcut(Mock(), Mock())
        second = X11Shortcut(Mock(), Mock())
        third = X11Shortcut(Mock(), Mock())
        try:
            first.start('<Ctrl><Alt>F12')
            with self.assertRaises(RuntimeError):
                second.start('<Ctrl><Alt>F12')
            second.close()
            first.close()
            third.start('<Ctrl><Alt>F12')
            self.assertIsNotNone(third.display)
        finally:
            first.close()
            second.close()
            third.close()

    def test_real_x11_grab_activates_with_xtest_key_events(self):
        from gi.repository import GLib
        callback = Mock()
        shortcut = X11Shortcut(callback, Mock())
        xtest = ctypes.CDLL(ctypes.util.find_library('Xtst'))
        xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtest.XTestFakeKeyEvent.restype = ctypes.c_int
        sender_display = None
        try:
            shortcut.start('<Ctrl><Alt>space')
            sender_display = shortcut.lib.XOpenDisplay(None)
            control = shortcut.lib.XKeysymToKeycode(shortcut.display, 0xffe3)
            alt = shortcut.lib.XKeysymToKeycode(shortcut.display, 0xffe9)
            for code in (control, alt, shortcut.keycode):
                xtest.XTestFakeKeyEvent(sender_display, code, True, 0)
            for code in (shortcut.keycode, alt, control):
                xtest.XTestFakeKeyEvent(sender_display, code, False, 0)
            shortcut.lib.XSync(sender_display, False)
            context = GLib.MainContext.default()
            for _ in range(100):
                if callback.called:
                    break
                context.iteration(False)
            callback.assert_called_once_with()
        finally:
            if sender_display:
                shortcut.lib.XCloseDisplay(sender_display)
            shortcut.close()


if __name__ == '__main__':
    unittest.main()
