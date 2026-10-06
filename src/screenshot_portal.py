"""User-mediated Wayland screenshots through the desktop portal.

Run ``capture_screenshot`` in a worker. Its private GLib context receives the
portal response without running GTK callbacks in that worker. A cancelled or
failed permission request never authorizes a different capture backend.
"""

import io
import math
import os
import stat
import time
import uuid
import warnings
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError


PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_INTERFACE = "org.freedesktop.portal.Screenshot"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1000 * 1000
MAX_IMAGE_EDGE = 16384


class ScreenshotPortalError(RuntimeError):
    """The requested screenshot was not obtained safely."""


class ScreenshotPortalUnavailable(ScreenshotPortalError):
    """No screenshot portal is available, before any authorization response."""


class ScreenshotPortalCancelled(ScreenshotPortalError):
    """The operator or the desktop cancelled this particular capture."""


def _bindings():
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        raise ScreenshotPortalUnavailable("Desktop screenshot portal bindings are unavailable") from None
    return Gio, GLib


def _check_deadline(cancel_event, deadline):
    if cancel_event is not None and cancel_event.is_set():
        raise ScreenshotPortalCancelled("Screen capture cancelled")
    if time.monotonic() >= deadline:
        raise ScreenshotPortalError("Screen capture timed out while awaiting desktop permission or capture")


def _unavailable_error(Gio, error):
    name = Gio.DBusError.get_remote_error(error) or ""
    return name in {
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.UnknownMethod",
    }


