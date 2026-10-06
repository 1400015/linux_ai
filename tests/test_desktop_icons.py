"""The shipped icon and window identity must work without an installer."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import desktop_icons


class TestDesktopIconPaths(unittest.TestCase):
    def test_checkout_and_installed_wheel_resolve_the_bundled_icon(self):
        checkout_icon = desktop_icons.icon_path()
        self.assertEqual(checkout_icon.name, desktop_icons.ICON_NAME + '.svg')
        self.assertEqual(checkout_icon.parent.name, 'assets')
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            icon = prefix / 'share/icons/hicolor/scalable/apps' / checkout_icon.name
            icon.parent.mkdir(parents=True)
            icon.write_bytes(checkout_icon.read_bytes())
            with patch.object(desktop_icons, '__file__', str(prefix / 'site-packages/src/desktop_icons.py')):
                with patch.object(sys, 'prefix', str(prefix)):
                    self.assertEqual(desktop_icons.icon_path(), icon)

    def test_desktop_identity_distinguishes_native_and_flatpak_launchers(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(desktop_icons.desktop_id(), 'linux-ai-assistant')
        with patch.dict(os.environ, {'FLATPAK_ID': desktop_icons.ICON_NAME}):
            self.assertEqual(desktop_icons.desktop_id(), desktop_icons.ICON_NAME)


class TestGtkDesktopIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        if not Gtk.init_check()[0]:
            raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
        cls.Gtk = Gtk

    def test_shipped_svg_is_loadable_at_small_and_large_icon_sizes(self):
        theme = desktop_icons.configure_application_icon()
        for size in (24, 48, 128):
            with self.subTest(size=size):
                pixbuf = theme.load_icon(desktop_icons.ICON_NAME, size, 0)
                self.assertEqual((pixbuf.get_width(), pixbuf.get_height()), (size, size))
                self.assertTrue(pixbuf.get_has_alpha())
        self.assertEqual(self.Gtk.Window.get_default_icon_name(), desktop_icons.ICON_NAME)

    def test_window_identity_matches_native_and_flatpak_desktop_entries(self):
        # A fresh process is needed: GDK snapshots this identity at startup.
        program = '''
import ctypes
import ctypes.util
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkX11
from src.desktop_icons import configure_desktop_identity, desktop_id
configure_desktop_identity()
assert Gtk.init_check()[0]
assert GLib.get_prgname() == desktop_id()
assert Gdk.get_program_class() == desktop_id()
window = Gtk.Window()
window.realize()
native = window.get_window()
if isinstance(native, GdkX11.X11Window):
    Gdk.flush()
    x11 = ctypes.CDLL(ctypes.util.find_library('X11'))
    class ClassHint(ctypes.Structure):
        _fields_ = [('name', ctypes.c_void_p), ('group', ctypes.c_void_p)]
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ClassHint)]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    assert display
    hint = ClassHint()
    try:
        assert x11.XGetClassHint(display, native.get_xid(), ctypes.byref(hint))
        actual = (ctypes.string_at(hint.name).decode(), ctypes.string_at(hint.group).decode())
        assert actual == (desktop_id(), desktop_id()), actual
    finally:
        if hint.name:
            x11.XFree(hint.name)
        if hint.group:
            x11.XFree(hint.group)
        x11.XCloseDisplay(display)
window.destroy()
'''
        for flatpak in ('', desktop_icons.ICON_NAME):
            with self.subTest(flatpak=bool(flatpak)):
                environment = dict(os.environ, FLATPAK_ID=flatpak)
                result = subprocess.run([sys.executable, '-c', program], env=environment,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
