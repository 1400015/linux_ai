import struct

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, Gtk  # noqa: E402

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, OSError):
    HAS_LAYER_SHELL = False


def is_wayland(window):
    return not isinstance(window.get_display(), GdkX11.X11Display)


def _apply_x11_struts(gdk_window, edge, size):
    """Reserva espaço no ecrã via _NET_WM_STRUT_PARTIAL."""
    if not isinstance(gdk_window.get_display(), GdkX11.X11Display):
        return False
    display = gdk_window.get_display()
    screen = display.get_default_screen()
    screen_w = screen.get_width()
    screen_h = screen.get_height()
    strut = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    if edge == "right":
        strut[1] = size
        strut[5], strut[6] = 0, screen_h
    elif edge == "left":
        strut[0] = size
        strut[4], strut[5] = 0, screen_h
    elif edge == "top":
        strut[2] = size
        strut[8], strut[9] = 0, screen_w
    else:
        strut[3] = size
        strut[10], strut[11] = 0, screen_w
    atom = Gdk.Atom.intern("_NET_WM_STRUT_PARTIAL", False)
    cardinal = Gdk.Atom.intern("CARDINAL", False)
    data = struct.pack("=" + "l" * 12, *strut)
    Gdk.property_change(gdk_window, atom, cardinal, 32,
                        Gdk.PropMode.REPLACE, data, 12)
    return True


def apply_dock(window, edge, width):
    """Tenta fixar a janela ao ecrã e reservar espaço.

    Retorna o método usado: "layer-shell", "x11-struts" ou "window".
    """
    if is_wayland(window) and HAS_LAYER_SHELL:
        try:
            GtkLayerShell.init_window(window)
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
            edges = {"left": GtkLayerShell.Edge.LEFT,
                     "right": GtkLayerShell.Edge.RIGHT,
                     "top": GtkLayerShell.Edge.TOP,
                     "bottom": GtkLayerShell.Edge.BOTTOM}
            for name, e in edges.items():
                GtkLayerShell.set_anchor(window, e, name == edge)
            return "layer-shell"
        except Exception:
            pass
    window.set_type_hint(Gdk.WindowTypeHint.DOCK)
    window.stick()
    window.set_keep_above(True)
    gdk_window = window.get_window()
    if gdk_window and _apply_x11_struts(gdk_window, edge, width):
        return "x11-struts"
    return "window"


def apply_float(window):
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.set_keep_above(True)
    window.unstick()

