"""Desktop identity and the bundled icon, without GUI imports at module load."""

import os
from pathlib import Path
import sys


ICON_NAME = 'io.github.linux_ai_assistant'
NATIVE_DESKTOP_ID = 'linux-ai-assistant'


def desktop_id():
    return ICON_NAME if os.environ.get('FLATPAK_ID') == ICON_NAME else NATIVE_DESKTOP_ID


def icon_path():
    """Find the shipped SVG in a checkout, installed wheel or Flatpak."""
    relative = Path('share/icons/hicolor/scalable/apps') / (ICON_NAME + '.svg')
    candidates = [Path(__file__).resolve().parents[1] / 'assets' / (ICON_NAME + '.svg'),
                  Path(sys.prefix) / relative]
    if os.environ.get('FLATPAK_ID') == ICON_NAME:
        candidates.append(Path('/app') / relative)
    return next((path for path in candidates if path.is_file()), None)


def configure_desktop_identity():
    """Set the native/Flatpak window identity before initializing GTK."""
    from gi.repository import Gdk, GLib
    GLib.set_prgname(desktop_id())
    GLib.set_application_name('Linux AI Assistant')
    Gdk.set_program_class(desktop_id())


def configure_application_icon():
    """Let GTK resolve the icon even when running directly from an extracted ZIP."""
    from gi.repository import Gtk
    theme = Gtk.IconTheme.get_default()
    path = icon_path()
    if theme is not None and path is not None:
        directory = str(path.parent)
        if directory not in theme.get_search_path():
            theme.append_search_path(directory)
    Gtk.Window.set_default_icon_name(ICON_NAME)
    return theme
