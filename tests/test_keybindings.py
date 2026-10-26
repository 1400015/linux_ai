"""Tests for src.keybindings — GTK-free keybinding management."""

import unittest
from types import SimpleNamespace

from src.keybindings import (
    BUILTIN_ACTIONS,
    DEFAULT_BINDINGS,
    Keybinding,
    KeybindingError,
    KeybindingSet,
    describe_binding,
    find_conflicts,
    format_accelerator,
    format_binding_table,
    is_builtin_action,
    load_from_config,
    merge_with_defaults,
    normalize_accelerator,
    parse_accelerator,
    save_to_config,
    validate_action_name,
    validate_accelerator,
    validate_binding_set,
)


class TestValidateActionName(unittest.TestCase):
    def test_valid_names(self):
        for name in ("send", "clear_input", "toggle_expert", "a", "action_1", "x" * 64):
            self.assertEqual(validate_action_name(name), name)

    def test_empty(self):
        with self.assertRaises(KeybindingError):
            validate_action_name("")

    def test_not_string(self):
        with self.assertRaises(KeybindingError):
            validate_action_name(None)
        with self.assertRaises(KeybindingError):
            validate_action_name(123)

    def test_uppercase_rejected(self):
        with self.assertRaises(KeybindingError):
            validate_action_name("Send")

    def test_starts_with_digit(self):
        with self.assertRaises(KeybindingError):
            validate_action_name("1action")

    def test_special_chars_rejected(self):
        with self.assertRaises(KeybindingError):
            validate_action_name("send-message")
        with self.assertRaises(KeybindingError):
            validate_action_name("send message")

    def test_too_long(self):
        with self.assertRaises(KeybindingError):
            validate_action_name("x" * 65)


class TestValidateAccelerator(unittest.TestCase):
    def test_plain_key(self):
        self.assertEqual(validate_accelerator("Escape"), "Escape")
        self.assertEqual(validate_accelerator("Return"), "Return")
        self.assertEqual(validate_accelerator("F1"), "F1")

    def test_with_modifiers(self):
        self.assertEqual(validate_accelerator("<Control>Return"), "<Control>Return")
        self.assertEqual(validate_accelerator("<Shift><Alt>F1"), "<Shift><Alt>F1")
        self.assertEqual(validate_accelerator("<Primary>a"), "<Primary>a")

    def test_single_letter(self):
        self.assertEqual(validate_accelerator("<Control>e"), "<Control>e")
        self.assertEqual(validate_accelerator("a"), "a")

    def test_whitespace_stripped(self):
        self.assertEqual(validate_accelerator("  Escape  "), "Escape")
        self.assertEqual(validate_accelerator("  <Control>q  "), "<Control>q")

    def test_empty_rejected(self):
        with self.assertRaises(KeybindingError):
            validate_accelerator("")
        with self.assertRaises(KeybindingError):
            validate_accelerator("   ")

    def test_not_string(self):
        with self.assertRaises(KeybindingError):
            validate_accelerator(None)
        with self.assertRaises(KeybindingError):
            validate_accelerator(123)

    def test_unknown_modifier(self):
        with self.assertRaises(KeybindingError):
            validate_accelerator("<Foo>Return")
        with self.assertRaises(KeybindingError):
            validate_accelerator("<Hyper2>a")

    def test_no_key_name(self):
        with self.assertRaises(KeybindingError):
            validate_accelerator("<Control>")
        with self.assertRaises(KeybindingError):
            validate_accelerator("<Shift><Alt>")

    def test_garbage_rejected(self):
        with self.assertRaises(KeybindingError):
            validate_accelerator("!!!")
        with self.assertRaises(KeybindingError):
            validate_accelerator("<Control>Return extra")
        with self.assertRaises(KeybindingError):
            validate_accelerator("ctrl+return")

    def test_too_long(self):
        long_accel = "<Control>" + "a" * 100
        with self.assertRaises(KeybindingError):
            validate_accelerator(long_accel)

    def test_all_known_modifiers(self):
        for mod in ("<Control>", "<Ctrl>", "<Primary>", "<Shift>", "<Alt>",
                     "<Super>", "<Meta>", "<Hyper>"):
            accel = mod + "x"
            self.assertEqual(validate_accelerator(accel), accel)


