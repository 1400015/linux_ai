"""GTK-free theme editing core: create, modify, validate and manage themes.

The GUI settings dialog and the CLI both delegate here so the rules (name
validation, colour validation, WCAG contrast hints, atomic writes) live in
exactly one place. All functions are pure or file-based; none import GTK.

A theme document has this shape (matching ``themes/*.json``)::

    {
        "name": "my-theme",
        "description": "optional",
        "colors": {
            "background": "#1e1e1e", "text": "#e0e0e0",
            "accent": "#4CAF50", "secondary": "#2d2d2d",
            "tertiary": "#252525", "border": "#3d3d3d"
        },
        "ui": {"font_family": "Monospace", "font_size": 12,
               "border_radius": 10, "opacity": 0.9},
        "syntax_highlighting": {"user_message": "#e0e0e0", ...}
    }
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .a11y import audit_theme_colors, suggest_accessible_foreground
from .theme_store import (
    REQUIRED_COLOR_KEYS,
    is_builtin,
    list_bundled_themes,
    list_user_themes,
    remove_theme,
    save_theme,
    user_theme_path,
    validate_theme_name,
)
from .theme_style import valid_color

# Colour keys a theme file may carry. "border" is accepted for backward
# compatibility with the bundled themes (the renderer does not currently
# consume it, but removing it would break every shipped theme file).
COLOR_KEYS = ("background", "text", "accent", "secondary", "tertiary", "border")

logger = logging.getLogger(__name__)

SYNTAX_KEYS = (
    "user_message", "ai_message", "system_message",
    "code", "keyword", "string", "comment", "number",
)

UI_KEYS = ("font_family", "font_size", "border_radius", "opacity")

DEFAULT_COLORS: Dict[str, str] = {
    "background": "#1e1e1e",
    "text": "#e0e0e0",
    "accent": "#4CAF50",
    "secondary": "#2d2d2d",
    "tertiary": "#252525",
    "border": "#3d3d3d",
}

DEFAULT_UI: Dict[str, Any] = {
    "font_family": "Monospace",
    "font_size": 12,
    "border_radius": 10,
    "opacity": 0.9,
}

DEFAULT_SYNTAX: Dict[str, str] = {
    "user_message": "#e0e0e0",
    "ai_message": "#a0d0a0",
    "system_message": "#808080",
    "code": "#d4d4d4",
    "keyword": "#569cd6",
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
}


class ThemeEditorError(Exception):
    """Raised when a theme operation cannot be completed."""


@dataclass
class ThemeDocument:
    """A complete, editable theme document."""

    name: str
    description: str = ""
    colors: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLORS))
    ui: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_UI))
    syntax_highlighting: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SYNTAX))

    def to_dict(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {"name": self.name}
        if self.description:
            doc["description"] = self.description
        doc["colors"] = dict(self.colors)
        doc["ui"] = dict(self.ui)
        if self.syntax_highlighting:
            doc["syntax_highlighting"] = dict(self.syntax_highlighting)
        return doc

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ThemeDocument":
        if not isinstance(data, dict):
            raise ThemeEditorError("theme must be a JSON object")
        name = data.get("name")
        if not isinstance(name, str):
            raise ThemeEditorError("theme 'name' is required")
        colors = data.get("colors", {})
        if not isinstance(colors, dict):
            raise ThemeEditorError("theme 'colors' must be an object")
        ui = data.get("ui", {})
        if not isinstance(ui, dict):
            raise ThemeEditorError("theme 'ui' must be an object")
        syntax = data.get("syntax_highlighting", {})
        if not isinstance(syntax, dict):
            raise ThemeEditorError("theme 'syntax_highlighting' must be an object")
        return cls(
            name=name,
            description=data.get("description", "") if isinstance(data.get("description"), str) else "",
            colors=dict(colors),
            ui=dict(ui),
            syntax_highlighting=dict(syntax),
        )


def default_theme(name: str, description: str = "") -> ThemeDocument:
    """Create a new theme document from the default dark palette."""
    validate_theme_name(name)
    return ThemeDocument(name=name, description=description)


def load_theme_document(name: str, bundled_dir: Optional[Path] = None) -> ThemeDocument:
    """Load a theme by name (user themes take priority over bundled)."""
    validate_theme_name(name)
    user_path = user_theme_path(name)
    if user_path.is_file():
        return _load_from_path(user_path)
    from .theme_store import bundled_themes_dir
    bundled_path = (bundled_dir or bundled_themes_dir()) / (name + ".json")
    if bundled_path.is_file():
        return _load_from_path(bundled_path)
    raise ThemeEditorError("theme not found: {}".format(name))


def _load_from_path(path: Path) -> ThemeDocument:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ThemeEditorError("cannot read theme {}: {}".format(path.name, error)) from error
    return ThemeDocument.from_dict(data)


def save_theme_document(doc: ThemeDocument) -> Path:
    """Validate and save a theme document to the user themes directory.

    ``theme_store.save_theme`` enforces a strict colour-key allowlist that
    does not include ``border`` (present in every bundled theme file but not
    consumed by the renderer). We validate the full document ourselves, then
    strip ``border`` before delegating the atomic write to the store.
    """
    _validate_for_save(doc)
    data = doc.to_dict()
    if "border" in data.get("colors", {}):
        data["colors"] = {k: v for k, v in data["colors"].items() if k != "border"}
    return save_theme(data)


def _validate_for_save(doc: ThemeDocument) -> None:
    """Validate a theme document before writing (strict + advisory checks)."""
    data = doc.to_dict()
    colors = data.get("colors", {})
    for key in REQUIRED_COLOR_KEYS:
        if key not in colors:
            raise ThemeEditorError("missing required colour: {}".format(key))
    for key, value in colors.items():
        if key not in COLOR_KEYS:
            raise ThemeEditorError("unknown colour key: {!r}".format(key))
        if not valid_color(value):
            raise ThemeEditorError("invalid colour for {!r}: {!r}".format(key, value))
    for key, value in doc.syntax_highlighting.items():
        if not valid_color(value):
            raise ThemeEditorError(
                "invalid syntax_highlighting colour for {!r}: {!r}".format(key, value)
            )


def delete_theme(name: str, bundled_dir: Optional[Path] = None) -> Optional[Path]:
    """Delete a user theme; built-in themes are refused by theme_store."""
    return remove_theme(name, bundled_dir)


def list_all_themes(bundled_dir: Optional[Path] = None) -> List[str]:
    """Return all theme names (bundled + user), user themes last, sorted."""
    bundled = list_bundled_themes(bundled_dir)
    user = [n for n in list_user_themes() if n not in bundled]
    return sorted(bundled) + sorted(user)


def is_user_theme(name: str, bundled_dir: Optional[Path] = None) -> bool:
    """True when the theme exists only in the user directory (editable)."""
    return name in list_user_themes() and not is_builtin(name, bundled_dir)


def set_color(doc: ThemeDocument, key: str, value: str) -> None:
    """Set a colour key on the document, validating the hex value."""
    if key not in COLOR_KEYS:
        raise ThemeEditorError("unknown colour key: {!r} (expected one of {})".format(
            key, ", ".join(COLOR_KEYS)))
    if not valid_color(value):
        raise ThemeEditorError("invalid colour for {!r}: {!r} (expected #rgb or #rrggbb)".format(key, value))
    doc.colors[key] = value.lower()


def set_syntax_color(doc: ThemeDocument, key: str, value: str) -> None:
    """Set a syntax-highlighting colour, validating the hex value."""
    if key not in SYNTAX_KEYS:
        raise ThemeEditorError("unknown syntax key: {!r} (expected one of {})".format(
            key, ", ".join(SYNTAX_KEYS)))
    if not valid_color(value):
        raise ThemeEditorError("invalid colour for {!r}: {!r}".format(key, value))
    doc.syntax_highlighting[key] = value.lower()


def set_ui_value(doc: ThemeDocument, key: str, value: Any) -> None:
    """Set a UI value with type coercion and range validation."""
    if key not in UI_KEYS:
        raise ThemeEditorError("unknown ui key: {!r} (expected one of {})".format(
            key, ", ".join(UI_KEYS)))
    if key == "font_family":
        if not isinstance(value, str) or not value.strip():
            raise ThemeEditorError("font_family must be a non-empty string")
        if len(value) > 64:
            raise ThemeEditorError("font_family is too long (max 64 chars)")
        doc.ui[key] = value.strip()
    elif key == "font_size":
        size = _coerce_int(value, "font_size")
        if not 6 <= size <= 72:
            raise ThemeEditorError("font_size must be between 6 and 72")
        doc.ui[key] = size
    elif key == "border_radius":
        radius = _coerce_int(value, "border_radius")
        if not 0 <= radius <= 40:
            raise ThemeEditorError("border_radius must be between 0 and 40")
        doc.ui[key] = radius
    elif key == "opacity":
        opacity = _coerce_float(value, "opacity")
        if not 0.1 <= opacity <= 1.0:
            raise ThemeEditorError("opacity must be between 0.1 and 1.0")
        doc.ui[key] = opacity


def _coerce_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ThemeEditorError("{} must be a number".format(label))
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ThemeEditorError("{} must be an integer".format(label))


def _coerce_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ThemeEditorError("{} must be a number".format(label))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ThemeEditorError("{} must be a number".format(label))


def rename_theme(doc: ThemeDocument, new_name: str) -> None:
    """Rename the document in memory (does not save or delete the old file)."""
    validate_theme_name(new_name)
    if new_name == doc.name:
        return
    if new_name in list_all_themes():
        raise ThemeEditorError("a theme named {!r} already exists".format(new_name))
    doc.name = new_name


def duplicate_theme(name: str, new_name: str, bundled_dir: Optional[Path] = None) -> ThemeDocument:
    """Load a theme and return a copy under a new name (not yet saved)."""
    doc = load_theme_document(name, bundled_dir)
    rename_theme(doc, new_name)
    return doc


def contrast_report(doc: ThemeDocument) -> Dict[str, Any]:
    """WCAG contrast report for the theme's main colour pairs."""
    colors = doc.colors
    results: Dict[str, Any] = {}
    for pair_name, report in audit_theme_colors(colors).items():
        results[pair_name] = {
            "ratio": round(report.ratio, 2),
            "level": report.level,
            "passes_aa_normal": report.ratio >= 4.5,
            "passes_aa_large": report.ratio >= 3.0,
        }
    return results


