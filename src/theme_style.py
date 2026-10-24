import re
import logging
from typing import Any, Callable, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T", int, float)

# GTK3 supports RGB hex colors; CSS4 RGBA hex forms fail its CSS parser.
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 _.,'-]{1,64}$")

MAX_CONTEXT_CHARS = 12000
MAX_CONTEXT_MESSAGES = 20
STREAM_FLUSH_INTERVAL = 0.08


def valid_color(value):
    """True when `value` is a hex color GTK3's CSS parser accepts.

    The predicate behind `safe_color`, exposed for callers that must *reject*
    a bad value (theme_store validates a file before writing it) instead of
    silently rendering the fallback.
    """
    return isinstance(value, str) and bool(_COLOR_RE.match(value.strip()))


def safe_color(value, fallback="#1e1e1e"):
    """Return a hex color supported by GTK3 CSS or `fallback`."""
    if isinstance(value, str) and _COLOR_RE.match(value.strip()):
        return value.strip()
    if value is not None:
        logger.warning("Invalid theme color ignored: %r", value)
    return fallback


def safe_font_family(value, fallback="Monospace"):
    """Return a safe font-family name for CSS."""
    if isinstance(value, str) and _FONT_FAMILY_RE.match(value.strip()):
        return value.strip()
    if value is not None:
        logger.warning("Invalid theme font family ignored: %r", value)
    return fallback


def contrasting_text_color(background):
    """Choose readable text for a validated theme accent, including light ones."""
    value = safe_color(background, '#4CAF50')[1:]
    if len(value) == 3:
        value = ''.join(character * 2 for character in value)
    channels = [int(value[offset:offset + 2], 16) / 255 for offset in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
              for channel in channels]
    luminance = sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return '#000000' if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else '#ffffff'


