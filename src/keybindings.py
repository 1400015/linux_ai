"""GTK-free keybinding management: parse, validate, and resolve shortcuts.

The GTK main window currently hardcodes its accelerators in
``_setup_keybindings``. This module owns the data model and validation so
the CLI, the settings dialog, and the window all share one source of truth.

A keybinding maps an *action name* (e.g. ``"send"``, ``"clear_input"``) to a
GTK accelerator string (e.g. ``"<Control>Return"``, ``"Escape"``). The
accelerator format is GTK's: zero or more modifiers in angle brackets
(``<Control>``, ``<Shift>``, ``<Alt>``, ``<Super>``, ``<Meta>``, ``<Primary>``)
followed by a key name (``Return``, ``Escape``, ``e``, ``F1``, ``space``, ...).

All functions here are pure; none import GTK. The GTK-specific parsing
(``Gtk.accelerator_parse``) happens in the window layer, which calls
``parse_accelerator`` to validate first and only then hands the string to GTK.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Known GTK modifier tokens (case-insensitive). <Primary> is an alias for
# <Control> in GTK3; both are accepted.
MODIFIERS = frozenset({
    "<control>", "<ctrl>", "<primary>",
    "<shift>",
    "<alt>", "<mod1>",
    "<super>", "<mod4>",
    "<meta>", "<mod3>",
    "<hyper>",
})

# Action names the main window knows about. The set is open: a user config
# may define extra actions, but the window only dispatches the ones it
# implements. Listing them here gives the CLI and tests a canonical registry.
BUILTIN_ACTIONS = (
    "send",            # Ctrl+Enter — send the message
    "clear_input",     # Escape — clear the input field
    "toggle_expert",   # Ctrl+E — toggle expert mode
    "close_window",    # Ctrl+Q — close the window
    "capture_screen",  # Ctrl+S — capture the screen
    "focus_input",     # Ctrl+L — focus the input entry
    "new_session",     # Ctrl+N — start a new conversation
    "toggle_history",  # Ctrl+H — show/hide history panel
)

# Default accelerator for each builtin action. These match the hardcoded
# values previously in main_window._setup_keybindings, plus two new ones
# (focus_input, new_session, toggle_history) that the window will adopt.
DEFAULT_BINDINGS: Dict[str, str] = {
    "send": "<Control>Return",
    "clear_input": "Escape",
    "toggle_expert": "<Control>e",
    "close_window": "<Control>q",
    "capture_screen": "<Control>s",
    "focus_input": "<Control>l",
    "new_session": "<Control>n",
    "toggle_history": "<Control>h",
}

# Accelerator grammar: zero or more <Modifier> tokens followed by a key name.
# Key names are alphanumeric or one of the named keys GTK recognises.
_ACCEL_RE = re.compile(
    r"^(?:<[^>]+>)*"          # zero or more <Modifier> tokens
    r"[A-Za-z0-9_]+$"         # key name (letters, digits, underscore)
)

# Key names that are always valid (a subset of GDK_KEY_* names GTK accepts).
_NAMED_KEYS = frozenset({
    "Return", "Enter", "Escape", "Tab", "BackSpace", "Delete", "Insert",
    "Home", "End", "Page_Up", "Page_Down", "Left", "Right", "Up", "Down",
    "space", "KP_Space", "F1", "F2", "F3", "F4", "F5", "F6",
    "F7", "F8", "F9", "F10", "F11", "F12",
    "plus", "minus", "comma", "period", "slash", "semicolon",
    "apostrophe", "bracketleft", "bracketright", "backslash",
    "grave", "equal",
})


class KeybindingError(Exception):
    """Raised when a keybinding definition is invalid."""


@dataclass(frozen=True)
class Keybinding:
    """A single action -> accelerator mapping."""

    action: str
    accelerator: str

    def to_dict(self) -> Dict[str, str]:
        return {self.action: self.accelerator}


@dataclass
class KeybindingSet:
    """An ordered collection of keybindings with lookup helpers."""

    bindings: Dict[str, str] = field(default_factory=dict)

    def get(self, action: str, default: Optional[str] = None) -> Optional[str]:
        return self.bindings.get(action, default)

    def set(self, action: str, accelerator: str) -> None:
        validate_action_name(action)
        validate_accelerator(accelerator)
        self.bindings[action] = accelerator

    def remove(self, action: str) -> bool:
        if action in self.bindings:
            del self.bindings[action]
            return True
        return False

    def actions(self) -> List[str]:
        return sorted(self.bindings.keys())

    def to_dict(self) -> Dict[str, str]:
        return dict(self.bindings)

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "KeybindingSet":
        kbs = cls()
        for action, accelerator in data.items():
            kbs.set(action, accelerator)
        return kbs

    @classmethod
    def defaults(cls) -> "KeybindingSet":
        return cls.from_dict(DEFAULT_BINDINGS)


def validate_action_name(action: str) -> str:
    """Return `action` when it is a safe identifier, else raise."""
    if not isinstance(action, str) or not action:
        raise KeybindingError("action name must be a non-empty string")
    if not re.match(r"^[a-z][a-z0-9_]*$", action):
        raise KeybindingError(
            "invalid action name: {!r} (expected lowercase letters, digits, underscores, "
            "starting with a letter)".format(action)
        )
    if len(action) > 64:
        raise KeybindingError("action name too long (max 64 chars)")
    return action


def validate_accelerator(accelerator: str) -> str:
    """Return `accelerator` when it is a syntactically valid GTK accelerator.

    This checks the *shape* (modifiers in angle brackets + a key name) but
    does not verify that GTK recognises the key name — that requires GTK and
    happens at registration time. The check is deliberately permissive about
    the key name (any alphanumeric/underscore token) so custom keys work.
    """
    if not isinstance(accelerator, str) or not accelerator.strip():
        raise KeybindingError("accelerator must be a non-empty string")
    accel = accelerator.strip()
    if not _ACCEL_RE.match(accel):
        raise KeybindingError(
            "invalid accelerator: {!r} (expected [<Modifier>...]KeyName, "
            "e.g. '<Control>Return', 'Escape', '<Shift><Alt>F1')".format(accelerator)
        )
    # Validate each modifier token.
    for token in re.findall(r"<[^>]+>", accel):
        if token.lower() not in MODIFIERS:
            raise KeybindingError("unknown modifier: {!r} (known: {})".format(
                token, ", ".join(sorted(m.title() for m in MODIFIERS))))
    # The key part is what remains after stripping modifiers.
    key_part = re.sub(r"<[^>]+>", "", accel)
    if not key_part:
        raise KeybindingError("accelerator has no key name: {!r}".format(accelerator))
    if len(accel) > 100:
        raise KeybindingError("accelerator too long (max 100 chars)")
    return accel


def parse_accelerator(accelerator: str) -> Tuple[List[str], str]:
    """Split an accelerator into (modifiers, key_name).

    Returns modifiers as a list of lowercase tokens without angle brackets
    (e.g. ``["control", "shift"]``) and the key name (e.g. ``"Return"``).
    Raises KeybindingError on invalid input.
    """
    accel = validate_accelerator(accelerator)
    modifiers = [m.lower().strip("<>") for m in re.findall(r"<[^>]+>", accel)]
    key = re.sub(r"<[^>]+>", "", accel)
    return modifiers, key


def format_accelerator(modifiers: List[str], key: str) -> str:
    """Rebuild an accelerator string from modifiers and key name."""
    parts = []
    for mod in modifiers:
        token = mod.lower()
        if token == "primary":
            token = "control"
        elif token == "mod1":
            token = "alt"
        elif token == "mod4":
            token = "super"
        parts.append("<{}>".format(token.title()))
    parts.append(key)
    return "".join(parts)


def normalize_accelerator(accelerator: str) -> str:
    """Canonicalise an accelerator: lowercase modifiers, sorted, primary->control."""
    modifiers, key = parse_accelerator(accelerator)
    canonical_mods = []
    for mod in modifiers:
        if mod == "primary":
            mod = "control"
        elif mod == "ctrl":
            mod = "control"
        elif mod == "mod1":
            mod = "alt"
        elif mod == "mod4":
            mod = "super"
        canonical_mods.append(mod)
    # Sort modifiers in a stable order: control, alt, shift, super, meta, hyper.
    order = {"control": 0, "alt": 1, "shift": 2, "super": 3, "meta": 4, "hyper": 5}
    canonical_mods = sorted(set(canonical_mods), key=lambda m: order.get(m, 99))
    return format_accelerator(canonical_mods, key)


def find_conflicts(bindings: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """Return (action_a, action_b, accelerator) for accelerators used by 2+ actions."""
    seen: Dict[str, str] = {}
    conflicts: List[Tuple[str, str, str]] = []
    for action in sorted(bindings):
        accel = normalize_accelerator(bindings[action])
        if accel in seen:
            conflicts.append((seen[accel], action, bindings[action]))
        else:
            seen[accel] = action
    return conflicts


def merge_with_defaults(user_bindings: Dict[str, str]) -> Dict[str, str]:
    """Merge user bindings over defaults: user wins, defaults fill gaps.

    Actions set to an empty string in user_bindings are treated as
    "disabled" and removed from the result (the action will have no
    accelerator). Unknown actions in user_bindings are kept (forward-
    compatible with future window features).
    """
    result = dict(DEFAULT_BINDINGS)
    for action, accel in user_bindings.items():
        if accel == "" or accel is None:
            result.pop(action, None)
        else:
            result[action] = accel
    return result


def load_from_config(config: Any) -> KeybindingSet:
    """Build a KeybindingSet from a ConfigManager (reads 'shortcuts.bindings')."""
    raw = config.get("shortcuts.bindings", {})
    if not isinstance(raw, dict):
        logger.warning("shortcuts.bindings is not a dict; using defaults")
        return KeybindingSet.defaults()
    merged = merge_with_defaults(raw)
    try:
        return KeybindingSet.from_dict(merged)
    except KeybindingError as error:
        logger.warning("Invalid keybinding in config, using defaults: %s", error)
        return KeybindingSet.defaults()


def save_to_config(kbs: KeybindingSet, config: Any) -> None:
    """Persist a KeybindingSet to ConfigManager under 'shortcuts.bindings'.

    Only the *user* overrides are stored (diff against defaults) so the
    config stays small and defaults can evolve between releases.
    """
    defaults = DEFAULT_BINDINGS
    overrides: Dict[str, str] = {}
    for action, accel in kbs.bindings.items():
        if defaults.get(action) != accel:
            overrides[action] = accel
    # Actions explicitly disabled (present in defaults but absent from kbs).
    for action in defaults:
        if action not in kbs.bindings:
            overrides[action] = ""
    config.set("shortcuts.bindings", overrides)
    config.flush()


def describe_binding(action: str, accelerator: str) -> str:
    """Human-readable description of one binding, e.g. 'send: Ctrl+Return'."""
    modifiers, key = parse_accelerator(accelerator)
    mod_str = "+".join(m.title() for m in modifiers)
    key_display = key
    # Make common key names more readable.
    key_display = {
        "Return": "Enter",
        "space": "Space",
        "Escape": "Esc",
        "BackSpace": "Backspace",
        "Page_Up": "PgUp",
        "Page_Down": "PgDn",
    }.get(key, key)
    if mod_str:
        return "{}: {}+{}".format(action, mod_str, key_display)
    return "{}: {}".format(action, key_display)


def format_binding_table(kbs: KeybindingSet) -> str:
    """Multi-line table of all bindings, sorted by action name."""
    lines = ["{:<20s} {}".format("Action", "Shortcut")]
    lines.append("{:<20s} {}".format("-" * 20, "-" * 20))
    for action in kbs.actions():
        accel = kbs.bindings[action]
        lines.append("{:<20s} {}".format(action, describe_binding(action, accel)))
    return "\n".join(lines)


def is_builtin_action(action: str) -> bool:
    """True when `action` is one of the window's builtin actions."""
    return action in BUILTIN_ACTIONS


def validate_binding_set(kbs: KeybindingSet) -> List[str]:
    """Return a list of warnings (conflicts, unknown actions). Empty = clean."""
    warnings: List[str] = []
    conflicts = find_conflicts(kbs.bindings)
    for action_a, action_b, accel in conflicts:
        warnings.append(
            "conflict: '{}' and '{}' both use '{}'".format(action_a, action_b, accel)
        )
    for action in kbs.bindings:
        if not is_builtin_action(action):
            warnings.append("unknown action: '{}' (not a builtin)".format(action))
    return warnings
