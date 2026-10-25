"""GTK-free accessibility helpers: WCAG contrast, touch targets, color names.

The theme system stores colors as hex strings; this module answers the
questions a theme author or a settings dialog needs without touching GTK:

- Is this foreground/background pair readable? (WCAG 2.1 contrast ratio)
- Does it meet AA or AAA for normal and large text?
- Is a widget large enough to be a comfortable touch target?
- What does this color look like, in words? (for tooltips and screen readers)

All functions are pure and side-effect free; the UI layer decides when to
call them.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# WCAG 2.1 minimum contrast ratios.
WCAG_AA_NORMAL = 4.5
WCAG_AA_LARGE = 3.0
WCAG_AAA_NORMAL = 7.0
WCAG_AAA_LARGE = 4.5

# WCAG 2.5.5 (AAA) target size; 24x24 CSS px is the AA-level minimum.
TOUCH_TARGET_AAA = 44.0
TOUCH_TARGET_AA = 24.0

# Hue sectors (degrees) mapped to human color names. Boundaries are the
# midpoints between adjacent pure hues in HSV space.
_HUE_NAMES: Tuple[Tuple[float, str], ...] = (
    (15.0, "red"),
    (45.0, "orange"),
    (70.0, "yellow"),
    (160.0, "green"),
    (200.0, "cyan"),
    (255.0, "blue"),
    (290.0, "purple"),
    (330.0, "magenta"),
    (360.0, "red"),
)


def parse_hex_color(value: str) -> Optional[Tuple[int, int, int]]:
    """Return (r, g, b) for a #rgb or #rrggbb string, or None if invalid."""
    if not isinstance(value, str):
        return None
    match = _COLOR_RE.match(value.strip())
    if not match:
        return None
    digits = value.strip()[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def _relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance (0.0 = black, 1.0 = white)."""
    channels = []
    for component in rgb:
        c = component / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground: str, background: str) -> Optional[float]:
    """WCAG 2.1 contrast ratio between two hex colors (1.0 - 21.0).

    Returns None when either color is not a valid hex string, so callers
    can distinguish "unreadable" from "not a color".
    """
    fg = parse_hex_color(foreground)
    bg = parse_hex_color(background)
    if fg is None or bg is None:
        return None
    lum_fg = _relative_luminance(fg)
    lum_bg = _relative_luminance(bg)
    lighter = max(lum_fg, lum_bg)
    darker = min(lum_fg, lum_bg)
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True)
class ContrastReport:
    """Result of checking one foreground/background pair."""

    ratio: float
    aa_normal: bool
    aa_large: bool
    aaa_normal: bool
    aaa_large: bool

    @property
    def level(self) -> str:
        """Highest WCAG level met: 'AAA', 'AA', 'AA Large' or 'Fail'.

        Note: AAA Large (4.5:1) has the same threshold as AA Normal, so a
        pair that passes AAA Large also passes AA Normal — the reported
        level is always the strictest *normal-text* level met.
        """
        if self.aaa_normal:
            return "AAA"
        if self.aa_normal:
            return "AA"
        if self.aa_large:
            return "AA Large"
        return "Fail"


def check_contrast(foreground: str, background: str) -> Optional[ContrastReport]:
    """Full WCAG contrast report for a color pair, or None if invalid."""
    ratio = contrast_ratio(foreground, background)
    if ratio is None:
        return None
    return ContrastReport(
        ratio=ratio,
        aa_normal=ratio >= WCAG_AA_NORMAL,
        aa_large=ratio >= WCAG_AA_LARGE,
        aaa_normal=ratio >= WCAG_AAA_NORMAL,
        aaa_large=ratio >= WCAG_AAA_LARGE,
    )


@dataclass(frozen=True)
class TouchTargetReport:
    """Result of checking a widget's clickable size."""

    width: float
    height: float
    aa: bool
    aaa: bool

    @property
    def min_side(self) -> float:
        return min(self.width, self.height)

    @property
    def level(self) -> str:
        if self.aaa:
            return "AAA"
        if self.aa:
            return "AA"
        return "Fail"


def check_touch_target(width: float, height: float) -> TouchTargetReport:
    """Check a widget size against WCAG 2.5.5 touch-target minimums.

    WCAG 2.5.5 (AAA) asks for at least 44x44 CSS pixels; the AA-level
    minimum (WCAG 2.5.8) is 24x24. Undersized targets on a touch screen
    are a real accessibility failure, not a cosmetic one.
    """
    return TouchTargetReport(
        width=float(width),
        height=float(height),
        aa=min(width, height) >= TOUCH_TARGET_AA,
        aaa=min(width, height) >= TOUCH_TARGET_AAA,
    )


def _rgb_to_hsv(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    r, g, b = (c / 255.0 for c in rgb)
    maximum = max(r, g, b)
    minimum = min(r, g, b)
    delta = maximum - minimum
    if delta == 0:
        hue = 0.0
    elif maximum == r:
        hue = 60.0 * (((g - b) / delta) % 6)
    elif maximum == g:
        hue = 60.0 * ((b - r) / delta + 2)
    else:
        hue = 60.0 * ((r - g) / delta + 4)
    saturation = 0.0 if maximum == 0 else delta / maximum
    return hue % 360.0, saturation, maximum


def color_name(value: str) -> Optional[str]:
    """Human-readable name for a hex color, e.g. 'dark green'.

    Returns None for invalid input. The name is built from HSV: lightness
    picks dark/light/gray, saturation picks vivid vs muted, and hue picks
    the color family. Useful for tooltips and screen-reader descriptions.
    """
    rgb = parse_hex_color(value)
    if rgb is None:
        return None
    hue, saturation, value_ = _rgb_to_hsv(rgb)
    if value_ < 0.12:
        return "black"
    if value_ > 0.92 and saturation < 0.08:
        return "white"
    if saturation < 0.12:
        if value_ < 0.35:
            return "dark gray"
        if value_ > 0.75:
            return "light gray"
        return "gray"
    for boundary, name in _HUE_NAMES:
        if hue < boundary:
            hue_name = name
            break
    if value_ < 0.35:
        prefix = "dark "
    elif value_ < 0.6 and saturation > 0.5:
        prefix = "deep "
    elif value_ > 0.8 and saturation < 0.4:
        prefix = "light "
    else:
        prefix = ""
    if saturation < 0.35:
        prefix += "muted " if prefix else "muted "
    return (prefix + hue_name).strip()


def describe_color_pair(foreground: str, background: str) -> Optional[str]:
    """One-line description of a color pair, e.g. 'dark text on light background'.

    Combines color_name() for both colors with the contrast verdict, for
    tooltips, theme previews and accessibility audits.
    """
    fg_name = color_name(foreground)
    bg_name = color_name(background)
    if fg_name is None or bg_name is None:
        return None
    report = check_contrast(foreground, background)
    verdict = report.level if report is not None else "unknown contrast"
    return "{fg} on {bg} ({verdict})".format(fg=fg_name, bg=bg_name, verdict=verdict)


def audit_theme_colors(colors: Dict[str, str]) -> Dict[str, ContrastReport]:
    """Check 'text' against 'background' (and 'accent' vs 'background') in a theme.

    Returns a mapping of pair name -> ContrastReport for the pairs that
    have valid colors. Missing or invalid keys are skipped, not failed.
    """
    pairs = {
        "text/background": (colors.get("text"), colors.get("background")),
        "accent/background": (colors.get("accent"), colors.get("background")),
    }
    results: Dict[str, ContrastReport] = {}
    for name, (fg, bg) in pairs.items():
        if fg is None or bg is None:
            continue
        report = check_contrast(fg, bg)
        if report is not None:
            results[name] = report
    return results


def suggest_accessible_foreground(background: str, prefer_dark: bool = True) -> Optional[str]:
    """Return #000000 or #ffffff — whichever contrasts more with `background`.

    Mirrors theme_style.contrasting_text_color but as a pure function with
    an explicit preference, for theme validation and onboarding hints.
    """
    bg = parse_hex_color(background)
    if bg is None:
        return None
    dark = contrast_ratio("#000000", background)
    light = contrast_ratio("#ffffff", background)
    if dark is None or light is None:
        return None
    if dark == light:
        return "#000000" if prefer_dark else "#ffffff"
    return "#000000" if dark > light else "#ffffff"