class _PortalRequest:
    def __init__(self, Gio, GLib, context, cancel_event, deadline):
        self.Gio, self.GLib, self.context = Gio, GLib, context
        self.cancel_event, self.deadline = cancel_event, deadline
        self.loop = GLib.MainLoop.new(context, False)
        self.cancellable = Gio.Cancellable()
        self.connection = None
        self.owner = None
        self.activation_attempted = False
        self.request_path = None
        self.subscriptions = []
        self.finished = False
        self.method_returned = False
        self.response_uri = None
        self.error = None

    def _remaining_ms(self):
        return max(1, min(10000, int((self.deadline - time.monotonic()) * 1000)))

    def _finish(self, error=None):
        if not self.finished:
            self.finished = True
            self.error = error
            self.loop.quit()

    def _poll(self, *_args):
        try:
            _check_deadline(self.cancel_event, self.deadline)
        except ScreenshotPortalError as error:
            self._finish(error)
        return not self.finished

    def _call_error(self, error):
        try:
            _check_deadline(self.cancel_event, self.deadline)
        except ScreenshotPortalError as terminal_error:
            self._finish(terminal_error)
            return
        if _unavailable_error(self.Gio, error) and self.response_uri is None:
            self._finish(ScreenshotPortalUnavailable("Desktop screenshot portal is unavailable"))
        else:
            self._finish(ScreenshotPortalError("The desktop screenshot portal request failed"))

    def _bus_ready(self, _source, result):
        if self.finished:
            return
        try:
            self.connection = self.Gio.bus_get_finish(result)
            self.connection.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetNameOwner", self.GLib.Variant("(s)", (PORTAL_NAME,)),
                self.GLib.VariantType.new("(s)"), self.Gio.DBusCallFlags.NONE,
                self._remaining_ms(), self.cancellable, self._owner_ready)
        except self.GLib.Error as error:
            self._call_error(error)

    def _service_started(self, connection, result):
        if self.finished:
            return
        try:
            connection.call_finish(result)
            connection.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetNameOwner", self.GLib.Variant("(s)", (PORTAL_NAME,)),
                self.GLib.VariantType.new("(s)"), self.Gio.DBusCallFlags.NONE,
                self._remaining_ms(), self.cancellable, self._owner_ready)
        except self.GLib.Error as error:
            self._call_error(error)

    def _owner_ready(self, connection, result):
        if self.finished:
            return
        try:
            _check_deadline(self.cancel_event, self.deadline)
            self.owner = connection.call_finish(result).unpack()[0]
            if not isinstance(self.owner, str) or not self.owner.startswith(":"):
                raise ScreenshotPortalError("Invalid desktop portal owner")
            sender = connection.get_unique_name().lstrip(":").replace(".", "_")
            token = "linux_ai_screenshot_" + uuid.uuid4().hex
            self.request_path = PORTAL_PATH + "/request/" + sender + "/" + token
            self.subscriptions.append(connection.signal_subscribe(
                self.owner, REQUEST_INTERFACE, "Response", self.request_path, None,
                self.Gio.DBusSignalFlags.NONE, self._response))
            self.subscriptions.append(connection.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", PORTAL_NAME,
                self.Gio.DBusSignalFlags.NONE, self._owner_changed))
            connection.call(
                self.owner, PORTAL_PATH, PORTAL_INTERFACE, "Screenshot",
                self.GLib.Variant("(sa{sv})", ("", {
                    "handle_token": self.GLib.Variant("s", token),
                    "interactive": self.GLib.Variant("b", True),
                })), self.GLib.VariantType.new("(o)"), self.Gio.DBusCallFlags.NONE,
                max(1, int((self.deadline - time.monotonic()) * 1000)),
                self.cancellable, self._method_ready)
        except self.GLib.Error as error:
            if (not self.activation_attempted
                    and self.Gio.DBusError.get_remote_error(error) == "org.freedesktop.DBus.Error.NameHasNoOwner"):
                self.activation_attempted = True
                connection.call(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "StartServiceByName", self.GLib.Variant("(su)", (PORTAL_NAME, 0)),
                    self.GLib.VariantType.new("(u)"), self.Gio.DBusCallFlags.NONE,
                    self._remaining_ms(), self.cancellable, self._service_started)
            else:
                self._call_error(error)
        except ScreenshotPortalError as error:
            self._finish(error)
        except (ValueError, TypeError):
            self._finish(ScreenshotPortalError("Invalid desktop screenshot portal response"))

    def _owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        values = parameters.unpack()
        if len(values) == 3 and values[0] == PORTAL_NAME and values[2] != self.owner:
            self._finish(ScreenshotPortalError("Desktop screenshot portal closed during capture"))

    def _method_ready(self, connection, result):
        if self.finished:
            return
        try:
            values = connection.call_finish(result).unpack()
            if len(values) != 1 or values[0] != self.request_path:
                raise ScreenshotPortalError("Unexpected desktop screenshot request handle")
            self.method_returned = True
            if self.response_uri is not None:
                self._finish()
        except self.GLib.Error as error:
            self._call_error(error)
        except (ValueError, TypeError, ScreenshotPortalError):
            self._finish(ScreenshotPortalError("Unexpected desktop screenshot request handle"))

    def _response(self, _connection, sender, path, _interface, _signal, parameters):
        if self.finished or sender != self.owner or path != self.request_path:
            return
        try:
            values = parameters.unpack()
            if len(values) != 2 or type(values[0]) is not int or not isinstance(values[1], dict):
                raise ScreenshotPortalError("Invalid desktop screenshot portal response")
            code, result = values
            if code == 1:
                self._finish(ScreenshotPortalCancelled("Screen capture cancelled by the desktop"))
            elif code != 0:
                self._finish(ScreenshotPortalError("The desktop did not authorize this screen capture"))
            elif not isinstance(result.get("uri"), str):
                self._finish(ScreenshotPortalError("Desktop screenshot portal returned no image"))
            else:
                self.response_uri = result["uri"]
                if self.method_returned:
                    self._finish()
        except (ValueError, TypeError, ScreenshotPortalError):
            self._finish(ScreenshotPortalError("Invalid desktop screenshot portal response"))

    def run(self):
        source = self.GLib.timeout_source_new(25)
        source.set_callback(self._poll)
        source.attach(self.context)
        try:
            self.Gio.bus_get(self.Gio.BusType.SESSION, self.cancellable, self._bus_ready)
            self.loop.run()
            if self.error is not None:
                raise self.error
            if self.response_uri is None:
                raise ScreenshotPortalError("Desktop screenshot portal returned no image")
            return self.response_uri
        finally:
            source.destroy()
            self.cancellable.cancel()
            if self.connection is not None:
                for subscription in self.subscriptions:
                    self.connection.signal_unsubscribe(subscription)
                # Only close our predicted request, never an arbitrary handle
                # returned by a malformed reply. Calls are ordered on the bus.
                if self.error is not None and self.owner and self.request_path:
                    try:
                        self.connection.call_sync(
                            self.owner, self.request_path, REQUEST_INTERFACE, "Close", None, None,
                            self.Gio.DBusCallFlags.NONE, 500, None)
                    except self.GLib.Error:
                        pass