class TestParseAccelerator(unittest.TestCase):
    def test_plain_key(self):
        mods, key = parse_accelerator("Escape")
        self.assertEqual(mods, [])
        self.assertEqual(key, "Escape")

    def test_with_modifiers(self):
        mods, key = parse_accelerator("<Control>Return")
        self.assertEqual(mods, ["control"])
        self.assertEqual(key, "Return")

    def test_multiple_modifiers(self):
        mods, key = parse_accelerator("<Shift><Alt>F1")
        self.assertEqual(mods, ["shift", "alt"])
        self.assertEqual(key, "F1")

    def test_invalid_raises(self):
        with self.assertRaises(KeybindingError):
            parse_accelerator("<Foo>bar")


class TestFormatAccelerator(unittest.TestCase):
    def test_no_modifiers(self):
        self.assertEqual(format_accelerator([], "Escape"), "Escape")

    def test_with_modifiers(self):
        self.assertEqual(format_accelerator(["control"], "Return"), "<Control>Return")

    def test_primary_to_control(self):
        self.assertEqual(format_accelerator(["primary"], "a"), "<Control>a")

    def test_mod1_to_alt(self):
        self.assertEqual(format_accelerator(["mod1"], "b"), "<Alt>b")

    def test_mod4_to_super(self):
        self.assertEqual(format_accelerator(["mod4"], "c"), "<Super>c")

    def test_roundtrip(self):
        accel = "<Shift><Control>F5"
        mods, key = parse_accelerator(accel)
        rebuilt = format_accelerator(mods, key)
        self.assertEqual(rebuilt, "<Shift><Control>F5")


class TestNormalizeAccelerator(unittest.TestCase):
    def test_primary_becomes_control(self):
        self.assertEqual(normalize_accelerator("<Primary>a"), "<Control>a")

    def test_ctrl_becomes_control(self):
        self.assertEqual(normalize_accelerator("<Ctrl>b"), "<Control>b")

    def test_modifiers_sorted(self):
        # Input order: shift, control -> output: control, shift
        self.assertEqual(
            normalize_accelerator("<Shift><Control>c"),
            "<Control><Shift>c",
        )

    def test_duplicate_modifiers_deduped(self):
        self.assertEqual(
            normalize_accelerator("<Control><Control>d"),
            "<Control>d",
        )

    def test_plain_key_unchanged(self):
        self.assertEqual(normalize_accelerator("Escape"), "Escape")

    def test_case_insensitive_modifiers(self):
        self.assertEqual(normalize_accelerator("<CONTROL>e"), "<Control>e")


class TestKeybinding(unittest.TestCase):
    def test_to_dict(self):
        kb = Keybinding(action="send", accelerator="<Control>Return")
        self.assertEqual(kb.to_dict(), {"send": "<Control>Return"})


