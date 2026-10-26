"""Tests for src.a11y — GTK-free accessibility helpers."""

import unittest

from src.a11y import (
    TOUCH_TARGET_AA,
    TOUCH_TARGET_AAA,
    WCAG_AA_LARGE,
    WCAG_AA_NORMAL,
    WCAG_AAA_LARGE,
    WCAG_AAA_NORMAL,
    ContrastReport,
    audit_theme_colors,
    check_contrast,
    check_touch_target,
    color_name,
    contrast_ratio,
    describe_color_pair,
    parse_hex_color,
    suggest_accessible_foreground,
)


class TestParseHexColor(unittest.TestCase):
    def test_six_digit(self):
        self.assertEqual(parse_hex_color("#4CAF50"), (76, 175, 80))

    def test_three_digit(self):
        self.assertEqual(parse_hex_color("#fff"), (255, 255, 255))

    def test_lowercase(self):
        self.assertEqual(parse_hex_color("#1e1e1e"), (30, 30, 30))

    def test_strips_whitespace(self):
        self.assertEqual(parse_hex_color("  #4CAF50  "), (76, 175, 80))

    def test_rejects_invalid(self):
        self.assertIsNone(parse_hex_color("red"))
        self.assertIsNone(parse_hex_color("#12345"))
        self.assertIsNone(parse_hex_color("#1234567"))
        self.assertIsNone(parse_hex_color(""))
        self.assertIsNone(parse_hex_color(None))
        self.assertIsNone(parse_hex_color(123))


class TestContrastRatio(unittest.TestCase):
    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=1)

    def test_white_on_white_is_1(self):
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=1)

    def test_same_color_is_1(self):
        self.assertAlmostEqual(contrast_ratio("#4CAF50", "#4CAF50"), 1.0, places=1)

    def test_is_symmetric(self):
        a = contrast_ratio("#333333", "#eeeeee")
        b = contrast_ratio("#eeeeee", "#333333")
        self.assertAlmostEqual(a, b, places=6)

    def test_known_pair_dark_gray_on_white(self):
        ratio = contrast_ratio("#6b6b6b", "#ffffff")
        self.assertGreaterEqual(ratio, WCAG_AA_NORMAL)
        self.assertLess(ratio, WCAG_AAA_NORMAL)

    def test_invalid_returns_none(self):
        self.assertIsNone(contrast_ratio("nope", "#ffffff"))
        self.assertIsNone(contrast_ratio("#000000", "nope"))


class TestCheckContrast(unittest.TestCase):
    def test_black_on_white_passes_aaa(self):
        report = check_contrast("#000000", "#ffffff")
        self.assertIsNotNone(report)
        self.assertTrue(report.aa_normal)
        self.assertTrue(report.aa_large)
        self.assertTrue(report.aaa_normal)
        self.assertTrue(report.aaa_large)
        self.assertEqual(report.level, "AAA")

    def test_light_gray_on_white_fails(self):
        report = check_contrast("#cccccc", "#ffffff")
        self.assertIsNotNone(report)
        self.assertFalse(report.aa_normal)
        self.assertFalse(report.aa_large)
        self.assertEqual(report.level, "Fail")

    def test_medium_pair_aa_large_only(self):
        report = check_contrast("#949494", "#ffffff")
        self.assertIsNotNone(report)
        self.assertGreaterEqual(report.ratio, WCAG_AA_LARGE)
        self.assertLess(report.ratio, WCAG_AA_NORMAL)
        self.assertFalse(report.aaa_large)
        self.assertEqual(report.level, "AA Large")

    def test_invalid_returns_none(self):
        self.assertIsNone(check_contrast("bad", "#ffffff"))

    def test_report_is_frozen(self):
        report = check_contrast("#000000", "#ffffff")
        with self.assertRaises(AttributeError):
            report.ratio = 1.0


class TestContrastReportLevel(unittest.TestCase):
    def test_aaa_when_normal_passes(self):
        report = ContrastReport(7.5, True, True, True, True)
        self.assertEqual(report.level, "AAA")

    def test_aa_when_only_normal_aa(self):
        report = ContrastReport(5.0, True, True, False, False)
        self.assertEqual(report.level, "AA")

    def test_aaa_large_reports_aaa_when_aa_normal_also_met(self):
        report = ContrastReport(5.0, True, True, True, True)
        self.assertEqual(report.level, "AAA")

    def test_aa_large(self):
        report = ContrastReport(3.5, False, True, False, True)
        self.assertEqual(report.level, "AA Large")

    def test_aa_large_min_ratio(self):
        report = ContrastReport(3.0, False, True, False, False)
        self.assertEqual(report.level, "AA Large")

    def test_fail(self):
        report = ContrastReport(2.0, False, False, False, False)
        self.assertEqual(report.level, "Fail")


