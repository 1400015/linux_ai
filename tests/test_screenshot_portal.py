"""Portal permission lifecycle and bounded, private screenshot snapshots."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin

from src.screenshot_portal import (
    PORTAL_NAME, PORTAL_PATH, ScreenshotPortalCancelled, ScreenshotPortalError,
    ScreenshotPortalUnavailable, _BoundedPNG, _PortalRequest, _copy_snapshot,
    _local_path, capture_screenshot,
)


def variant(value):
    return SimpleNamespace(unpack=lambda: value)


class TestPortalResponse(unittest.TestCase):
    def setUp(self):
        gio = SimpleNamespace(Cancellable=Mock(), DBusCallFlags=SimpleNamespace(NONE=0))
        glib = SimpleNamespace(MainLoop=SimpleNamespace(new=Mock(return_value=Mock())),
                               Error=type("FakeGLibError", (Exception,), {}))
        self.request = _PortalRequest(gio, glib, Mock(), threading.Event(), time.monotonic() + 5)
        self.request.owner = ":1.10"
        self.request.request_path = PORTAL_PATH + "/request/1_11/token"

    def response(self, code, values, sender=":1.10", path=None):
        self.request._response(None, sender, path or self.request.request_path,
                               None, None, variant((code, values)))

    def test_success_requires_matching_method_reply_and_response(self):
        self.response(0, {"uri": "file:///tmp/approved.png"})
        self.assertFalse(self.request.finished)
        connection = Mock()
        connection.call_finish.return_value = variant((self.request.request_path,))
        self.request._method_ready(connection, None)
        self.assertTrue(self.request.finished)
        self.assertIsNone(self.request.error)

    def test_wrong_sender_and_request_are_ignored(self):
        self.response(0, {"uri": "file:///tmp/image.png"}, sender=":1.666")
        self.response(0, {"uri": "file:///tmp/image.png"}, path=self.request.request_path + "_other")
        self.assertIsNone(self.request.response_uri)
        self.assertFalse(self.request.finished)

    def test_user_cancel_and_denial_are_definitive(self):
        for code, expected in ((1, ScreenshotPortalCancelled), (2, ScreenshotPortalError),
                               (99, ScreenshotPortalError)):
            with self.subTest(code=code):
                self.setUp()
                self.response(code, {})
                self.assertIsInstance(self.request.error, expected)
                self.assertNotIsInstance(self.request.error, ScreenshotPortalUnavailable)

    def test_malformed_or_missing_uri_is_rejected(self):
        for value in ((0, {}), (False, {}), (0, []), (0, {"uri": 12}), (0,), None):
            with self.subTest(value=value):
                self.setUp()
                self.request._response(None, self.request.owner, self.request.request_path,
                                       None, None, variant(value))
                self.assertIsInstance(self.request.error, ScreenshotPortalError)

    def test_unobserved_method_handle_is_rejected(self):
        connection = Mock()
        connection.call_finish.return_value = variant(("/another/request",))
        self.request._method_ready(connection, None)
        self.assertIsInstance(self.request.error, ScreenshotPortalError)

    def test_cancellation_and_deadline_quit_context(self):
        self.request.cancel_event.set()
        self.assertFalse(self.request._poll())
        self.assertIsInstance(self.request.error, ScreenshotPortalCancelled)
        self.request.loop.quit.assert_called_once()
        self.setUp()
        self.request.deadline = time.monotonic() - 1
        self.assertFalse(self.request._poll())
        self.assertIsInstance(self.request.error, ScreenshotPortalError)
        self.assertNotIsInstance(self.request.error, ScreenshotPortalUnavailable)

    def test_owner_change_aborts_request(self):
        self.request._owner_changed(None, None, None, None, None,
                                    variant((PORTAL_NAME, self.request.owner, ":1.20")))
        self.assertIsInstance(self.request.error, ScreenshotPortalError)


class TestPortalSnapshot(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "desktop image.png"
        self.output = self.root / "private.png"
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "private metadata")
        with Image.new("RGBA", (64, 32), (10, 20, 30, 255)) as image:
            image.save(self.source, pnginfo=metadata)

    def copy(self, uri=None, output=None, event=None):
        _copy_snapshot(uri or self.source.as_uri(), str(output or self.output), event, time.monotonic() + 5)

    def test_private_png_without_metadata_and_original_preserved(self):
        original = self.source.read_bytes()
        self.copy()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        with Image.open(self.output) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (64, 32))
            self.assertFalse(image.info)

    def test_remote_or_malformed_uri_is_rejected(self):
        for uri in ("https://example.com/image.png", "file://remote/tmp/image.png",
                    "file:///tmp/image.png?download=true", "file:///tmp/image.png#fragment",
                    "file:relative.png", "file:///tmp/%00.png", "file:///tmp/%FF.png"):
            with self.subTest(uri=uri), self.assertRaises(ScreenshotPortalError):
                _local_path(uri)

    def test_symlink_source_and_parent_are_rejected(self):
        link = self.root / "link.png"
        link.symlink_to(self.source)
        with self.assertRaises(ScreenshotPortalError):
            self.copy(link.as_uri())
        directory_link = self.root / "directory-link"
        directory_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ScreenshotPortalError):
            self.copy((directory_link / self.source.name).as_uri())

    def test_fifo_is_rejected_without_waiting_for_writer(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ScreenshotPortalError):
            self.copy(fifo.as_uri())

    def test_source_and_output_must_be_distinct(self):
        original = self.source.read_bytes()
        with self.assertRaises(ScreenshotPortalError):
            self.copy(output=self.source)
        self.assertEqual(self.source.read_bytes(), original)

    def test_invalid_and_oversized_source_preserve_existing_output(self):
        self.output.write_bytes(b"existing snapshot")
        self.source.write_bytes(b"not an image")
        with self.assertRaises(ScreenshotPortalError):
            self.copy()
        self.assertEqual(self.output.read_bytes(), b"existing snapshot")
        with patch("src.screenshot_portal.MAX_IMAGE_BYTES", 5):
            with self.assertRaises(ScreenshotPortalError):
                self.copy()
        self.assertEqual(self.output.read_bytes(), b"existing snapshot")

    def test_dimension_and_encoded_output_limits(self):
        with patch("src.screenshot_portal.MAX_IMAGE_PIXELS", 1):
            with self.assertRaises(ScreenshotPortalError):
                self.copy()
        with patch("src.screenshot_portal.MAX_IMAGE_BYTES", 5):
            with _BoundedPNG() as stream:
                with self.assertRaises(ScreenshotPortalError):
                    stream.write(b"123456")

    def test_cancel_does_not_replace_existing_output(self):
        self.output.write_bytes(b"old snapshot")
        event = threading.Event()
        event.set()
        with self.assertRaises(ScreenshotPortalCancelled):
            self.copy(event=event)
        self.assertEqual(self.output.read_bytes(), b"old snapshot")

    def test_import_is_safe_without_gi_and_pre_cancel_never_opens_bus(self):
        result = subprocess.run([sys.executable, "-c", "import sys; sys.modules['gi'] = None; "
                                 "import src.screenshot_portal"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        event = threading.Event()
        event.set()
        with patch("src.screenshot_portal._bindings", side_effect=AssertionError("bus opened")):
            with self.assertRaises(ScreenshotPortalCancelled):
                capture_screenshot(str(self.output), event)

    def test_timeout_validation(self):
        for value in (0, -1, float("inf"), float("nan"), True, "120"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture_screenshot(str(self.output), timeout=value)


# An actual session bus verifies Gio callback dispatch in the worker's private
# context. The fake portal can hold the method reply as well as the permission
# response, reproducing the desktop authorization wait without a compositor.
REAL_PORTAL_TEST = r'''
import pathlib, sys, tempfile, threading, time
from gi.repository import Gio, GLib
from PIL import Image
from src.screenshot_portal import capture_screenshot, ScreenshotPortalCancelled, ScreenshotPortalError
mode = sys.argv[1]
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                     'RequestName', GLib.Variant('(su)', ('org.freedesktop.portal.Desktop', 0)),
                     GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 1000, None)
xml = ''' + '"""' + r'''
<node><interface name="org.freedesktop.portal.Screenshot">
<method name="Screenshot"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="o" direction="out"/></method></interface></node>
''' + '"""' + r'''
request_xml = '<node><interface name="org.freedesktop.portal.Request"><method name="Close"/></interface></node>'
portal_info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
request_info = Gio.DBusNodeInfo.new_for_xml(request_xml).interfaces[0]
loop = GLib.MainLoop()
directory = tempfile.TemporaryDirectory()
image_path = pathlib.Path(directory.name) / 'approved.png'
output_path = pathlib.Path(directory.name) / 'snapshot.png'
Image.new('RGB', (80, 40), 'blue').save(image_path)
event = threading.Event()
closed = threading.Event()
requested = threading.Event()
errors = []
def guarded(callback):
    def invoke(*args):
        try:
            return callback(*args)
        except BaseException as error:
            errors.append('portal callback: ' + repr(error))
            event.set()
            GLib.idle_add(loop.quit)
            return False
    return invoke