def _local_path(uri):
    if not isinstance(uri, str) or not uri or len(uri) > 8192:
        raise ScreenshotPortalError("Desktop screenshot portal returned an invalid image URI")
    try:
        parsed = urlsplit(uri)
        path = unquote(parsed.path, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise ScreenshotPortalError("Desktop screenshot portal returned an invalid image URI") from None
    if (parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}
            or parsed.query or parsed.fragment or not path.startswith("/") or "\x00" in path):
        raise ScreenshotPortalError("Desktop screenshot portal must return a local image file")
    return path


def _open_parent(path):
    if not os.path.isabs(path):
        raise ScreenshotPortalError("Screen capture requires an absolute output path")
    parts = path.split("/")
    if any(part in {".", ".."} for part in parts) or not parts[-1]:
        raise ScreenshotPortalError("Invalid screen capture file path")
    descriptor = os.open("/", os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            if part:
                next_descriptor = os.open(
                    part, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


class _BoundedPNG(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > MAX_IMAGE_BYTES:
            raise ScreenshotPortalError("Screen capture exceeds the 64 MiB image limit")
        return super().write(value)


def _copy_snapshot(uri, output_path, cancel_event, deadline):
    source_path = _local_path(uri)
    source_parent = output_parent = None
    temporary_name = None
    try:
        _check_deadline(cancel_event, deadline)
        source_parent, source_name = _open_parent(source_path)
        descriptor = os.open(source_name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=source_parent)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_IMAGE_BYTES:
                raise ScreenshotPortalError("Desktop screenshot must be a regular image no larger than 64 MiB")
            data = source.read(MAX_IMAGE_BYTES + 1)
            after = os.fstat(source.fileno())
            if (len(data) > MAX_IMAGE_BYTES or before.st_size != len(data)
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ScreenshotPortalError("Desktop screenshot changed while being read")
        _check_deadline(cancel_event, deadline)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if (image.format not in {"PNG", "JPEG", "WEBP"}
                        or not 0 < width <= MAX_IMAGE_EDGE or not 0 < height <= MAX_IMAGE_EDGE
                        or width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1):
                    raise ScreenshotPortalError("Desktop screenshot exceeds supported image dimensions")
                image.load()
                with Image.new("RGB", image.size, "white") as normalized:
                    with image.convert("RGBA") as rgba:
                        normalized.paste(rgba, mask=rgba.getchannel("A"))
                    with _BoundedPNG() as snapshot:
                        normalized.save(snapshot, format="PNG")
                        png = snapshot.getvalue()
        _check_deadline(cancel_event, deadline)
        output_parent, output_name = _open_parent(os.fspath(output_path))
        try:
            target = os.stat(output_name, dir_fd=output_parent, follow_symlinks=False)
        except FileNotFoundError:
            target = None
        if target is not None and (not stat.S_ISREG(target.st_mode) or target.st_uid != os.geteuid()
                                   or (target.st_dev, target.st_ino) == (before.st_dev, before.st_ino)):
            raise ScreenshotPortalError("Screen capture output is not a safe private file")
        temporary_name = ".linux_ai_capture_" + uuid.uuid4().hex + ".png"
        descriptor = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600, dir_fd=output_parent)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(png)
            destination.flush()
            os.fsync(destination.fileno())
        _check_deadline(cancel_event, deadline)
        os.replace(temporary_name, output_name, src_dir_fd=output_parent, dst_dir_fd=output_parent)
        temporary_name = None
    except ScreenshotPortalError:
        raise
    except (OSError, ValueError, TypeError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ScreenshotPortalError("Desktop screenshot could not be copied safely") from None
    finally:
        if temporary_name is not None and output_parent is not None:
            try:
                os.unlink(temporary_name, dir_fd=output_parent)
            except OSError:
                pass
        for descriptor in (source_parent, output_parent):
            if descriptor is not None:
                os.close(descriptor)


def capture_screenshot(output_path, cancel_event=None, timeout=120):
    """Request desktop approval and create a private PNG snapshot.

    The timeout includes service discovery and the permission dialog. Portal
    cancellation, denial, timeout and invalid image responses are definitive
    failures; callers must not substitute another capture backend for them.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Screen capture timeout must be positive and finite")
    deadline = time.monotonic() + timeout
    _check_deadline(cancel_event, deadline)
    Gio, GLib = _bindings()
    context = GLib.MainContext.new()
    context.push_thread_default()
    try:
        uri = _PortalRequest(Gio, GLib, context, cancel_event, deadline).run()
    finally:
        context.pop_thread_default()
    _copy_snapshot(uri, output_path, cancel_event, deadline)