def suggest_fix(doc: ThemeDocument, color_key: str) -> Optional[str]:
    """Suggest an accessible foreground colour for the given background key."""
    if color_key not in COLOR_KEYS:
        return None
    background = doc.colors.get(color_key)
    if background is None or not valid_color(background):
        return None
    return suggest_accessible_foreground(background)


def validate_theme_document(doc: ThemeDocument) -> List[str]:
    """Return a list of human-readable validation warnings (empty = valid).

    Structural errors raise ThemeEditorError; this function reports
    advisory issues (missing required colours, low contrast) that do not
    prevent saving but should be surfaced to the user.
    """
    warnings: List[str] = []
    for key in REQUIRED_COLOR_KEYS:
        if key not in doc.colors:
            warnings.append("missing required colour: {}".format(key))
        elif not valid_color(doc.colors[key]):
            warnings.append("invalid colour for {}: {!r}".format(key, doc.colors[key]))
    for key in doc.colors:
        if key not in COLOR_KEYS:
            warnings.append("unknown colour key: {}".format(key))
    for key, value in doc.syntax_highlighting.items():
        if not valid_color(value):
            warnings.append("invalid syntax colour for {}: {!r}".format(key, value))
    report = contrast_report(doc)
    for pair_name, info in report.items():
        if not info["passes_aa_normal"]:
            warnings.append(
                "low contrast: {} ratio {:.2f} (WCAG AA normal needs 4.5)".format(
                    pair_name, info["ratio"])
            )
    return warnings


