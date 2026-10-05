"""Desktop activation and optional, user-approved global shortcuts.

Nothing here reads selected text or injects input. Wayland shortcuts are
registered through the portal; X11 uses a separate X connection. The external
``linux-ai-assistant --show`` command remains the compositor-binding fallback.
"""

import ctypes
import ctypes.util
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)
APPLICATION_ID = 'io.github.linux_ai_assistant'
PORTAL_NAME = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'
PORTAL_INTERFACE = 'org.freedesktop.portal.GlobalShortcuts'


def _fallback():
    command = ('flatpak run io.github.linux_ai_assistant --show'
               if os.environ.get('FLATPAK_ID') == APPLICATION_ID else 'linux-ai-assistant --show')
    return 'Configure your desktop shortcut to run: ' + command


def _bindings():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gio, GLib, Gtk, Gdk
    return Gio, GLib, Gtk, Gdk


def validate_accelerator(accelerator):
    """Require a bounded GTK accelerator with a deliberate modifier."""
    if not isinstance(accelerator, str) or len(accelerator) > 128:
        raise ValueError('Invalid global shortcut')
    _gio, _glib, Gtk, Gdk = _bindings()
    key, modifiers = Gtk.accelerator_parse(accelerator)
    deliberate = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK |
                  Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
    if not key or not (modifiers & deliberate):
        raise ValueError('Use a shortcut containing Ctrl, Alt or Super')
    allowed = deliberate | Gdk.ModifierType.SHIFT_MASK
    if modifiers & ~allowed:
        raise ValueError('Unsupported shortcut modifier')
    return int(key), int(modifiers)


class DesktopActivation:
    """Keep one application per user session using the session D-Bus."""

    def __init__(self, show_callback, application_id=APPLICATION_ID):
        Gio, _glib, _gtk, _gdk = _bindings()
        self.application = Gio.Application.new(application_id, Gio.ApplicationFlags.FLAGS_NONE)
        self.application.connect('activate', lambda *_args: show_callback())

    def register(self):
        if not self.application.register(None):
            raise RuntimeError('Could not register desktop activation')
        # Gio otherwise silently becomes non-unique when no session bus exists.
        if self.application.get_dbus_connection() is None:
            raise RuntimeError('A session D-Bus is required to prevent duplicate application windows')
        return not self.application.get_is_remote()

    def activate(self):
        self.application.activate()

    def close(self):
        self.application.quit()


class GlobalShortcut:
    """Lifetime owner for one optional desktop shortcut."""

    def __init__(self, callback, status_changed=None):
        self.callback = callback
        self.status_changed = status_changed or (lambda _status: None)
        self.backend = None
        self.status = 'Global shortcut disabled'

    def _status(self, status):
        self.status = status
        self.status_changed(status)

    def configure(self, enabled, accelerator):
        self.close()
        if not enabled:
            self._status('Global shortcut disabled')
            return self.status
        try:
            validate_accelerator(accelerator)
            _gio, _glib, _gtk, Gdk = _bindings()
            display = Gdk.Display.get_default()
            display_type = type(display).__name__.lower() if display is not None else ''
            if 'wayland' in display_type or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
                backend = PortalShortcut(self.callback, self._status)
            elif 'x11' in display_type or (not display_type and os.environ.get('DISPLAY')):
                backend = X11Shortcut(self.callback, self._status)
            else:
                raise RuntimeError('No supported desktop display')
            self.backend = backend
            backend.start(accelerator)
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            self.close()
            logger.info('Global shortcut unavailable: %s', type(error).__name__)
            self._status('Global shortcut unavailable. ' + _fallback())
        return self.status

    def close(self):
        if self.backend is not None:
            self.backend.close()
            self.backend = None