class TestKeybindingSet(unittest.TestCase):
    def test_defaults(self):
        kbs = KeybindingSet.defaults()
        self.assertEqual(kbs.get("send"), "<Control>Return")
        self.assertEqual(kbs.get("clear_input"), "Escape")
        self.assertEqual(kbs.get("toggle_expert"), "<Control>e")
        self.assertEqual(kbs.get("close_window"), "<Control>q")
        self.assertEqual(kbs.get("capture_screen"), "<Control>s")
        for action in BUILTIN_ACTIONS:
            self.assertIn(action, kbs.bindings)

    def test_set_and_get(self):
        kbs = KeybindingSet()
        kbs.set("send", "<Control>Return")
        self.assertEqual(kbs.get("send"), "<Control>Return")

    def test_set_validates(self):
        kbs = KeybindingSet()
        with self.assertRaises(KeybindingError):
            kbs.set("bad name", "Escape")
        with self.assertRaises(KeybindingError):
            kbs.set("send", "<Foo>bar")

    def test_remove(self):
        kbs = KeybindingSet.defaults()
        self.assertTrue(kbs.remove("send"))
        self.assertIsNone(kbs.get("send"))
        self.assertFalse(kbs.remove("send"))

    def test_actions_sorted(self):
        kbs = KeybindingSet.defaults()
        self.assertEqual(kbs.actions(), sorted(kbs.actions()))

    def test_from_dict(self):
        kbs = KeybindingSet.from_dict({"send": "<Control>Return", "custom": "F5"})
        self.assertEqual(kbs.get("send"), "<Control>Return")
        self.assertEqual(kbs.get("custom"), "F5")

    def test_from_dict_invalid(self):
        with self.assertRaises(KeybindingError):
            KeybindingSet.from_dict({"send": "<Bad>key"})

    def test_to_dict_roundtrip(self):
        kbs = KeybindingSet.defaults()
        kbs2 = KeybindingSet.from_dict(kbs.to_dict())
        self.assertEqual(kbs.bindings, kbs2.bindings)


class TestFindConflicts(unittest.TestCase):
    def test_no_conflicts(self):
        bindings = {"send": "<Control>Return", "close": "<Control>q"}
        self.assertEqual(find_conflicts(bindings), [])

    def test_conflict_detected(self):
        bindings = {"send": "<Control>Return", "other": "<Control>Return"}
        conflicts = find_conflicts(bindings)
        self.assertEqual(len(conflicts), 1)
        action_a, action_b, accel = conflicts[0]
        self.assertEqual(accel, "<Control>Return")
        self.assertIn(action_a, ("send", "other"))
        self.assertIn(action_b, ("send", "other"))

    def test_conflict_normalized(self):
        # <Primary>a and <Control>a are the same after normalization.
        bindings = {"a": "<Primary>x", "b": "<Control>x"}
        conflicts = find_conflicts(bindings)
        self.assertEqual(len(conflicts), 1)

    def test_multiple_conflicts(self):
        bindings = {
            "a": "<Control>x",
            "b": "<Control>x",
            "c": "<Control>y",
            "d": "<Control>y",
        }
        conflicts = find_conflicts(bindings)
        self.assertEqual(len(conflicts), 2)


class TestMergeWithDefaults(unittest.TestCase):
    def test_empty_user_keeps_defaults(self):
        merged = merge_with_defaults({})
        self.assertEqual(merged, DEFAULT_BINDINGS)

    def test_user_overrides_default(self):
        merged = merge_with_defaults({"send": "<Control>Enter"})
        self.assertEqual(merged["send"], "<Control>Enter")
        self.assertEqual(merged["close_window"], "<Control>q")

    def test_user_disables_action(self):
        merged = merge_with_defaults({"send": ""})
        self.assertNotIn("send", merged)
        self.assertIn("close_window", merged)

    def test_user_adds_custom_action(self):
        merged = merge_with_defaults({"my_action": "F9"})
        self.assertEqual(merged["my_action"], "F9")
        self.assertIn("send", merged)

    def test_none_disables(self):
        merged = merge_with_defaults({"send": None})
        self.assertNotIn("send", merged)


class TestLoadFromConfig(unittest.TestCase):
    def test_loads_defaults_when_missing(self):
        config = SimpleNamespace(get=lambda k, d=None: d)
        kbs = load_from_config(config)
        self.assertEqual(kbs.get("send"), "<Control>Return")

    def test_loads_user_overrides(self):
        stored = {"send": "<Control>Enter", "custom": "F9"}
        config = SimpleNamespace(get=lambda k, d=None: stored if k == "shortcuts.bindings" else d)
        kbs = load_from_config(config)
        self.assertEqual(kbs.get("send"), "<Control>Enter")
        self.assertEqual(kbs.get("custom"), "F9")
        self.assertEqual(kbs.get("close_window"), "<Control>q")

    def test_invalid_config_falls_back(self):
        config = SimpleNamespace(get=lambda k, d=None: "not a dict" if k == "shortcuts.bindings" else d)
        kbs = load_from_config(config)
        self.assertEqual(kbs.get("send"), "<Control>Return")

    def test_invalid_accelerator_falls_back(self):
        stored = {"send": "<Bad>key"}
        config = SimpleNamespace(get=lambda k, d=None: stored if k == "shortcuts.bindings" else d)
        kbs = load_from_config(config)
        self.assertEqual(kbs.get("send"), "<Control>Return")


