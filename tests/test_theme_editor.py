"""Tests for src.theme_editor — GTK-free theme editing core."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.theme_editor import (
    COLOR_KEYS,
    SYNTAX_KEYS,
    ThemeDocument,
    ThemeEditorError,
    apply_theme,
    contrast_report,
    default_theme,
    delete_theme,
    diff_themes,
    duplicate_theme,
    export_theme,
    format_contrast_text,
    format_theme_text,
    import_theme,
    is_user_theme,
    list_all_themes,
    load_theme_document,
    rename_theme,
    save_theme_document,
    set_color,
    set_syntax_color,
    set_ui_value,
    suggest_fix,
    validate_theme_document,
)

BUNDLED_DIR = Path(__file__).parent.parent / "themes"


class TestThemeDocument(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        doc = default_theme("my-theme", "test theme")
        data = doc.to_dict()
        self.assertEqual(data["name"], "my-theme")
        self.assertEqual(data["description"], "test theme")
        self.assertIn("colors", data)
        self.assertIn("ui", data)
        self.assertIn("syntax_highlighting", data)
        doc2 = ThemeDocument.from_dict(data)
        self.assertEqual(doc2.name, doc.name)
        self.assertEqual(doc2.colors, doc.colors)

    def test_from_dict_missing_name(self):
        with self.assertRaises(ThemeEditorError):
            ThemeDocument.from_dict({"colors": {}})

    def test_from_dict_not_dict(self):
        with self.assertRaises(ThemeEditorError):
            ThemeDocument.from_dict([1, 2, 3])

    def test_from_dict_bad_colors(self):
        with self.assertRaises(ThemeEditorError):
            ThemeDocument.from_dict({"name": "x", "colors": "not a dict"})

    def test_default_theme_has_all_color_keys(self):
        doc = default_theme("test")
        for key in COLOR_KEYS:
            self.assertIn(key, doc.colors)

    def test_default_theme_has_all_syntax_keys(self):
        doc = default_theme("test")
        for key in SYNTAX_KEYS:
            self.assertIn(key, doc.syntax_highlighting)

    def test_default_theme_rejects_bad_name(self):
        with self.assertRaises(ValueError):
            default_theme("../etc/passwd")
        with self.assertRaises(ValueError):
            default_theme("")


class TestSetColor(unittest.TestCase):
    def test_set_valid_color(self):
        doc = default_theme("t")
        set_color(doc, "background", "#FF0000")
        self.assertEqual(doc.colors["background"], "#ff0000")

    def test_set_short_hex(self):
        doc = default_theme("t")
        set_color(doc, "accent", "#0f0")
        self.assertEqual(doc.colors["accent"], "#0f0")

    def test_set_invalid_color(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_color(doc, "background", "red")
        with self.assertRaises(ThemeEditorError):
            set_color(doc, "background", "#gggggg")

    def test_set_unknown_key(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_color(doc, "unknown_key", "#000000")

    def test_set_all_color_keys(self):
        doc = default_theme("t")
        for key in COLOR_KEYS:
            set_color(doc, key, "#123456")
            self.assertEqual(doc.colors[key], "#123456")


class TestSetSyntaxColor(unittest.TestCase):
    def test_set_valid(self):
        doc = default_theme("t")
        set_syntax_color(doc, "keyword", "#ff0000")
        self.assertEqual(doc.syntax_highlighting["keyword"], "#ff0000")

    def test_set_invalid_color(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_syntax_color(doc, "keyword", "not-a-color")

    def test_set_unknown_key(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_syntax_color(doc, "unknown", "#000000")


class TestSetUiValue(unittest.TestCase):
    def test_set_font_family(self):
        doc = default_theme("t")
        set_ui_value(doc, "font_family", "DejaVu Sans Mono")
        self.assertEqual(doc.ui["font_family"], "DejaVu Sans Mono")

    def test_set_font_family_empty(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "font_family", "")

    def test_set_font_family_too_long(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "font_family", "x" * 65)

    def test_set_font_size_int(self):
        doc = default_theme("t")
        set_ui_value(doc, "font_size", 16)
        self.assertEqual(doc.ui["font_size"], 16)

    def test_set_font_size_string(self):
        doc = default_theme("t")
        set_ui_value(doc, "font_size", "14")
        self.assertEqual(doc.ui["font_size"], 14)

    def test_set_font_size_out_of_range(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "font_size", 5)
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "font_size", 73)

    def test_set_font_size_bool_rejected(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "font_size", True)

    def test_set_border_radius(self):
        doc = default_theme("t")
        set_ui_value(doc, "border_radius", 20)
        self.assertEqual(doc.ui["border_radius"], 20)

    def test_set_border_radius_out_of_range(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "border_radius", -1)
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "border_radius", 41)

    def test_set_opacity(self):
        doc = default_theme("t")
        set_ui_value(doc, "opacity", 0.8)
        self.assertAlmostEqual(doc.ui["opacity"], 0.8)

    def test_set_opacity_string(self):
        doc = default_theme("t")
        set_ui_value(doc, "opacity", "0.95")
        self.assertAlmostEqual(doc.ui["opacity"], 0.95)

    def test_set_opacity_out_of_range(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "opacity", 0.05)
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "opacity", 1.5)

    def test_set_unknown_key(self):
        doc = default_theme("t")
        with self.assertRaises(ThemeEditorError):
            set_ui_value(doc, "unknown", "value")


class TestRenameTheme(unittest.TestCase):
    def test_rename(self):
        doc = default_theme("old-name")
        rename_theme(doc, "new-name")
        self.assertEqual(doc.name, "new-name")

    def test_rename_same_name(self):
        doc = default_theme("same")
        rename_theme(doc, "same")
        self.assertEqual(doc.name, "same")

    def test_rename_invalid_name(self):
        doc = default_theme("t")
        with self.assertRaises(ValueError):
            rename_theme(doc, "../bad")

    def test_rename_conflict_with_bundled(self):
        doc = default_theme("my-theme")
        with self.assertRaises(ThemeEditorError):
            rename_theme(doc, "dark")


class TestContrastReport(unittest.TestCase):
    def test_dark_theme_passes(self):
        doc = default_theme("t")
        report = contrast_report(doc)
        self.assertIn("text/background", report)
        self.assertTrue(report["text/background"]["passes_aa_normal"])

    def test_low_contrast_detected(self):
        doc = default_theme("t")
        doc.colors["text"] = "#1e1e1e"
        doc.colors["background"] = "#1e1e1e"
        report = contrast_report(doc)
        self.assertFalse(report["text/background"]["passes_aa_normal"])
        self.assertAlmostEqual(report["text/background"]["ratio"], 1.0, places=1)

    def test_light_theme(self):
        doc = default_theme("t")
        doc.colors["background"] = "#ffffff"
        doc.colors["text"] = "#333333"
        report = contrast_report(doc)
        self.assertTrue(report["text/background"]["passes_aa_normal"])


class TestSuggestFix(unittest.TestCase):
    def test_dark_background_suggests_white(self):
        doc = default_theme("t")
        doc.colors["background"] = "#000000"
        suggestion = suggest_fix(doc, "background")
        self.assertEqual(suggestion, "#ffffff")

    def test_light_background_suggests_black(self):
        doc = default_theme("t")
        doc.colors["background"] = "#ffffff"
        suggestion = suggest_fix(doc, "background")
        self.assertEqual(suggestion, "#000000")

    def test_unknown_key(self):
        doc = default_theme("t")
        self.assertIsNone(suggest_fix(doc, "unknown"))

    def test_invalid_color(self):
        doc = default_theme("t")
        doc.colors["background"] = "invalid"
        self.assertIsNone(suggest_fix(doc, "background"))


class TestValidateThemeDocument(unittest.TestCase):
    def test_valid_theme_no_warnings(self):
        doc = default_theme("t")
        warnings = validate_theme_document(doc)
        self.assertEqual(warnings, [])

    def test_missing_required_color(self):
        doc = default_theme("t")
        del doc.colors["background"]
        warnings = validate_theme_document(doc)
        self.assertTrue(any("background" in w for w in warnings))

    def test_low_contrast_warning(self):
        doc = default_theme("t")
        doc.colors["text"] = "#1e1e1e"
        doc.colors["background"] = "#1e1e1e"
        warnings = validate_theme_document(doc)
        self.assertTrue(any("contrast" in w.lower() for w in warnings))

    def test_unknown_color_key(self):
        doc = default_theme("t")
        doc.colors["unknown"] = "#000000"
        warnings = validate_theme_document(doc)
        self.assertTrue(any("unknown colour key" in w for w in warnings))

    def test_invalid_syntax_color(self):
        doc = default_theme("t")
        doc.syntax_highlighting["keyword"] = "not-a-color"
        warnings = validate_theme_document(doc)
        self.assertTrue(any("syntax" in w.lower() for w in warnings))


class TestDiffThemes(unittest.TestCase):
    def test_no_changes(self):
        a = default_theme("t")
        b = default_theme("t")
        diff = diff_themes(a, b)
        self.assertEqual(diff, {})

    def test_color_change(self):
        a = default_theme("t")
        b = default_theme("t")
        b.colors["background"] = "#000000"
        diff = diff_themes(a, b)
        self.assertIn("colors", diff)
        self.assertEqual(len(diff["colors"]), 1)
        key, old, new = diff["colors"][0]
        self.assertEqual(key, "background")
        self.assertEqual(new, "#000000")

    def test_ui_change(self):
        a = default_theme("t")
        b = default_theme("t")
        b.ui["font_size"] = 16
        diff = diff_themes(a, b)
        self.assertIn("ui", diff)
        self.assertEqual(diff["ui"][0][0], "font_size")

    def test_syntax_change(self):
        a = default_theme("t")
        b = default_theme("t")
        b.syntax_highlighting["keyword"] = "#ff0000"
        diff = diff_themes(a, b)
        self.assertIn("syntax_highlighting", diff)


class TestFormatText(unittest.TestCase):
    def test_format_theme_text(self):
        doc = default_theme("my-theme", "a test theme")
        text = format_theme_text(doc)
        self.assertIn("my-theme", text)
        self.assertIn("a test theme", text)
        self.assertIn("background", text)
        self.assertIn("font_size", text)

    def test_format_contrast_text(self):
        doc = default_theme("t")
        text = format_contrast_text(doc)
        self.assertIn("Contrast report", text)
        self.assertIn("text/background", text)

    def test_format_contrast_text_no_data(self):
        doc = ThemeDocument(name="t", colors={})
        text = format_contrast_text(doc)
        self.assertIn("No contrast data", text)


class TestLoadThemeDocument(unittest.TestCase):
    def test_load_bundled_theme(self):
        doc = load_theme_document("dark", BUNDLED_DIR)
        self.assertEqual(doc.name, "Dark")
        self.assertIn("background", doc.colors)
        self.assertEqual(doc.colors["background"], "#1e1e1e")

    def test_load_nonexistent(self):
        with self.assertRaises(ThemeEditorError):
            load_theme_document("nonexistent-theme-xyz", BUNDLED_DIR)

    def test_load_invalid_name(self):
        with self.assertRaises(ValueError):
            load_theme_document("../etc/passwd", BUNDLED_DIR)


class TestSaveAndDelete(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._home_patch = patch("pathlib.Path.home", return_value=Path(self._tmp.name))
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_save_and_load(self):
        doc = default_theme("my-saved-theme")
        set_color(doc, "background", "#112233")
        path = save_theme_document(doc)
        self.assertTrue(path.is_file())
        loaded = load_theme_document("my-saved-theme", BUNDLED_DIR)
        self.assertEqual(loaded.colors["background"], "#112233")

    def test_save_rejects_invalid_syntax_color(self):
        doc = default_theme("bad-syntax")
        doc.syntax_highlighting["keyword"] = "not-a-color"
        with self.assertRaises(ThemeEditorError):
            save_theme_document(doc)

    def test_delete_user_theme(self):
        doc = default_theme("to-delete")
        save_theme_document(doc)
        result = delete_theme("to-delete", BUNDLED_DIR)
        self.assertIsNotNone(result)
        with self.assertRaises(ThemeEditorError):
            load_theme_document("to-delete", BUNDLED_DIR)

    def test_delete_builtin_refused(self):
        with self.assertRaises(ValueError):
            delete_theme("dark", BUNDLED_DIR)

    def test_delete_nonexistent(self):
        result = delete_theme("nonexistent-xyz", BUNDLED_DIR)
        self.assertIsNone(result)


class TestListThemes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._home_patch = patch("pathlib.Path.home", return_value=Path(self._tmp.name))
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_list_includes_bundled(self):
        themes = list_all_themes(BUNDLED_DIR)
        self.assertIn("dark", themes)
        self.assertIn("light", themes)
        self.assertIn("dracula", themes)

    def test_list_includes_user_themes(self):
        doc = default_theme("my-custom")
        save_theme_document(doc)
        themes = list_all_themes(BUNDLED_DIR)
        self.assertIn("my-custom", themes)

    def test_is_user_theme(self):
        self.assertFalse(is_user_theme("dark", BUNDLED_DIR))
        doc = default_theme("user-only")
        save_theme_document(doc)
        self.assertTrue(is_user_theme("user-only", BUNDLED_DIR))


class TestDuplicateTheme(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._home_patch = patch("pathlib.Path.home", return_value=Path(self._tmp.name))
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_duplicate_bundled(self):
        doc = duplicate_theme("dark", "my-dark-copy", BUNDLED_DIR)
        self.assertEqual(doc.name, "my-dark-copy")
        self.assertEqual(doc.colors["background"], "#1e1e1e")

    def test_duplicate_conflict(self):
        with self.assertRaises(ThemeEditorError):
            duplicate_theme("dark", "light", BUNDLED_DIR)


class TestExportImport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_export_and_import(self):
        doc = default_theme("export-test")
        set_color(doc, "accent", "#ff8800")
        path = self.tmp / "exported.json"
        export_theme(doc, path)
        self.assertTrue(path.is_file())
        imported = import_theme(path)
        self.assertEqual(imported.name, "export-test")
        self.assertEqual(imported.colors["accent"], "#ff8800")

    def test_export_refuses_overwrite(self):
        doc = default_theme("t")
        path = self.tmp / "exists.json"
        path.write_text("{}")
        with self.assertRaises(ThemeEditorError):
            export_theme(doc, path)

    def test_import_nonexistent(self):
        with self.assertRaises(ThemeEditorError):
            import_theme(self.tmp / "nope.json")

    def test_import_invalid_json(self):
        path = self.tmp / "bad.json"
        path.write_text("not json{")
        with self.assertRaises(ThemeEditorError):
            import_theme(path)

    def test_import_missing_colors(self):
        path = self.tmp / "incomplete.json"
        path.write_text(json.dumps({"name": "bad", "colors": {"background": "#000"}}))
        with self.assertRaises(ThemeEditorError):
            import_theme(path)


class TestApplyTheme(unittest.TestCase):
    def test_apply_sets_config_keys(self):
        doc = default_theme("apply-test")
        set_color(doc, "background", "#112233")
        set_color(doc, "text", "#ffffff")
        set_color(doc, "accent", "#ff8800")
        set_ui_value(doc, "font_size", 16)
        set_ui_value(doc, "opacity", 0.8)

        stored: dict = {}
        config = SimpleNamespace(
            set=lambda k, v: stored.__setitem__(k, v),
            flush=lambda: None,
        )
        apply_theme(doc, config)
        self.assertEqual(stored["app.theme"], "apply-test")
        self.assertEqual(stored["ui.font_family"], "Monospace")
        self.assertEqual(stored["ui.font_size"], 16)
        self.assertEqual(stored["ui.background_color"], "#112233")
        self.assertEqual(stored["ui.text_color"], "#ffffff")
        self.assertEqual(stored["ui.accent_color"], "#ff8800")
        self.assertAlmostEqual(stored["app.opacity"], 0.8)


if __name__ == "__main__":
    unittest.main()