def diff_themes(a: ThemeDocument, b: ThemeDocument) -> Dict[str, List[Tuple[str, str, str]]]:
    """Compare two theme documents; return changed keys grouped by section."""
    changes: Dict[str, List[Tuple[str, str, str]]] = {"colors": [], "ui": [], "syntax_highlighting": []}
    all_color_keys = sorted(set(a.colors) | set(b.colors))
    for key in all_color_keys:
        color_a = a.colors.get(key, "")
        color_b = b.colors.get(key, "")
        if color_a != color_b:
            changes["colors"].append((key, color_a, color_b))
    all_ui_keys = sorted(set(a.ui) | set(b.ui))
    for key in all_ui_keys:
        ui_a: Optional[Any] = a.ui.get(key)
        ui_b: Optional[Any] = b.ui.get(key)
        if ui_a != ui_b:
            changes["ui"].append((key, str(ui_a), str(ui_b)))
    all_syntax_keys = sorted(set(a.syntax_highlighting) | set(b.syntax_highlighting))
    for key in all_syntax_keys:
        syn_a = a.syntax_highlighting.get(key, "")
        syn_b = b.syntax_highlighting.get(key, "")
        if syn_a != syn_b:
            changes["syntax_highlighting"].append((key, syn_a, syn_b))
    return {section: items for section, items in changes.items() if items}