def safe_number(
    value: Any,
    fallback: T,
    cast: Callable[[Any], T],
    minimum: Optional[Union[int, float]] = None,
    maximum: Optional[Union[int, float]] = None,
) -> T:
    """Convert `value` to a number with bounds, or return `fallback`."""
    try:
        number = cast(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None and number < minimum:
        return fallback
    if maximum is not None and number > maximum:
        return fallback
    return number


def valid_chat_style(
    theme_colors,
    theme_info,
    config_get,
    syntax_colors=None,
    ui=None,
):
    """Return the validated theme/style values used by the chat window.

    Keeps the GTK-specific CSS assembly in `src/main_window.py` while this
    module owns the parsing, validation, and color-contrast logic.
    """
    if theme_info is None:
        theme_info = {}
    if syntax_colors is None:
        syntax_colors = {}
    if ui is None:
        ui = {}

    colors = theme_colors

    if theme_info and 'syntax_highlighting' in theme_info:
        syntax_colors = theme_info['syntax_highlighting']
    if theme_info and 'ui' in theme_info:
        ui = theme_info['ui']

    bg_color = safe_color(colors.get('background'), '#1e1e1e')
    text_color = safe_color(colors.get('text'), '#e0e0e0')
    accent_color = safe_color(colors.get('accent'), '#4CAF50')
    secondary_color = safe_color(colors.get('secondary'), '#2d2d2d')
    tertiary_color = safe_color(colors.get('tertiary'), '#252525')
    accent_text = contrasting_text_color(accent_color)
    expert_text = contrasting_text_color('#2196F3')
    danger_text = contrasting_text_color('#f44336')

    font_family = safe_font_family(
        ui.get('font_family') or config_get('ui.font_family'),
        'Monospace',
    )
    font_size = safe_number(
        ui.get('font_size') or config_get('ui.font_size'),
        12, int, minimum=4, maximum=72,
    )
    border_radius = safe_number(
        ui.get('border_radius') or config_get('ui.border_radius'),
        10, int, minimum=0, maximum=64,
    )

    user_msg_color = safe_color(syntax_colors.get('user_message'), text_color)
    ai_msg_color = safe_color(syntax_colors.get('ai_message'), text_color)
    system_msg_color = safe_color(syntax_colors.get('system_message'), text_color)

    return {
        'bg_color': bg_color,
        'text_color': text_color,
        'accent_color': accent_color,
        'secondary_color': secondary_color,
        'tertiary_color': tertiary_color,
        'accent_text': accent_text,
        'expert_text': expert_text,
        'danger_text': danger_text,
        'font_family': font_family,
        'font_size': font_size,
        'border_radius': border_radius,
        'user_msg_color': user_msg_color,
        'ai_msg_color': ai_msg_color,
        'system_msg_color': system_msg_color,
    }


def app_style_css(style):
    """Return the GTK CSS for the app using already-validated style values."""
    return f"""
        .linux-ai-main #main-box {{
            background-color: {style['bg_color']};
            color: {style['text_color']};
            border-radius: {style['border_radius']}px;
            padding: 10px;
            margin: 5px;
        }}

        .linux-ai-main #header {{
            background-color: {style['secondary_color']};
            border-radius: {style['border_radius']}px {style['border_radius']}px 0 0;
            padding: 8px;
            margin-bottom: 10px;
        }}

        .linux-ai-main #chat-area {{
            background-color: {style['tertiary_color']};
            border-radius: 5px;
            padding: 10px;
            margin-bottom: 10px;
            min-height: 300px;
        }}

        .linux-ai-main #input-area {{
            background-color: {style['secondary_color']};
            border-radius: 5px;
            padding: 10px;
        }}

        .linux-ai-main #main-box textview,
        .linux-ai-main #main-box textview text {{
            font-family: {style['font_family']};
            font-size: {style['font_size']}pt;
            background-color: {style['tertiary_color']};
            color: {style['text_color']};
            border: none;
            padding: 5px;
        }}

        .linux-ai-main #main-box button {{
            background-color: {style['accent_color']};
            background-image: none;
            color: {style['accent_text']};
            text-shadow: none;
            -gtk-icon-shadow: none;
            box-shadow: none;
            border-radius: 5px;
            padding: 5px 10px;
            font-family: {style['font_family']};
            font-size: 10pt;
            border: none;
            min-width: 32px;
        }}

        .linux-ai-main #main-box button:hover {{
            opacity: 0.9;
        }}

        .linux-ai-main #main-box button:active {{
            opacity: 0.7;
        }}

        .linux-ai-main #main-box button.expert {{
            background-color: #2196F3;
            color: {style['expert_text']};
        }}

        .linux-ai-main #main-box button.expert:hover {{
            background-color: #0b7dda;
        }}

        .linux-ai-main #main-box button.danger {{
            background-color: #f44336;
            color: {style['danger_text']};
        }}

        .linux-ai-main #main-box button.danger:hover {{
            background-color: #da190b;
        }}

        .linux-ai-main #main-box button:disabled {{
            background-color: {style['secondary_color']};
            color: {style['text_color']};
            opacity: 0.6;
        }}

        .linux-ai-main #main-box entry {{
            background-color: {style['secondary_color']};
            background-image: none;
            color: {style['text_color']};
            border-radius: 5px;
            padding: 5px;
            font-family: {style['font_family']};
            font-size: {style['font_size']}pt;
            border: 1px solid transparent;
            box-shadow: none;
            caret-color: {style['text_color']};
        }}

        .linux-ai-main #main-box entry:focus {{
            border: 1px solid {style['accent_color']};
        }}

        .linux-ai-main #main-box entry selection,
        .linux-ai-main #main-box textview text selection {{
            background-color: {style['accent_color']};
            color: {style['accent_text']};
        }}

        .linux-ai-main #main-box scrolledwindow {{
            background-color: {style['tertiary_color']};
            border-radius: 5px;
            border: none;
        }}

        .linux-ai-main #main-box .loading {{
            opacity: 0.7;
            font-style: italic;
        }}

        .linux-ai-main #main-box .expert-mode {{
            border-left: 3px solid #2196F3;
            padding-left: 10px;
        }}
        """
