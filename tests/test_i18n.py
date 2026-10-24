"""The catalog contract: what every future translation edit must preserve.

The review found four silent failures the runtime never reports: `_()`
literals with no Portuguese entry (the UI silently fell back to English),
catalog keys nothing calls (dead strings nobody dares delete), a key defined
twice in the same catalog (`Connection not tested` was, so the first value
was unreachable), and a `{placeholder}` present in the key but not in the
translation (`str.format` raises at the call site). These tests re-derive
each invariant from the source tree, so a regression names the exact string.
"""

import ast
import re
import unittest
from pathlib import Path

from src import i18n
from src.i18n import LANGUAGE_NAMES, OFFLINE_TEXTS, SUPPORTED_LANGUAGES, TRANSLATIONS

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("src", "plugins", "scripts")
MIN_PREFIX = 15


def _python_files():
    for directory in SOURCE_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if path.name != "i18n.py":
                yield path


def _translated_literals():
    """Every string passed to ``_()`` as a literal, across the runtime code."""
    found = set()
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


def _string_constants():
    """All string constants in the runtime code (label tables, concatenated statuses)."""
    found = set()
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def _is_referenced(key, literals, constants):
    """A key counts as used when a literal calls it, a constant repeats it, or a
    constant prefixes it (the global-shortcut statuses are string concatenations)."""
    if key in literals or key in constants:
        return True
    return any(value and len(value) >= MIN_PREFIX and key.startswith(value) for value in constants)


def _placeholders(text):
    return sorted(re.findall(r"\{(\w+)[^}]*\}", text))


class CatalogContractTests(unittest.TestCase):
    def test_every_translated_literal_has_a_portuguese_entry(self):
        """`_()` falls back to English, so a missing entry ships untranslated UI."""
        missing = sorted(k for k in _translated_literals() if k not in TRANSLATIONS["pt"])
        self.assertEqual(missing, [], "add these keys to TRANSLATIONS['pt']")

    def test_no_catalog_key_is_orphaned(self):
        """A key nothing references is dead weight that drifts out of sync."""
        literals, constants = _translated_literals(), _string_constants()
        orphans = sorted(k for k in TRANSLATIONS["pt"] if not _is_referenced(k, literals, constants))
        self.assertEqual(orphans, [], "remove these keys or reference them")

    def test_no_key_is_defined_twice_in_the_same_catalog(self):
        """A duplicate silently shadows the first value; both copies rot."""
        tree = ast.parse((ROOT / "src" / "i18n.py").read_text(encoding="utf-8"))
        seen = set()
        duplicates = []

        def collect(keys, owner):
            for key in keys:
                if not isinstance(key, ast.Constant):
                    continue
                marker = (owner, key.value)
                if marker in seen:
                    duplicates.append((owner, key.value))
                seen.add(marker)

        for node in tree.body:
            if (isinstance(node, ast.Assign) and node.targets
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "TRANSLATIONS"):
                for name, value in zip(node.value.keys, node.value.values):
                    if isinstance(name, ast.Constant) and name.value in TRANSLATIONS:
                        collect(value.keys, name.value)
            elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "update"
                    and isinstance(node.value.func.value, ast.Subscript)
                    and isinstance(node.value.func.value.value, ast.Name)
                    and node.value.func.value.value.id == "TRANSLATIONS"):
                self.fail("TRANSLATIONS update fragments are forbidden; edit the dict"
                          " literal (line {})".format(node.lineno))
        self.assertEqual(sorted(duplicates), [])

    def test_placeholders_match_between_key_and_translation(self):
        """`"x {path}".format(...)` raises KeyError when the translation drops the slot."""
        mismatches = []
        for lang, catalog in TRANSLATIONS.items():
            for key, value in catalog.items():
                if _placeholders(key) != _placeholders(value):
                    mismatches.append((lang, key))
        self.assertEqual(mismatches, [])

    def test_offline_placeholders_match_the_english_template(self):
        """offline_text() formats with the same kwargs for every language."""
        base = OFFLINE_TEXTS["en"]
        mismatches = []
        for lang, catalog in OFFLINE_TEXTS.items():
            for key, value in catalog.items():
                if key in base and _placeholders(base[key]) != _placeholders(value):
                    mismatches.append((lang, key))
        self.assertEqual(mismatches, [])

    def test_offline_catalogs_do_not_invent_keys(self):
        """offline_text() raises KeyError on a key the English template lacks."""
        english = OFFLINE_TEXTS["en"]
        for lang, catalog in OFFLINE_TEXTS.items():
            invented = sorted(set(catalog) - set(english))
            self.assertEqual(invented, [],
                             "OFFLINE_TEXTS[{}] has keys English lacks".format(lang))

    def test_language_names_cover_supported_languages(self):
        """The settings combo shows LANGUAGE_NAMES; an uncovered code renders raw."""
        self.assertEqual(set(LANGUAGE_NAMES), set(SUPPORTED_LANGUAGES))

    def test_non_english_catalogs_are_subsets_of_english_authored_keys(self):
        """es/fr/de are partial by design, but may not invent keys English lacks."""
        for lang in ("es", "fr", "de"):
            invented = sorted(k for k in TRANSLATIONS[lang] if k not in TRANSLATIONS["pt"])
            self.assertEqual(invented, [], "TRANSLATIONS[{}] invents keys".format(lang))

    def test_supported_languages_are_available(self):
        self.assertEqual(SUPPORTED_LANGUAGES, tuple(i18n.available_languages()))
        self.assertIn("en", SUPPORTED_LANGUAGES)
        for lang in SUPPORTED_LANGUAGES:
            self.assertNotEqual(TRANSLATIONS.get(lang, {}).get("Quit"), "", lang)


if __name__ == "__main__":
    unittest.main()