class TestSaveToConfig(unittest.TestCase):
    def test_saves_only_overrides(self):
        stored: dict = {}
        config = SimpleNamespace(
            set=lambda k, v: stored.__setitem__(k, v),
            flush=lambda: None,
        )
        kbs = KeybindingSet.defaults()
        kbs.set("send", "<Control>Enter")
        save_to_config(kbs, config)
        bindings = stored["shortcuts.bindings"]
        self.assertEqual(bindings, {"send": "<Control>Enter"})

    def test_saves_disabled_action(self):
        stored: dict = {}
        config = SimpleNamespace(
            set=lambda k, v: stored.__setitem__(k, v),
            flush=lambda: None,
        )
        kbs = KeybindingSet.defaults()
        kbs.remove("send")
        save_to_config(kbs, config)
        bindings = stored["shortcuts.bindings"]
        self.assertEqual(bindings, {"send": ""})

    def test_no_changes_saves_empty(self):
        stored: dict = {}
        config = SimpleNamespace(
            set=lambda k, v: stored.__setitem__(k, v),
            flush=lambda: None,
        )
        kbs = KeybindingSet.defaults()
        save_to_config(kbs, config)
        self.assertEqual(stored["shortcuts.bindings"], {})


class TestDescribeBinding(unittest.TestCase):
    def test_plain_key(self):
        self.assertEqual(describe_binding("clear_input", "Escape"), "clear_input: Esc")

    def test_with_modifier(self):
        self.assertEqual(describe_binding("send", "<Control>Return"), "send: Control+Enter")

    def test_multiple_modifiers(self):
        desc = describe_binding("x", "<Shift><Alt>F1")
        self.assertIn("Shift", desc)
        self.assertIn("Alt", desc)
        self.assertIn("F1", desc)

    def test_space_display(self):
        self.assertEqual(describe_binding("x", "space"), "x: Space")

    def test_page_up_display(self):
        self.assertEqual(describe_binding("x", "Page_Up"), "x: PgUp")


class TestFormatBindingTable(unittest.TestCase):
    def test_table_has_header(self):
        kbs = KeybindingSet.defaults()
        table = format_binding_table(kbs)
        lines = table.splitlines()
        self.assertIn("Action", lines[0])
        self.assertIn("Shortcut", lines[0])
        self.assertTrue(lines[1].startswith("---"))

    def test_table_lists_all_actions(self):
        kbs = KeybindingSet.defaults()
        table = format_binding_table(kbs)
        for action in BUILTIN_ACTIONS:
            self.assertIn(action, table)


class TestIsBuiltinAction(unittest.TestCase):
    def test_builtin_actions(self):
        for action in BUILTIN_ACTIONS:
            self.assertTrue(is_builtin_action(action))

    def test_custom_action(self):
        self.assertFalse(is_builtin_action("my_custom_action"))


class TestValidateBindingSet(unittest.TestCase):
    def test_clean_set(self):
        kbs = KeybindingSet.defaults()
        warnings = validate_binding_set(kbs)
        self.assertEqual(warnings, [])

    def test_conflict_warning(self):
        kbs = KeybindingSet()
        kbs.set("send", "<Control>x")
        kbs.set("close_window", "<Control>x")
        warnings = validate_binding_set(kbs)
        self.assertTrue(any("conflict" in w for w in warnings))

    def test_unknown_action_warning(self):
        kbs = KeybindingSet()
        kbs.set("my_custom", "F9")
        warnings = validate_binding_set(kbs)
        self.assertTrue(any("unknown action" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
