"""Theme parsing and validation are GTK-free, so they are tested headless.

This is the module the renderer trusts: if `safe_color` accepted a CSS4 value,
GTK3's CSS parser would reject the whole stylesheet at runtime and the window
would come up unstyled, so the boundaries below are part of the contract.
"""

import unittest
from unittest.mock import patch

from src import theme_style
from src.theme_style import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_MESSAGES,
    STREAM_FLUSH_INTERVAL,
    app_style_css,
    contrasting_text_color,
    safe_color,
    safe_font_family,
    safe_number,
    valid_chat_style,
    valid_color,
)


class ColorTests(unittest.TestCase):
    def test_valid_color_accepts_the_hex_forms_gtk3_parses(self):
        for value in ("#fff", "#FFF", "#1e1e1e", "#4CAF50", " #fff ", "#000000"):
            with self.subTest(value=value):
                self.assertTrue(valid_color(value))
                self.assertTrue(safe_color(value).startswith("#"))

    def test_valid_color_rejects_css4_and_textual_forms(self):
        for value in ("#12345", "#12345678", "#1e1e1e80", "red", "rgb(0,0,0)",
                      "", "#", "fff", None, 123, "#zzzzzz"):
            with self.subTest(value=value):
                self.assertFalse(valid_color(value))
                self.assertEqual(safe_color(value, "#fallback"), "#fallback")

    def test_safe_color_keeps_the_requested_fallback(self):
        self.assertEqual(safe_color(None, "#101010"), "#101010")
        # A missing key (None) is a normal default, not a warning condition.
        with patch.object(theme_style.logger, "warning") as warning:
            safe_color(None, "#101010")
            warning.assert_not_called()
        with patch.object(theme_style.logger, "warning") as warning:
            safe_color("nonsense", "#101010")
            warning.assert_called_once()


class FontFamilyTests(unittest.TestCase):
    def test_reasonable_font_stacks_survive(self):
        for value in ("Monospace", "DejaVu Sans, monospace", "Cantarell", "serif"):
            with self.subTest(value=value):
                self.assertEqual(safe_font_family(value), value)

    def test_font_families_that_could_escape_css_are_rejected(self):
        for value in ("a" * 65, 'Monospace"; color: red; x="', "mono space;", "", None, 7):
            with self.subTest(value=value):
                self.assertEqual(safe_font_family(value, "Fallback"), "Fallback")


class ContrastTests(unittest.TestCase):
    def test_extremes_pick_a_readable_foreground(self):
        self.assertEqual(contrasting_text_color("#ffffff"), "#000000")
        self.assertEqual(contrasting_text_color("#000000"), "#ffffff")

    def test_short_and_invalid_backgrounds_still_return_a_hex_colour(self):
        for value in ("#fff", "#abc", "nonsense", None, "#4CAF50"):
            with self.subTest(value=value):
                self.assertIn(contrasting_text_color(value), ("#000000", "#ffffff"))


class SafeNumberTests(unittest.TestCase):
    def test_bounds_are_enforced(self):
        self.assertEqual(safe_number("14", 12, int, minimum=4, maximum=72), 14)
        self.assertEqual(safe_number("3", 12, int, minimum=4, maximum=72), 12)
        self.assertEqual(safe_number("80", 12, int, minimum=4, maximum=72), 12)

    def test_unusable_values_fall_back(self):
        for value in ("abc", None, [1], object()):
            with self.subTest(value=value):
                self.assertEqual(safe_number(value, 12, int), 12)
        # bool is an int subclass, but True is not a usable size.
        self.assertEqual(safe_number(True, 12, int, minimum=4, maximum=72), 12)

    def test_float_and_string_parsing(self):
        self.assertEqual(safe_number("1.5", 1.0, float, minimum=0.0, maximum=2.0), 1.5)
        self.assertEqual(safe_number("1.5", 1.0, float, minimum=0.0, maximum=1.0), 1.0)