class PortalShortcut:
    """GlobalShortcuts portal request/session lifecycle, entirely asynchronous."""

    def __init__(self, callback, status_changed, connection=None):
        self.Gio, self.GLib, _gtk, _gdk = _bindings()
        self.callback = callback
        self.status_changed = status_changed
        self.connection = connection
        self.cancellable = self.Gio.Cancellable()
        self.session = None
        self.closed = False
        self.subscriptions = set()
        self.pending_requests = set()
        self.accelerator = None

    def start(self, accelerator):
        self.accelerator = accelerator
        self.preferred_trigger = portal_trigger(accelerator)
        self.status_changed('Waiting for desktop shortcut approval')
        if self.connection is not None:
            self._create_session()
            return
        self.Gio.bus_get(self.Gio.BusType.SESSION, self.cancellable, self._bus_ready)

    def _bus_ready(self, _source, result):
        try:
            self.connection = self.Gio.bus_get_finish(result)
            if not self.closed:
                self._create_session()
        except self.GLib.Error:
            self._unavailable()

    def _unavailable(self):
        if not self.closed:
            self.close()
            self.status_changed('Global shortcut unavailable or permission denied. ' + _fallback())

    def _subscribe(self, interface, signal, path, callback):
        subscription = self.connection.signal_subscribe(
            PORTAL_NAME, interface, signal, path, None,
            self.Gio.DBusSignalFlags.NONE, callback)
        self.subscriptions.add(subscription)
        return subscription

    def _unsubscribe(self, subscription):
        if subscription in self.subscriptions:
            self.connection.signal_unsubscribe(subscription)
            self.subscriptions.remove(subscription)

    def _request(self, method, signature, args, complete):
        token = 'linux_ai_' + uuid.uuid4().hex
        options = args[-1]
        options['handle_token'] = self.GLib.Variant('s', token)
        sender = self.connection.get_unique_name().lstrip(':').replace('.', '_')
        request_path = PORTAL_PATH + '/request/' + sender + '/' + token
        self.pending_requests.add(request_path)
        holder = []

        def response(_connection, _sender, _path, _interface, _signal, parameters):
            self._unsubscribe(holder[0])
            self.pending_requests.discard(request_path)
            if self.closed:
                return
            code, values = parameters.unpack()
            if code != 0:
                self._unavailable()
                return
            complete(values)

        holder.append(self._subscribe('org.freedesktop.portal.Request', 'Response', request_path, response))

        def returned(connection, result):
            try:
                actual_path = connection.call_finish(result).unpack()[0]
                if actual_path != request_path:
                    # The portal contract promises the supplied handle token.
                    # Refuse an unobserved request rather than losing lifecycle control.
                    self.pending_requests.add(actual_path)
                    self._unavailable()
            except self.GLib.Error:
                self._unsubscribe(holder[0])
                self.pending_requests.discard(request_path)
                self._unavailable()

        self.connection.call(PORTAL_NAME, PORTAL_PATH, PORTAL_INTERFACE, method,
                             self.GLib.Variant(signature, tuple(args)), self.GLib.VariantType.new('(o)'),
                             self.Gio.DBusCallFlags.NONE, 10000, self.cancellable, returned)

    def _create_session(self):
        token = 'linux_ai_session_' + uuid.uuid4().hex
        sender = self.connection.get_unique_name().lstrip(':').replace('.', '_')
        # Remember the promised session handle before the asynchronous request.
        # Disabling while CreateSession completes can then close both handles.
        self.session = PORTAL_PATH + '/session/' + sender + '/' + token
        self._request('CreateSession', '(a{sv})', [{
            'session_handle_token': self.GLib.Variant('s', token)
        }], self._session_ready)

    def _session_ready(self, values):
        session = values.get('session_handle')
        if not isinstance(session, str) or session != self.session:
            self._unavailable()
            return
        self.session = session
        self._subscribe(PORTAL_INTERFACE, 'Activated', PORTAL_PATH, self._activated)
        self._subscribe('org.freedesktop.portal.Session', 'Closed', session,
                        lambda *_args: self._unavailable())
        shortcuts = [('show-assistant', {
            'description': self.GLib.Variant('s', 'Show Linux AI Assistant'),
            'preferred_trigger': self.GLib.Variant('s', self.preferred_trigger),
        })]
        self._request('BindShortcuts', '(oa(sa{sv})sa{sv})',
                      [session, shortcuts, '', {}], self._bound)

    def _bound(self, values):
        shortcuts = values.get('shortcuts', [])
        if not any(isinstance(item, (tuple, list)) and len(item) == 2 and
                   item[0] == 'show-assistant' for item in shortcuts):
            self._unavailable()
            return
        self.status_changed('Global shortcut active through the desktop portal')

    def _activated(self, _connection, _sender, _path, _interface, _signal, parameters):
        if self.closed:
            return
        values = parameters.unpack()
        if len(values) >= 2 and values[0] == self.session and values[1] == 'show-assistant':
            self.callback()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancellable.cancel()
        if self.connection is not None:
            for subscription in tuple(self.subscriptions):
                self._unsubscribe(subscription)
            for path in self.pending_requests:
                self._close_path(path, 'org.freedesktop.portal.Request')
            self.pending_requests.clear()
            if self.session is not None:
                self._close_path(self.session, 'org.freedesktop.portal.Session')
        self.session = None

    def _close_path(self, path, interface):
        self.connection.call(PORTAL_NAME, path, interface, 'Close', None, None,
                             self.Gio.DBusCallFlags.NONE, 1000, None, None)


