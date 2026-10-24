"""Theme files have exactly one owner: names, paths, writes and deletion.

These tests pin the behaviour the window and ConfigManager both depend on,
including the two ways the previous duplication went wrong: a hardcoded
built-in list (a bundled theme added later was deletable) and a name rule
copied next to every path construction (traversal only guarded in one caller).
"""

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import theme_store


class ThemeStoreTestCase(unittest.TestCase):
    """HOME and the bundled directory are isolated for every test."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.home_patch = patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.bundled = self.root / "bundled"
        self.bundled.mkdir()
        self.user = self.home / ".config" / "linux_ai_assistant" / "themes"

    def bundle(self, *names):
        for name in names:
            (self.bundled / f"{name}.json").write_text("{}", encoding="utf-8")

    @staticmethod
    def document(name="solarized-dark", **colors):
        palette = {"background": "#1e1e1e", "text": "#e0e0e0", "accent": "#4CAF50"}
        palette.update(colors)
        return {"name": name, "description": "", "colors": palette}


class NameValidationTests(ThemeStoreTestCase):
    def test_simple_identifiers_are_accepted(self):
        for name in ("dark", "my-theme", "My_Theme_2", "a" * 64):
            with self.subTest(name=name):
                self.assertEqual(theme_store.validate_theme_name(name), name)
                self.assertFalse(theme_store.is_builtin(name, self.bundled))

    def test_names_that_could_escape_the_directory_are_rejected(self):
        for name in ("", " ", "with space", "../config", "a/b", "a\\b", ".",
                     "dark.json", "a" * 65, None, 7, "tema ç"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    theme_store.validate_theme_name(name)

    def test_unknown_names_are_never_treated_as_built_in(self):
        self.assertFalse(theme_store.is_builtin("../dark", self.bundled))
        self.assertFalse(theme_store.is_builtin(None, self.bundled))

    def test_user_theme_path_is_built_under_the_user_directory(self):
        path = theme_store.user_theme_path("my-theme")
        self.assertEqual(path, self.user / "my-theme.json")
        with self.assertRaises(ValueError):
            theme_store.user_theme_path("../escape")

    def test_a_traversal_name_is_refused_before_any_path_is_built(self):
        with self.assertRaises(ValueError):
            theme_store.remove_theme("../../config", self.bundled)


class ListingTests(ThemeStoreTestCase):
    def test_bundled_themes_are_read_from_the_directory_not_a_list(self):
        self.bundle("dark", "light", "zeta", "alpha")
        self.assertEqual(theme_store.list_bundled_themes(self.bundled),
                         ["alpha", "dark", "light", "zeta"])
        self.assertTrue(theme_store.is_builtin("zeta", self.bundled))

    def test_a_bundled_theme_added_later_is_still_protected(self):
        # The window used to refuse removal of exactly four hardcoded names;
        # a fifth bundled theme could be "removed" (which did nothing at all).
        self.bundle("aurora")
        self.assertTrue(theme_store.is_builtin("aurora", self.bundled))
        with self.assertRaises(ValueError):
            theme_store.remove_theme("aurora", self.bundled)
        self.assertTrue((self.bundled / "aurora.json").exists())

    def test_user_listing_is_empty_until_a_theme_is_written(self):
        self.assertEqual(theme_store.list_user_themes(), [])
        theme_store.save_theme(self.document("custom"))
        self.assertEqual(theme_store.list_user_themes(), ["custom"])


class SaveThemeTests(ThemeStoreTestCase):
    def test_writes_a_readable_file_with_private_permissions(self):
        path = theme_store.save_theme(self.document("custom"))
        self.assertEqual(path, self.user / "custom.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], "custom")
        # No temporary file is left next to it.
        self.assertEqual(sorted(p.name for p in self.user.iterdir()), ["custom.json"])

    def test_replaces_an_existing_theme_atomically(self):
        theme_store.save_theme(self.document("custom", background="#000000"))
        path = theme_store.save_theme(self.document("custom", background="#ffffff"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["colors"]["background"],
                         "#ffffff")

    def test_invalid_colours_are_rejected_without_creating_a_file(self):
        bad = [
            {"background": "#12345"},     # not 3 or 6 hex digits
            {"background": "#12345678"},  # CSS4 rgba hex: GTK3's CSS parser fails
            {"background": "red"},        # not a hex colour
            {"background": "#1e1e1e80"},
        ]
        for colors in bad:
            with self.subTest(colors=colors):
                with self.assertRaises(ValueError):
                    theme_store.save_theme(self.document("custom", **colors))
        self.assertFalse(self.user.exists())

    def test_unknown_colour_keys_and_malformed_documents_are_rejected(self):
        for theme in (
            # A colour key outside the documented palette: the store must not
            # write a key the renderer would silently drop.
            dict(self.document("custom"), colors={"highlight": "#ffffff"}),
            dict(self.document("custom"), colors="red"),        # wrong type
            dict(self.document("custom"), colors={"background": "#000000"}),  # incomplete
            dict(self.document("custom"), name="../escape"),    # bad name
            {"description": "no name"},
            "not-an-object",
        ):
            with self.subTest(theme=theme):
                with self.assertRaises(ValueError):
                    theme_store.save_theme(theme)
        self.assertFalse(self.user.exists())

    def test_optional_colours_are_validated_when_present(self):
        self.assertTrue(theme_store.save_theme(
            self.document("custom", secondary="#2d2d2d", tertiary="#252525")).exists())
        with self.assertRaises(ValueError):
            theme_store.save_theme(self.document("custom", tertiary="nonsense"))


class RemoveThemeTests(ThemeStoreTestCase):
    def test_removes_a_user_theme_once_and_reports_it(self):
        theme_store.save_theme(self.document("custom"))
        removed = theme_store.remove_theme("custom", self.bundled)
        self.assertEqual(removed, self.user / "custom.json")
        self.assertFalse((self.user / "custom.json").exists())
        # A second removal finds nothing and reports None instead of raising.
        self.assertIsNone(theme_store.remove_theme("custom", self.bundled))

    def test_bundled_names_are_refused_even_when_a_user_copy_exists(self):
        self.bundle("dark")
        theme_store.save_theme(self.document("dark"))
        with self.assertRaises(ValueError):
            theme_store.remove_theme("dark", self.bundled)
        self.assertTrue((self.user / "dark.json").exists())

    def test_a_symlink_out_of_the_directory_is_refused(self):
        self.user.mkdir(parents=True)
        outside = self.root / "outside.json"
        outside.write_text("keep me", encoding="utf-8")
        (self.user / "escape.json").symlink_to(outside)
        with self.assertRaises(ValueError):
            theme_store.remove_theme("escape", self.bundled)
        self.assertTrue(outside.exists())


class ConfigManagerIntegrationTests(ThemeStoreTestCase):
    """ConfigManager reads themes through the same store the window writes."""

    def setUp(self):
        super().setUp()
        from src.config_manager import ConfigManager
        self.config = ConfigManager(str(self.home / "config.json"))
        self.addCleanup(self.config.flush)
        self.config._themes_dir = self.bundled

    def test_available_themes_merge_bundled_and_user_themes(self):
        self.bundle("dark", "light")
        theme_store.save_theme(self.document("custom"))
        theme_store.save_theme(self.document("dark", background="#101010"))
        self.assertEqual(self.config.get_available_themes(),
                         ["custom", "dark", "light"])

    def test_bundled_theme_directory_comes_from_the_store(self):
        import src.config_manager as config_module
        from src.config_manager import ConfigManager
        # config_manager imported the function by name, so patch it there.
        with patch.object(config_module, "bundled_themes_dir", return_value=self.bundled):
            manager = ConfigManager(str(self.home / "other.json"))
            self.addCleanup(manager.flush)
        self.assertEqual(manager._themes_dir, self.bundled)

    def test_user_theme_shadows_the_bundled_one(self):
        self.bundle("dark")
        theme_store.save_theme(self.document("dark", background="#010203"))
        loaded = self.config._load_theme("dark")
        self.assertEqual(loaded["colors"]["background"], "#010203")

    def test_traversal_name_never_reaches_the_filesystem(self):
        self.assertIsNone(self.config._load_theme("../../config"))
        self.assertEqual(self.config.get_available_themes(),
                         sorted(theme_store.list_bundled_themes(self.bundled)))


if __name__ == "__main__":
    unittest.main()