class ValidChatStyleTests(unittest.TestCase):
    @staticmethod
    def config(values):
        return lambda key: values.get(key)

    def test_missing_everything_yields_the_documented_defaults(self):
        style = valid_chat_style({}, None, self.config({}))
        self.assertEqual(style["bg_color"], "#1e1e1e")
        self.assertEqual(style["text_color"], "#e0e0e0")
        self.assertEqual(style["accent_color"], "#4CAF50")
        self.assertEqual(style["font_family"], "Monospace")
        self.assertEqual(style["font_size"], 12)
        self.assertEqual(style["border_radius"], 10)
        self.assertEqual(style["user_msg_color"], style["text_color"])

    def test_invalid_theme_values_never_reach_the_renderer(self):
        colors = {"background": "not-a-colour", "text": "#12345", "accent": "#4CAF50"}
        theme_info = {"ui": {"font_family": 'x"; color: red', "font_size": 999,
                             "border_radius": -5}}
        style = valid_chat_style(colors, theme_info, self.config({}))
        self.assertEqual(style["bg_color"], "#1e1e1e")
        self.assertEqual(style["text_color"], "#e0e0e0")
        self.assertEqual(style["font_family"], "Monospace")
        self.assertEqual(style["font_size"], 12)
        self.assertEqual(style["border_radius"], 10)

    def test_theme_info_overrides_the_configuration(self):
        config = self.config({"ui.font_family": "Config Font", "ui.font_size": 10,
                              "ui.border_radius": 4})
        theme_info = {
            "ui": {"font_family": "Theme Font", "font_size": 18, "border_radius": 14},
            "syntax_highlighting": {"user_message": "#112233", "ai_message": "#223344",
                                    "system_message": "#334455"},
        }
        style = valid_chat_style({"background": "#000000", "text": "#ffffff",
                                  "accent": "#00ff00"}, theme_info, config)
        self.assertEqual(style["font_family"], "Theme Font")
        self.assertEqual(style["font_size"], 18)
        self.assertEqual(style["border_radius"], 14)
        self.assertEqual(style["user_msg_color"], "#112233")
        self.assertEqual(style["ai_msg_color"], "#223344")
        self.assertEqual(style["system_msg_color"], "#334455")

    def test_config_is_used_when_the_theme_is_silent(self):
        style = valid_chat_style({}, None, self.config({
            "ui.font_family": "Config Font", "ui.font_size": 15, "ui.border_radius": 6,
        }))
        self.assertEqual((style["font_family"], style["font_size"], style["border_radius"]),
                         ("Config Font", 15, 6))

    def test_light_accents_get_readable_text(self):
        style = valid_chat_style({"background": "#ffffff", "text": "#000000",
                                  "accent": "#ffffff"}, None, self.config({}))
        self.assertEqual(style["accent_text"], "#000000")
        self.assertEqual(style["text_color"], "#000000")

    def test_every_expected_key_is_always_present(self):
        expected = {
            "bg_color", "text_color", "accent_color", "secondary_color", "tertiary_color",
            "accent_text", "expert_text", "danger_text", "font_family", "font_size",
            "border_radius", "user_msg_color", "ai_msg_color", "system_msg_color",
        }
        self.assertEqual(set(valid_chat_style({}, {}, self.config({}))), expected)


class AppStyleCssTests(unittest.TestCase):
    def setUp(self):
        self.style = valid_chat_style(
            {"background": "#101010", "text": "#f0f0f0", "accent": "#00ff00"},
            {"ui": {"font_family": "Monospace", "font_size": 13, "border_radius": 7}},
            lambda key: None,
        )

    def test_css_uses_the_validated_values(self):
        css = app_style_css(self.style)
        self.assertIn("background-color: #101010", css)
        self.assertIn("color: #f0f0f0", css)
        self.assertIn("border-radius: 7px", css)
        self.assertIn("font-family: Monospace", css)
        self.assertIn("font-size: 13pt", css)

    def test_css_is_balanced_and_free_of_values_gtk3_rejects(self):
        css = app_style_css(self.style)
        self.assertEqual(css.count("{"), css.count("}"))
        # GTK3 parses 3/6-digit hex only: CSS4 alpha hex aborts the parser and
        # leaves the window unstyled.
        self.assertNotIn("rgba(", css)
        for value in (self.style["bg_color"], self.style["text_color"],
                      self.style["accent_color"], self.style["font_family"]):
            self.assertTrue(value)
            self.assertRegex(value, r"^#[0-9a-fA-F]{6}$|^[A-Za-z][A-Za-z ,'-]*$")

    def test_constants_that_govern_context_and_streaming(self):
        self.assertEqual(MAX_CONTEXT_CHARS, 12000)
        self.assertEqual(MAX_CONTEXT_MESSAGES, 20)
        self.assertGreater(STREAM_FLUSH_INTERVAL, 0)
        self.assertLess(STREAM_FLUSH_INTERVAL, 1)


if __name__ == "__main__":
    unittest.main()