def portal_trigger(accelerator):
    """Convert GTK notation to the XDG portal's preferred trigger syntax."""
    _gio, _glib, Gtk, Gdk = _bindings()
    key, modifiers = validate_accelerator(accelerator)
    parts = []
    for mask, name in ((Gdk.ModifierType.CONTROL_MASK, 'CTRL'),
                       (Gdk.ModifierType.MOD1_MASK, 'ALT'),
                       (Gdk.ModifierType.SHIFT_MASK, 'SHIFT'),
                       (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK, 'LOGO')):
        if modifiers & mask:
            parts.append(name)
    key_name = Gdk.keyval_name(key)
    if not key_name or not re.fullmatch(r'[A-Za-z0-9_]+', key_name):
        raise ValueError('Unsupported portal key name')
    parts.append(key_name)
    return '+'.join(parts)


class _XKeyEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                ('window', ctypes.c_ulong), ('root', ctypes.c_ulong),
                ('subwindow', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('x', ctypes.c_int), ('y', ctypes.c_int),
                ('x_root', ctypes.c_int), ('y_root', ctypes.c_int),
                ('state', ctypes.c_uint), ('keycode', ctypes.c_uint),
                ('same_screen', ctypes.c_int)]


class _XEvent(ctypes.Union):
    _fields_ = [('type', ctypes.c_int), ('key', _XKeyEvent), ('pad', ctypes.c_long * 24)]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('display', ctypes.c_void_p),
                ('resourceid', ctypes.c_ulong), ('serial', ctypes.c_ulong),
                ('error_code', ctypes.c_ubyte), ('request_code', ctypes.c_ubyte),
                ('minor_code', ctypes.c_ubyte)]


class _XModifierKeymap(ctypes.Structure):
    _fields_ = [('max_keypermod', ctypes.c_int),
                ('modifiermap', ctypes.POINTER(ctypes.c_ubyte))]