@guarded
def close(*args):
    closed.set()
    args[-1].return_value(GLib.Variant('()', ()))
@guarded
def screenshot(_conn, sender, path, interface, method, parameters, invocation):
    parent, options = parameters.unpack()
    assert options['interactive'] is True and parent == ''
    token = options['handle_token']
    handle = '/org/freedesktop/portal/desktop/request/' + sender.lstrip(':').replace('.', '_') + '/' + token
    connection.register_object(handle, request_info, close, None, None)
    requested.set()
    if mode == 'cancel-before-reply':
        event.set()
        return
    if mode != 'response-before-reply':
        invocation.return_value(GLib.Variant('(o)', (handle,)))
    if mode in ('timeout', 'cancel'):
        if mode == 'cancel':
            GLib.timeout_add(30, lambda: event.set() or False)
        return
    code = 1 if mode == 'desktop-cancel' else 2 if mode == 'denied' else 0
    uri = 'https://example.com/image.png' if mode == 'remote-uri' else image_path.as_uri()
    response = GLib.Variant('(ua{sv})', (code, {'uri': GLib.Variant('s', uri)}))
    connection.emit_signal(sender, handle, 'org.freedesktop.portal.Request', 'Response', response)
    if mode == 'response-before-reply':
        @guarded
        def deferred_reply():
            invocation.return_value(GLib.Variant('(o)', (handle,)))
            return False
        GLib.timeout_add(40, deferred_reply)
