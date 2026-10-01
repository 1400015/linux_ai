import logging
import struct

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11  # noqa: E402

logger = logging.getLogger(__name__)

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, OSError):
    HAS_LAYER_SHELL = False


def is_wayland(window):
    return not isinstance(window.get_display(), GdkX11.X11Display)


def _apply_x11_struts(gdk_window, edge, size):
    """Reserve screen space via _NET_WM_STRUT_PARTIAL."""
    if not isinstance(gdk_window.get_display(), GdkX11.X11Display):
        return False
    display = gdk_window.get_display()
    screen = display.get_default_screen()
    screen_w = screen.get_width()
    screen_h = screen.get_height()
    strut = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    if edge == "right":
        strut[1] = size
        strut[6], strut[7] = 0, screen_h - 1
    elif edge == "left":
        strut[0] = size
        strut[4], strut[5] = 0, screen_h - 1
    elif edge == "top":
        strut[2] = size
        strut[8], strut[9] = 0, screen_w - 1
    else:
        strut[3] = size
        strut[10], strut[11] = 0, screen_w - 1
    atom = Gdk.Atom.intern("_NET_WM_STRUT_PARTIAL", False)
    cardinal = Gdk.Atom.intern("CARDINAL", False)
    data = struct.pack("=" + "l" * 12, *strut)
    Gdk.property_change(gdk_window, atom, cardinal, 32,
                        Gdk.PropMode.REPLACE, data, 12)
    return True


def apply_dock(window, edge, width):
    """Try to pin the window to the screen and reserve space.

    Returns the method used: "layer-shell", "x11-struts" or "window".
    Idempotent: calling it again just re-applies the same edge.
    """
    if is_wayland(window) and HAS_LAYER_SHELL:
        try:
            # init_window() is safe to call more than once (it is a no-op
            # after the first call), so re-docking does not stack anchors.
            GtkLayerShell.init_window(window)
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
            edges = {"left": GtkLayerShell.Edge.LEFT,
                     "right": GtkLayerShell.Edge.RIGHT,
                     "top": GtkLayerShell.Edge.TOP,
                     "bottom": GtkLayerShell.Edge.BOTTOM}
            for name, e in edges.items():
                GtkLayerShell.set_anchor(window, e, name == edge)
            return "layer-shell"
        except Exception as e:
            # Log instead of a bare `pass`: silently swallowing this made
            # Wayland dock failures invisible.
            logger.warning(f"Layer-shell dock failed, falling back: {e}")
    window.set_type_hint(Gdk.WindowTypeHint.DOCK)
    window.stick()
    window.set_keep_above(True)
    gdk_window = window.get_window()
    if gdk_window and _apply_x11_struts(gdk_window, edge, width):
        return "x11-struts"
    return "window"


def apply_float(window):
    """Return the window to floating mode.

    Must undo everything apply_dock() did: layer-shell anchors, the X11
    strut reservation and the DOCK type hint - otherwise the screen stays
    reserved (or the window stays anchored) after undocking.
    """
    if HAS_LAYER_SHELL:
        try:
            if GtkLayerShell.is_layer_window(window):
                # Anchors off + strut released (GtkLayerShell tracks the
                # window's own margins, so reset them too).
                for e in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                          GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
                    GtkLayerShell.set_anchor(window, e, False)
                for m in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                          GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
                    GtkLayerShell.set_margin(window, m, 0)
                GtkLayerShell.set_exclusive_zone(window, -1)
        except Exception as e:
            logger.warning(f"Could not clear layer-shell state: {e}")

    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.set_keep_above(True)
    window.unstick()

    # Release the X11 strut (a zeroed _NET_WM_STRUT_PARTIAL) so other
    # windows can use the reserved space again.
    gdk_window = window.get_window()
    if gdk_window and isinstance(gdk_window.get_display(), GdkX11.X11Display):
        try:
            empty = [0] * 12
            atom = Gdk.Atom.intern("_NET_WM_STRUT_PARTIAL", False)
            cardinal = Gdk.Atom.intern("CARDINAL", False)
            data = struct.pack("=" + "l" * 12, *empty)
            Gdk.property_change(gdk_window, atom, cardinal, 32,
                                Gdk.PropMode.REPLACE, data, 12)
        except Exception as e:
            logger.warning(f"Could not release X11 strut: {e}")