class X11Shortcut:
    """Optional X11 passive key grab, released when disabled or the app exits."""

    def __init__(self, callback, status_changed):
        self.callback = callback
        self.status_changed = status_changed
        self.display = None
        self.lib = None
        self.source = None
        self.keycode = None
        self.masks = []

    def start(self, accelerator):
        _gio, self.GLib, _gtk, Gdk = _bindings()
        key, modifiers = validate_accelerator(accelerator)
        library = ctypes.util.find_library('X11')
        if not library:
            raise RuntimeError('X11 client library unavailable')
        self.lib = ctypes.CDLL(library)
        definitions = {
            'XOpenDisplay': ([ctypes.c_char_p], ctypes.c_void_p),
            'XDefaultRootWindow': ([ctypes.c_void_p], ctypes.c_ulong),
            'XKeysymToKeycode': ([ctypes.c_void_p, ctypes.c_ulong], ctypes.c_ubyte),
            'XGrabKey': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                          ctypes.c_int, ctypes.c_int, ctypes.c_int], ctypes.c_int),
            'XUngrabKey': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong], ctypes.c_int),
            'XSync': ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            'XConnectionNumber': ([ctypes.c_void_p], ctypes.c_int),
            'XPending': ([ctypes.c_void_p], ctypes.c_int),
            'XNextEvent': ([ctypes.c_void_p, ctypes.POINTER(_XEvent)], ctypes.c_int),
            'XCloseDisplay': ([ctypes.c_void_p], ctypes.c_int),
            'XSetErrorHandler': ([ctypes.c_void_p], ctypes.c_void_p),
            'XGetModifierMapping': ([ctypes.c_void_p], ctypes.POINTER(_XModifierKeymap)),
            'XFreeModifiermap': ([ctypes.POINTER(_XModifierKeymap)], ctypes.c_int),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.lib, name)
            function.argtypes = arguments
            function.restype = result
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('X11 display unavailable')
        self.root = self.lib.XDefaultRootWindow(self.display)
        self.keycode = self.lib.XKeysymToKeycode(self.display, key)
        if not self.keycode:
            raise ValueError('Shortcut key is absent from the X11 keymap')
        self.modifiers = modifiers & (int(Gdk.ModifierType.SHIFT_MASK) |
                                      int(Gdk.ModifierType.CONTROL_MASK) | int(Gdk.ModifierType.MOD1_MASK))
        mapping = self.lib.XGetModifierMapping(self.display)
        if not mapping:
            raise RuntimeError('Could not inspect X11 modifier mapping')
        try:
            def modifier_mask(keysym):
                keycode = self.lib.XKeysymToKeycode(self.display, keysym)
                width = mapping.contents.max_keypermod
                return sum(1 << modifier for modifier in range(8)
                           if keycode and any(mapping.contents.modifiermap[modifier * width + slot] == keycode
                                              for slot in range(width)))

            for modifier, keysym in ((Gdk.ModifierType.SUPER_MASK, 0xffeb),
                                     (Gdk.ModifierType.META_MASK, 0xffe7)):
                if modifiers & int(modifier):
                    mask = modifier_mask(keysym)
                    if not mask:
                        raise ValueError('Shortcut modifier is absent from the X11 keymap')
                    self.modifiers |= mask
            number_lock = modifier_mask(0xff7f)
            self.masks = sorted({self.modifiers | lock for lock in (0, 2, number_lock, number_lock | 2)})
        finally:
            self.lib.XFreeModifiermap(mapping)
        errors = []
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))
        previous = None

        def error_handler(display, event):
            if display == self.display:
                errors.append(event.contents.error_code)
                return 0
            if previous:
                return callback_type(previous)(display, event)
            return 0

        handler = callback_type(error_handler)
        previous = self.lib.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
        try:
            for mask in self.masks:
                self.lib.XGrabKey(self.display, self.keycode, mask, self.root, False, 1, 1)
            self.lib.XSync(self.display, False)
        finally:
            self.lib.XSetErrorHandler(previous)
        if errors:
            raise RuntimeError('Shortcut is already used or could not be grabbed')
        self.source = self.GLib.io_add_watch(self.lib.XConnectionNumber(self.display),
                                             self.GLib.PRIORITY_DEFAULT,
                                             self.GLib.IO_IN | self.GLib.IO_HUP | self.GLib.IO_ERR,
                                             self._events)
        self.status_changed('Global shortcut active on X11')

    def _events(self, _descriptor, condition):
        if condition & (self.GLib.IO_HUP | self.GLib.IO_ERR):
            self.source = None
            self.close()
            self.status_changed('Global shortcut disconnected. ' + _fallback())
            return False
        event = _XEvent()
        while self.lib.XPending(self.display):
            self.lib.XNextEvent(self.display, ctypes.byref(event))
            if event.type == 2 and event.key.keycode == self.keycode:
                self.callback()
        return True

    def close(self):
        if self.source is not None:
            self.GLib.source_remove(self.source)
            self.source = None
        if self.display is not None:
            for mask in self.masks:
                self.lib.XUngrabKey(self.display, self.keycode, mask, self.root)
            self.lib.XCloseDisplay(self.display)
            self.display = None