connection.register_object('/org/freedesktop/portal/desktop', portal_info, screenshot, None, None)
def worker():
    began = time.monotonic()
    try:
        try:
            capture_screenshot(str(output_path), event, timeout=.25 if mode == 'timeout' else 3)
            assert mode in ('success', 'response-before-reply'), mode
            assert output_path.stat().st_mode & 0o777 == 0o600
            assert image_path.exists()
            with Image.open(output_path) as im:
                assert im.size == (80, 40)
        except ScreenshotPortalCancelled:
            assert mode in ('cancel', 'cancel-before-reply', 'desktop-cancel'), mode
        except ScreenshotPortalError:
            assert mode in ('timeout', 'denied', 'remote-uri'), mode
        if mode in ('cancel', 'cancel-before-reply', 'timeout'):
            assert closed.wait(.75), 'portal Request.Close was not delivered'
            assert time.monotonic() - began < 1.5
    except BaseException as error:
        errors.append('worker: ' + repr(error))
    finally:
        GLib.idle_add(loop.quit)
thread = threading.Thread(target=worker)
thread.start()
GLib.timeout_add_seconds(8, loop.quit)
loop.run()
thread.join(1)
assert not thread.is_alive(), 'worker remained blocked'
assert requested.is_set(), 'portal was not called'
assert not errors, errors
'''


class TestRealPortalBus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from gi.repository import Gio  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Gio unavailable")
        if not shutil.which("dbus-run-session"):
            raise unittest.SkipTest("dbus-run-session unavailable")

    def test_worker_response_and_permission_lifecycle(self):
        for mode in ("success", "response-before-reply", "cancel-before-reply", "cancel", "desktop-cancel", "denied",
                     "timeout", "remote-uri"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["dbus-run-session", "--", sys.executable, "-c", REAL_PORTAL_TEST, mode],
                    cwd=str(Path(__file__).resolve().parents[1]), capture_output=True,
                    text=True, timeout=12)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_service_is_unavailable_not_denied(self):
        script = '''
from src.screenshot_portal import capture_screenshot, ScreenshotPortalUnavailable
try:
    capture_screenshot('/tmp/unused-portal-test.png', timeout=2)
except ScreenshotPortalUnavailable:
    pass
else:
    raise AssertionError('missing service was not identified')
'''
        result = subprocess.run(["dbus-run-session", "--", sys.executable, "-c", script],
                                cwd=str(Path(__file__).resolve().parents[1]),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