def export_theme(doc: ThemeDocument, path: Path) -> Path:
    """Write a theme document to an arbitrary JSON file (not the themes dir)."""
    _validate_for_save(doc)
    path = Path(path)
    if path.exists():
        raise ThemeEditorError("refusing to overwrite existing file: {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def import_theme(path: Path) -> ThemeDocument:
    """Load a theme document from an arbitrary JSON file."""
    path = Path(path)
    if not path.is_file():
        raise ThemeEditorError("file not found: {}".format(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ThemeEditorError("cannot parse theme file: {}".format(error)) from error
    doc = ThemeDocument.from_dict(data)
    _validate_for_save(doc)
    return doc


def apply_theme(doc: ThemeDocument, config: Any) -> None:
    """Apply a theme document to a ConfigManager: set app.theme and ui.* keys."""
    config.set("app.theme", doc.name)
    config.set("ui.font_family", doc.ui.get("font_family", "Monospace"))
    config.set("ui.font_size", doc.ui.get("font_size", 12))
    if "border_radius" in doc.ui:
        config.set("ui.border_radius", doc.ui["border_radius"])
    if "opacity" in doc.ui:
        config.set("app.opacity", doc.ui["opacity"])
    for key in ("background", "text", "accent"):
        if key in doc.colors:
            config_key = {"background": "ui.background_color",
                          "text": "ui.text_color",
                          "accent": "ui.accent_color"}[key]
            config.set(config_key, doc.colors[key])
    config.flush()


def format_theme_text(doc: ThemeDocument) -> str:
    """Human-readable text representation of a theme document."""
    lines = ["Theme: {}".format(doc.name)]
    if doc.description:
        lines.append("Description: {}".format(doc.description))
    lines.append("Colors:")
    for key in COLOR_KEYS:
        if key in doc.colors:
            lines.append("  {:12s} {}".format(key, doc.colors[key]))
    lines.append("UI:")
    for key in UI_KEYS:
        if key in doc.ui:
            lines.append("  {:12s} {}".format(key, doc.ui[key]))
    if doc.syntax_highlighting:
        lines.append("Syntax highlighting:")
        for key in SYNTAX_KEYS:
            if key in doc.syntax_highlighting:
                lines.append("  {:16s} {}".format(key, doc.syntax_highlighting[key]))
    return "\n".join(lines)


def format_contrast_text(doc: ThemeDocument) -> str:
    """Human-readable WCAG contrast report for a theme."""
    report = contrast_report(doc)
    if not report:
        return "No contrast data (missing colours)"
    lines = ["Contrast report for '{}':".format(doc.name)]
    for pair_name, info in report.items():
        status = "PASS" if info["passes_aa_normal"] else "FAIL"
        lines.append("  {:20s} ratio {:5.2f}  AA-normal: {}  level: {}".format(
            pair_name, info["ratio"], status, info["level"]))
    return "\n".join(lines)
