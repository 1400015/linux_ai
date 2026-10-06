"""Wayland capture uses desktop consent without falling through to XWayland."""

import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import screenshot_portal
from src.system_utils import SystemUtils


class CaptureBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name) / 'screen.png'
        self.utility = object.__new__(SystemUtils)
        self.utility.is_wayland = True
        self.utility._which = lambda name: '/fixture/' + name

    def portal_success(self, path, **kwargs):
        Path(path).write_bytes(b'fixture portal image')

    def legacy_success(self, argv, timeout):
        Path(argv[-1]).write_bytes(b'fixture legacy image')
        return subprocess.CompletedProcess(argv, 0, '', '')

    def test_wayland_works_without_command_line_capture_utilities(self):
        self.utility._which = lambda name: None
        event = threading.Event()
        with patch.object(screenshot_portal, 'capture_screenshot', side_effect=self.portal_success) as portal, \
                patch('src.system_utils._run_process') as process:
            result = self.utility.capture_screen(str(self.target), cancel_event=event)
        self.assertEqual(result, (True, str(self.target)))
        self.assertEqual(self.target.read_bytes(), b'fixture portal image')
        self.assertEqual(portal.call_args.args[0], str(self.target))
        self.assertIs(portal.call_args.kwargs['cancel_event'], event)
        process.assert_not_called()

    def test_relative_cli_output_still_creates_the_requested_image(self):
        previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        try:
            with patch.object(screenshot_portal, 'capture_screenshot', side_effect=self.portal_success) as portal:
                result = self.utility.capture_screen('screen.png')
        finally:
            os.chdir(previous_directory)
        self.assertEqual(result, (True, 'screen.png'))
        self.assertEqual(self.target.read_bytes(), b'fixture portal image')
        self.assertEqual(portal.call_args.args[0], str(self.target))

    def test_already_cancelled_capture_does_not_open_permission_or_create_a_file(self):
        event = threading.Event()
        event.set()
        with patch.object(screenshot_portal, 'capture_screenshot') as portal, \
                patch('src.system_utils._run_process') as process, \
                patch('tempfile.mkstemp') as temporary:
            result = self.utility.capture_screen(cancel_event=event)
        self.assertFalse(result[0])
        self.assertIn('cancel', result[1].lower())
        portal.assert_not_called()
        process.assert_not_called()
        temporary.assert_not_called()

    def test_portal_decline_does_not_capture_with_another_backend(self):
        with patch.object(screenshot_portal, 'capture_screenshot',
                          side_effect=screenshot_portal.ScreenshotPortalCancelled('Screen capture cancelled')) as portal, \
                patch('src.system_utils._run_process') as process:
            result = self.utility.capture_screen(str(self.target))
        self.assertFalse(result[0])
        self.assertIn('cancel', result[1].lower())
        portal.assert_called_once()
        process.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_portal_error_does_not_fall_back_or_leave_generated_empty_file(self):
        paths = []

        def failure(path, **kwargs):
            paths.append(path)
            raise screenshot_portal.ScreenshotPortalError('Desktop portal timed out')

        with patch.object(screenshot_portal, 'capture_screenshot', side_effect=failure), \
                patch('src.system_utils._run_process') as process:
            result = self.utility.capture_screen()
        self.assertFalse(result[0])
        self.assertIn('timed out', result[1])
        self.assertEqual(len(paths), 1)
        self.assertFalse(Path(paths[0]).exists())
        process.assert_not_called()

    def test_unavailable_portal_preserves_grim_capture_on_wlroots(self):
        self.utility._which = lambda name: '/fixture/grim' if name == 'grim' else None
        with patch.object(screenshot_portal, 'capture_screenshot',
                          side_effect=screenshot_portal.ScreenshotPortalUnavailable('No screenshot portal')), \
                patch('src.system_utils._run_process', side_effect=self.legacy_success) as process:
            result = self.utility.capture_screen(str(self.target))
        self.assertEqual(result, (True, str(self.target)))
        self.assertEqual(self.target.read_bytes(), b'fixture legacy image')
        self.assertEqual(process.call_args.args[0], ['/fixture/grim', str(self.target)])

    def test_unavailable_portal_cannot_use_xwayland_scrot(self):
        self.utility._which = lambda name: '/fixture/scrot' if name == 'scrot' else None
        with patch.dict(os.environ, {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}), \
                patch.object(screenshot_portal, 'capture_screenshot',
                             side_effect=screenshot_portal.ScreenshotPortalUnavailable('No screenshot portal')), \
                patch('src.system_utils._run_process') as process:
            result = self.utility.capture_screen(str(self.target))
        self.assertFalse(result[0])
        process.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_x11_keeps_scrot_and_does_not_request_portal_consent(self):
        self.utility.is_wayland = False
        self.utility._which = lambda name: '/fixture/scrot' if name == 'scrot' else None
        with patch.object(screenshot_portal, 'capture_screenshot') as portal, \
                patch('src.system_utils._run_process', side_effect=self.legacy_success) as process:
            result = self.utility.capture_screen(str(self.target))
        self.assertEqual(result, (True, str(self.target)))
        self.assertEqual(process.call_args.args[0], ['/fixture/scrot', str(self.target)])
        portal.assert_not_called()

    def test_wayland_display_and_sway_socket_win_over_xwayland_display(self):
        for native in ({'WAYLAND_DISPLAY': 'wayland-0'}, {'SWAYSOCK': '/fixture/sway.sock'}):
            with self.subTest(native=native), \
                    patch.dict(os.environ, dict(native, DISPLAY=':0'), clear=True):
                config = SimpleNamespace(get=lambda key, default=None: default)
                self.assertTrue(SystemUtils(config).is_wayland)


if __name__ == '__main__':
    unittest.main()