class TestTouchTarget(unittest.TestCase):
    def test_44x44_passes_aaa(self):
        report = check_touch_target(44, 44)
        self.assertTrue(report.aa)
        self.assertTrue(report.aaa)
        self.assertEqual(report.level, "AAA")
        self.assertEqual(report.min_side, 44.0)

    def test_24x24_passes_aa_only(self):
        report = check_touch_target(24, 24)
        self.assertTrue(report.aa)
        self.assertFalse(report.aaa)
        self.assertEqual(report.level, "AA")

    def test_16x16_fails(self):
        report = check_touch_target(16, 16)
        self.assertFalse(report.aa)
        self.assertFalse(report.aaa)
        self.assertEqual(report.level, "Fail")

    def test_uses_min_side(self):
        report = check_touch_target(100, 20)
        self.assertFalse(report.aa)
        self.assertEqual(report.min_side, 20.0)

    def test_rectangular_44x30_passes_aa_only(self):
        report = check_touch_target(44, 30)
        self.assertTrue(report.aa)
        self.assertFalse(report.aaa)

    def test_constants(self):
        self.assertEqual(TOUCH_TARGET_AA, 24.0)
        self.assertEqual(TOUCH_TARGET_AAA, 44.0)

    def test_report_is_frozen(self):
        report = check_touch_target(44, 44)
        with self.assertRaises(AttributeError):
            report.width = 10


class TestColorName(unittest.TestCase):
    def test_black(self):
        self.assertEqual(color_name("#000000"), "black")

    def test_white(self):
        self.assertEqual(color_name("#ffffff"), "white")

    def test_red(self):
        self.assertEqual(color_name("#ff0000"), "red")

    def test_green(self):
        name = color_name("#4CAF50")
        self.assertIn("green", name)

    def test_dark_blue(self):
        name = color_name("#00008b")
        self.assertIn("blue", name)
        self.assertTrue("dark" in name or "deep" in name)

    def test_navy_is_dark_blue(self):
        name = color_name("#000080")
        self.assertIn("blue", name)
        self.assertTrue("dark" in name or "deep" in name)

    def test_gray_low_saturation(self):
        self.assertEqual(color_name("#808080"), "gray")

    def test_dark_gray(self):
        self.assertEqual(color_name("#404040"), "dark gray")

    def test_light_gray(self):
        self.assertEqual(color_name("#c0c0c0"), "light gray")

    def test_yellow(self):
        self.assertEqual(color_name("#ffff00"), "yellow")

    def test_orange(self):
        self.assertEqual(color_name("#ffa500"), "orange")

    def test_cyan(self):
        self.assertEqual(color_name("#00ffff"), "cyan")

    def test_purple(self):
        name = color_name("#a020f0")
        self.assertIn("purple", name)

    def test_violet_is_purple(self):
        name = color_name("#8a2be2")
        self.assertIn("purple", name)

    def test_magenta(self):
        self.assertEqual(color_name("#ff00ff"), "magenta")

    def test_muted(self):
        name = color_name("#6b8e6b")
        self.assertIn("muted", name)
        self.assertIn("green", name)

    def test_three_digit_hex(self):
        self.assertEqual(color_name("#f00"), "red")

    def test_invalid_returns_none(self):
        self.assertIsNone(color_name("nope"))
        self.assertIsNone(color_name(None))


class TestDescribeColorPair(unittest.TestCase):
    def test_dark_on_light(self):
        description = describe_color_pair("#000000", "#ffffff")
        self.assertIsNotNone(description)
        self.assertIn("black", description)
        self.assertIn("white", description)
        self.assertIn("AAA", description)

    def test_includes_verdict(self):
        description = describe_color_pair("#cccccc", "#ffffff")
        self.assertIsNotNone(description)
        self.assertIn("Fail", description)

    def test_invalid_returns_none(self):
        self.assertIsNone(describe_color_pair("bad", "#ffffff"))
        self.assertIsNone(describe_color_pair("#000000", "bad"))


class TestAuditThemeColors(unittest.TestCase):
    def test_both_pairs_present(self):
        colors = {"text": "#e0e0e0", "background": "#1e1e1e", "accent": "#4CAF50"}
        results = audit_theme_colors(colors)
        self.assertIn("text/background", results)
        self.assertIn("accent/background", results)
        self.assertTrue(results["text/background"].aa_normal)

    def test_missing_keys_skipped(self):
        results = audit_theme_colors({"text": "#e0e0e0"})
        self.assertEqual(results, {})

    def test_invalid_colors_skipped(self):
        results = audit_theme_colors({"text": "bad", "background": "#1e1e1e"})
        self.assertEqual(results, {})

    def test_empty_dict(self):
        self.assertEqual(audit_theme_colors({}), {})


class TestSuggestAccessibleForeground(unittest.TestCase):
    def test_dark_background_suggests_white(self):
        self.assertEqual(suggest_accessible_foreground("#1e1e1e"), "#ffffff")

    def test_light_background_suggests_black(self):
        self.assertEqual(suggest_accessible_foreground("#ffffff"), "#000000")

    def test_invalid_returns_none(self):
        self.assertIsNone(suggest_accessible_foreground("nope"))

    def test_prefer_dark_on_tie(self):
        result = suggest_accessible_foreground("#777777", prefer_dark=True)
        self.assertIn(result, ("#000000", "#ffffff"))


class TestWcagConstants(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(WCAG_AA_NORMAL, 4.5)
        self.assertEqual(WCAG_AA_LARGE, 3.0)
        self.assertEqual(WCAG_AAA_NORMAL, 7.0)
        self.assertEqual(WCAG_AAA_LARGE, 4.5)


if __name__ == "__main__":
    unittest.main()
