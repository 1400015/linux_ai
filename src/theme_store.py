"""Single owner for theme file names, locations and user-theme writes.

Themes live in two places: the bundled ``themes/`` directory shipped with the
application (read-only, part of the package) and the user's
``~/.config/linux_ai_assistant/themes`` (writable, created on demand).

Any code that turns a *name* into a *path*, or decides whether a name may be
created or deleted, belongs here: the settings dialog, the main window and
ConfigManager all used to keep their own copy of the path and of the name
rules, and the "built-in" list in the window was hardcoded, so a bundled theme
added later would have been deletable (and its removal failed silently).
"""

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .theme_style import valid_color

logger = logging.getLogger(__name__)

# A theme name becomes a file name. Anything outside this simple identifier
# could escape the themes directories ("../../etc/some.json"), so the rule is
# enforced here for every reader and writer, not only for the dialog that
# happens to create files.
THEME_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Colour keys a theme file may carry. Every value must be a hex colour that
# GTK3's CSS parser accepts (theme_style.valid_color); rejecting them here
# keeps invalid values out of the file instead of relying on render-time
# fallbacks to hide them.
COLOR_KEYS = ("background", "text", "accent", "secondary", "tertiary")

# The three colours rendering reads from every theme; the dialog writes them
# and ConfigManager falls back to them one by one, so a document without them
# is not a usable theme.
REQUIRED_COLOR_KEYS = ("background", "text", "accent")


def bundled_themes_dir() -> Path:
    """Return the read-only themes shipped with the application.

    Source tree first (running from a checkout), then the installed data
    directory written by ``[tool.setuptools.data-files]``.
    """
    source_tree = Path(__file__).parent.parent / "themes"
    if source_tree.is_dir():
        return source_tree
    return Path(sys.prefix) / "share" / "linux-ai-assistant" / "themes"


def user_themes_dir() -> Path:
    """Return the user's writable themes directory (not created).

    Resolved on every call so ``Path.home()`` is read at use time; tests patch
    it and a cached path would ignore their HOME.
    """
    return Path.home() / ".config" / "linux_ai_assistant" / "themes"


def validate_theme_name(name) -> str:
    """Return `name` when it is a safe file-name identifier, else raise."""
    if not isinstance(name, str) or not THEME_NAME_RE.match(name):
        raise ValueError("invalid theme name: {!r}".format(name))
    return name


def user_theme_path(name: str) -> Path:
    """Return the user's file for `name`; the name is validated first."""
    return user_themes_dir() / (validate_theme_name(name) + ".json")


def list_user_themes() -> List[str]:
    """Return the names of the themes written by the user, sorted."""
    directory = user_themes_dir()
    if not directory.is_dir():
        return []
    return sorted(theme_file.stem for theme_file in directory.glob("*.json"))


def list_bundled_themes(bundled_dir: Optional[Path] = None) -> List[str]:
    """Return the names shipped with the application, sorted.

    Derived from the directory itself, never from a hardcoded list: a new
    bundled theme has to be protected without touching this module.
    """
    directory = bundled_themes_dir() if bundled_dir is None else Path(bundled_dir)
    if not directory.is_dir():
        return []
    return sorted(theme_file.stem for theme_file in directory.glob("*.json"))


def is_builtin(name, bundled_dir: Optional[Path] = None) -> bool:
    """Return True when `name` ships with the application."""
    if not isinstance(name, str) or not THEME_NAME_RE.match(name):
        return False
    return name in list_bundled_themes(bundled_dir)


def validate_theme(theme) -> Dict:
    """Return `theme` when it is a well-formed theme document, else raise.

    Only the shape and the colour values are checked: description and ui
    blocks stay free-form, and rendering keeps applying `theme_style`'s
    fallbacks to whatever a hand-edited file contains.
    """
    if not isinstance(theme, dict):
        raise ValueError("theme must be a JSON object")
    validate_theme_name(theme.get("name"))
    colors = theme.get("colors")
    if not isinstance(colors, dict):
        raise ValueError("theme 'colors' must be an object")
    for key in REQUIRED_COLOR_KEYS:
        if key not in colors:
            raise ValueError("theme colour is missing: {!r}".format(key))
    for key, value in colors.items():
        if key not in COLOR_KEYS:
            raise ValueError("unknown theme colour: {!r}".format(key))
        if not valid_color(value):
            raise ValueError("invalid {} colour: {!r} (expected #rgb or #rrggbb)".format(key, value))
    return theme


def save_theme(theme: Dict) -> Path:
    """Write `theme` into the user's themes directory and return its path.

    The file is replaced atomically and created with mode 0600: an interrupted
    write must never leave a truncated theme behind, and a theme file may
    contain values the user pasted from somewhere else.
    """
    validate_theme(theme)
    target = user_theme_path(theme["name"])
    directory = user_themes_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkstemp creates an exclusive 0600 file in the destination directory, so
    # the rename is atomic and no predictable temporary name is reused.
    descriptor, temporary = tempfile.mkstemp(dir=str(directory), prefix=".theme-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(theme, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(target))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    logger.debug("Theme saved: %s", target)
    return target


def remove_theme(name: str, bundled_dir: Optional[Path] = None):
    """Delete the user's file for `name`; return its path, or None if absent.

    Built-in themes are refused and the target is resolved before unlinking,
    so a name that somehow escaped the identifier check still cannot delete
    anything outside the user's themes directory.
    """
    validate_theme_name(name)
    if is_builtin(name, bundled_dir):
        raise ValueError("built-in theme cannot be removed: {}".format(name))
    directory = user_themes_dir()
    target = directory / (name + ".json")
    resolved = target.resolve()
    if resolved.parent != directory.resolve():
        raise ValueError("refusing to delete outside the themes directory: {}".format(resolved))
    if not resolved.exists():
        return None
    resolved.unlink()
    logger.debug("Theme removed: %s", resolved)
    return resolved
