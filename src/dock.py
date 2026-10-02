import logging
import struct

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk  # noqa: E402

# GdkX11 is only present on X11-capable builds; import it defensively so
# importing this module (and therefore main_window) does not fail on a
# non-X11 GDK backend.
try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: E402
    HAS_GDK_X11 = True
except (ImportError, ValueError):
    GdkX11 = None
    HAS_GDK_X11 = False

logger = logging.getLogger(__name__)

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, OSError):
    HAS_LAYER_SHELL = False


def is_wayland(window):
    if not HAS_GDK_X11:
        return True
    return not isinstance(window.get_display(), GdkX11.X11Display)


def _apply_x11_struts(gdk_window, edge, size):
    """Reserve screen space via _NET_WM_STRUT_PARTIAL."""
    if not HAS_GDK_X11 or not isinstance(gdk_window.get_display(), GdkX11.X11Display):
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


def _layer_keyboard_mode():
    """Return a focusable mode that does not take exclusive keyboard focus."""
    modes = getattr(GtkLayerShell, "KeyboardMode", None)
    on_demand = getattr(modes, "ON_DEMAND", None)
    protocol_version = getattr(GtkLayerShell, "get_protocol_version", None)
    # Older protocols/libraries offer only NONE or exclusive keyboard focus.
    # A layer window cannot be made an ordinary toplevel again on undock, so
    # use the ordinary-window fallback before initializing such a surface.
    if (on_demand is not None
            and callable(getattr(GtkLayerShell, "set_keyboard_mode", None))
            and callable(protocol_version) and protocol_version() >= 4):
        return on_demand
    return None


def apply_dock(window, edge, width):
    """Try to pin the window to the screen and reserve space.

    Returns the method used: "layer-shell", "x11-struts" or "window".
    Idempotent: calling it again just re-applies the same edge.
    """
    if is_wayland(window) and HAS_LAYER_SHELL:
        try:
            keyboard_mode = _layer_keyboard_mode()
            if keyboard_mode is not None:
                # Reapplying does not initialize a second layer surface.
                GtkLayerShell.init_for_window(window)
                GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
                edges = {"left": GtkLayerShell.Edge.LEFT,
                         "right": GtkLayerShell.Edge.RIGHT,
                         "top": GtkLayerShell.Edge.TOP,
                         "bottom": GtkLayerShell.Edge.BOTTOM}
                for name, e in edges.items():
                    GtkLayerShell.set_anchor(window, e, name == edge)
                # Initialization after realize may leave an ordinary window.
                if GtkLayerShell.is_layer_window(window):
                    GtkLayerShell.set_keyboard_mode(window, keyboard_mode)
                    return "layer-shell"
                logger.warning("Layer-shell init had no effect; falling back")
            else:
                logger.info("Layer-shell lacks on-demand keyboard focus; using an ordinary window")
        except Exception as e:
            # Log instead of a bare `pass`: silently swallowing this made
            # Wayland dock failures invisible.
            logger.warning(f"Layer-shell dock failed, falling back: {e}")

    # Caminho X11: reservar o espaço E mover/ancorar a janela ao bordo —
    # o strut só reserva; sem o move, a janela ficava onde estava. O strut
    # usa o tamanho REAL da janela, não a largura guardada na config (que
    # diverge se o utilizador redimensionou).
    window.set_type_hint(Gdk.WindowTypeHint.DOCK)
    window.stick()
    window.set_keep_above(True)
    try:
        real_w, real_h = window.get_size()
        screen = window.get_screen()
        screen_w, screen_h = screen.get_width(), screen.get_height()
        if edge == "right":
            window.move(max(screen_w - real_w, 0), 0)
            size = real_w
        elif edge == "left":
            window.move(0, 0)
            size = real_w
        elif edge == "top":
            window.move(0, 0)
            size = real_h
        else:
            window.move(0, max(screen_h - real_h, 0))
            size = real_h
    except Exception as e:
        logger.warning(f"Could not position docked window; using configured size: {e}")
        size = width
    gdk_window = window.get_window()
    if gdk_window and _apply_x11_struts(gdk_window, edge, size):
        return "x11-struts"
    return "window"


def apply_float_button(window, edge):
    """Anchor the helper button on the configured screen edge.

    Returns True only when gtk-layer-shell actually owns the window. The
    caller falls back to ordinary placement when this returns False: a
    missing symbol, a rejected protocol or an init that does not stick
    must not skip that placement.
    """
    if not is_wayland(window) or not HAS_LAYER_SHELL:
        return False
    try:
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_namespace(window, "linux-ai-float-button")
        edges = {"left": GtkLayerShell.Edge.LEFT,
                 "right": GtkLayerShell.Edge.RIGHT,
                 "top": GtkLayerShell.Edge.TOP,
                 "bottom": GtkLayerShell.Edge.BOTTOM}
        anchor = edges.get(edge, GtkLayerShell.Edge.RIGHT)
        GtkLayerShell.set_anchor(window, anchor, True)
        GtkLayerShell.set_margin(window, anchor, 12)
        if GtkLayerShell.is_layer_window(window):
            return True
        logger.warning("Layer-shell init had no effect on the float button; falling back")
    except Exception as e:
        logger.warning(f"Layer-shell float button failed, falling back: {e}")
    return False


def apply_float(window, always_on_top: bool = True):
    """Return the window to floating mode.

    Must undo everything apply_dock() did: layer-shell anchors, the X11
    strut reservation and the DOCK type hint - otherwise the screen stays
    reserved (or the window stays anchored) after undocking.

    `always_on_top` vem da config (app.always_on_top): o caminho antigo
    forçava keep_above(True)+unstick(), contradizendo o estado flutuante
    inicial (stick + keep_above configurável).
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
    window.set_keep_above(bool(always_on_top))
    window.stick()

    # Release the X11 strut (a zeroed _NET_WM_STRUT_PARTIAL) so other
    # windows can use the reserved space again.
    gdk_window = window.get_window()
    if gdk_window and HAS_GDK_X11 and isinstance(gdk_window.get_display(), GdkX11.X11Display):
        try:
            empty = [0] * 12
            atom = Gdk.Atom.intern("_NET_WM_STRUT_PARTIAL", False)
            cardinal = Gdk.Atom.intern("CARDINAL", False)
            data = struct.pack("=" + "l" * 12, *empty)
            Gdk.property_change(gdk_window, atom, cardinal, 32,
                                Gdk.PropMode.REPLACE, data, 12)
        except Exception as e:
            logger.warning(f"Could not release X11 strut: {e}")
