"""Tests for src.sync_bundle — GTK-free sync bundle (settings + history)."""

import json
import unittest

from src.sync_bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    DEFAULT_MAX_BUNDLE_BYTES,
    FORBIDDEN_SETTINGS_KEYS,
    SYNCABLE_SETTINGS,
    ImportPlan,
    SyncBundle,
    SyncBundleError,
    apply_settings,
    build_bundle,
    export_settings,
    export_sessions,
    merge_sessions,
    parse_bundle,
    plan_import,
    serialize_bundle,
)


def make_config():
    return {
        "app": {
            "name": "Linux AI Assistant",
            "width": 400,
            "height": 500,
            "x_position": 100,
            "y_position": 100,
            "opacity": 0.9,
            "always_on_top": True,
            "auto_start": False,
            "theme": "dark",
            "encryption_enabled": False,
            "expert_mode": False,
            "language": "pt",
            "dock_mode": "float",
            "dock_edge": "right",
            "button_edge": "right",
            "global_shortcut_enabled": False,
            "global_shortcut": "<Ctrl><Alt>space",
            "display_independent_watchdog": False,
        },
        "api": {
            "default_provider": "openrouter",
            "providers": {
                "openrouter": {
                    "api_key": "sk-secret-do-not-sync",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "google/gemini-2.5-flash",
                    "timeout": 30,
                },
                "local_llm": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "llama3.2",
                    "timeout": 120,
                    "backend": "ollama",
                    "strict_local": True,
                },
            },
        },
        "assistance": {"mode": "auto"},
        "pomodoro": {
            "enabled": True,
            "work_seconds": 1500,
            "short_break_seconds": 300,
            "long_break_seconds": 900,
            "cycles_before_long_break": 4,
        },
        "context": {"max_messages": 20, "max_chars": 12000},
        "features": {
            "screen_capture": True,
            "ocr_enabled": True,
            "expert_mode": True,
            "file_edit": True,
            "system_info": True,
        },
        "permissions": {
            "require_sudo": True,
            "allowed_edit_dirs": ["/home/user/projects"],
        },
        "ui": {
            "font_family": "Monospace",
            "font_size": 12,
            "background_color": "#1e1e1e",
            "text_color": "#e0e0e0",
            "accent_color": "#4CAF50",
            "border_radius": 10,
        },
        "plugins": {"enabled": ["example_provider"]},
    }


def make_sessions():
    return [
        {
            "id": "abc123",
            "title": "First conversation",
            "created_at": 1000.0,
            "updated_at": 2000.0,
            "archived": False,
            "messages": [
                {"role": "user", "content": "hello", "timestamp": 1000.0},
                {"role": "assistant", "content": "hi there", "timestamp": 1001.0},
            ],
            "task": {"some": "runtime state"},
            "diagnostic": {"id": "net-route", "step": 1},
        },
        {
            "id": "def456",
            "title": "Second conversation",
            "created_at": 3000.0,
            "updated_at": 4000.0,
            "archived": True,
            "messages": [
                {"role": "user", "content": "question", "timestamp": 3000.0},
            ],
        },
    ]


class TestExportSettings(unittest.TestCase):
    def test_exports_whitelisted_keys(self):
        exported = export_settings(make_config())
        self.assertEqual(exported["app.language"], "pt")
        self.assertEqual(exported["app.theme"], "dark")
        self.assertEqual(exported["assistance.mode"], "auto")
        self.assertEqual(exported["pomodoro.work_seconds"], 1500)
        self.assertEqual(exported["context.max_messages"], 20)
        self.assertEqual(exported["ui.font_family"], "Monospace")
        self.assertEqual(exported["permissions.allowed_edit_dirs"], ["/home/user/projects"])
        self.assertEqual(exported["api.default_provider"], "openrouter")
        self.assertEqual(exported["api.providers.local_llm.model"], "llama3.2")

    def test_never_exports_credentials(self):
        exported = export_settings(make_config())
        for key in exported:
            self.assertNotIn("api_key", key)
        flat = json.dumps(exported)
        self.assertNotIn("sk-secret-do-not-sync", flat)

    def test_never_exports_machine_local_keys(self):
        exported = export_settings(make_config())
        self.assertNotIn("app.width", exported)
        self.assertNotIn("app.height", exported)
        self.assertNotIn("app.x_position", exported)
        self.assertNotIn("app.y_position", exported)
        self.assertNotIn("app.opacity", exported)
        self.assertNotIn("app.name", exported)

    def test_never_exports_encryption_flag_or_plugins(self):
        exported = export_settings(make_config())
        self.assertNotIn("app.encryption_enabled", exported)
        self.assertNotIn("plugins.enabled", exported)

    def test_type_mismatch_skipped(self):
        config = make_config()
        config["app"]["theme"] = 123  # wrong type
        config["context"]["max_messages"] = "20"  # wrong type
        config["pomodoro"]["enabled"] = "yes"  # wrong type
        exported = export_settings(config)
        self.assertNotIn("app.theme", exported)
        self.assertNotIn("context.max_messages", exported)
        self.assertNotIn("pomodoro.enabled", exported)

    def test_bool_not_treated_as_int(self):
        config = make_config()
        config["pomodoro"]["work_seconds"] = True  # bool is int subclass
        exported = export_settings(config)
        self.assertNotIn("pomodoro.work_seconds", exported)

    def test_int_not_treated_as_bool(self):
        config = make_config()
        config["pomodoro"]["enabled"] = 1  # int, not bool
        exported = export_settings(config)
        self.assertNotIn("pomodoro.enabled", exported)

    def test_missing_keys_skipped(self):
        exported = export_settings({"app": {}})
        self.assertEqual(exported, {})

    def test_local_llm_api_key_never_exported(self):
        config = make_config()
        config["api"]["providers"]["local_llm"]["api_key"] = "local-secret"
        exported = export_settings(config)
        flat = json.dumps(exported)
        self.assertNotIn("local-secret", flat)
        self.assertNotIn("api.providers.local_llm.api_key", exported)

    def test_forbidden_prefix_blocks_provider_keys(self):
        config = make_config()
        # Even if someone adds a provider key to the whitelist, the prefix
        # defense must block it.
        config["api"]["providers"]["openrouter"]["model"] = "secret-model"
        exported = export_settings(config)
        self.assertNotIn("api.providers.openrouter.model", exported)


class TestExportSessions(unittest.TestCase):
    def test_exports_fields(self):
        exported = export_sessions(make_sessions())
        self.assertEqual(len(exported), 2)
        first = exported[0]
        self.assertEqual(first["id"], "abc123")
        self.assertEqual(first["title"], "First conversation")
        self.assertEqual(len(first["messages"]), 2)

    def test_drops_task_and_diagnostic(self):
        exported = export_sessions(make_sessions())
        self.assertNotIn("task", exported[0])
        self.assertNotIn("diagnostic", exported[0])

    def test_messages_keep_only_safe_fields(self):
        sessions = [{
            "id": "x", "title": "t", "created_at": 1.0, "updated_at": 2.0,
            "archived": False,
            "messages": [{"role": "user", "content": "hi", "timestamp": 1.0,
                          "extra_field": "dropped"}],
        }]
        exported = export_sessions(sessions)
        self.assertEqual(exported[0]["messages"],
                         [{"role": "user", "content": "hi", "timestamp": 1.0}])

    def test_max_sessions_limit(self):
        sessions = [{"id": str(i), "title": "t", "created_at": 1.0, "updated_at": 2.0,
                     "archived": False, "messages": []} for i in range(10)]
        exported = export_sessions(sessions, max_sessions=3)
        self.assertEqual(len(exported), 3)

    def test_non_dict_sessions_skipped(self):
        exported = export_sessions([{"id": "ok", "title": "t", "created_at": 1.0,
                                     "updated_at": 2.0, "archived": False, "messages": []},
                                    "not a dict", None])
        self.assertEqual(len(exported), 1)

    def test_missing_messages_defaults_to_empty(self):
        exported = export_sessions([{"id": "x", "title": "t"}])
        self.assertEqual(exported[0]["messages"], [])


class TestBuildAndSerialize(unittest.TestCase):
    def test_build_bundle_structure(self):
        bundle = build_bundle(make_config(), make_sessions(), source="laptop")
        self.assertEqual(bundle["format"], BUNDLE_FORMAT)
        self.assertEqual(bundle["version"], BUNDLE_VERSION)
        self.assertEqual(bundle["source"], "laptop")
        self.assertIn("settings", bundle)
        self.assertIn("sessions", bundle)
        self.assertGreater(bundle["created_at"], 0)

    def test_serialize_round_trip(self):
        bundle = build_bundle(make_config(), make_sessions())
        text = serialize_bundle(bundle)
        parsed = parse_bundle(text)
        self.assertEqual(parsed.setting_count, len(bundle["settings"]))
        self.assertEqual(parsed.session_count, 2)

    def test_serialize_rejects_oversized(self):
        bundle = build_bundle({}, [])
        bundle["settings"] = {"big": "x" * (DEFAULT_MAX_BUNDLE_BYTES + 1)}
        with self.assertRaises(SyncBundleError):
            serialize_bundle(bundle)


class TestParseBundle(unittest.TestCase):
    def test_valid_bundle(self):
        text = serialize_bundle(build_bundle(make_config(), make_sessions()))
        parsed = parse_bundle(text)
        self.assertIsInstance(parsed, SyncBundle)
        self.assertGreater(parsed.setting_count, 0)

    def test_rejects_non_string(self):
        with self.assertRaises(SyncBundleError):
            parse_bundle({"not": "a string"})

    def test_rejects_oversized(self):
        with self.assertRaises(SyncBundleError):
            parse_bundle("x" * (DEFAULT_MAX_BUNDLE_BYTES + 1))

    def test_rejects_invalid_json(self):
        with self.assertRaises(SyncBundleError):
            parse_bundle("not json{")

    def test_rejects_non_object(self):
        with self.assertRaises(SyncBundleError):
            parse_bundle("[1, 2, 3]")

    def test_rejects_wrong_format(self):
        payload = {"format": "something-else", "version": 1, "settings": {}, "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_wrong_version(self):
        payload = {"format": BUNDLE_FORMAT, "version": 99, "settings": {}, "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_non_int_version(self):
        payload = {"format": BUNDLE_FORMAT, "version": "1", "settings": {}, "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_invalid_settings(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1, "settings": [], "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_invalid_sessions(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1, "settings": {}, "sessions": {}}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_too_many_sessions(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1, "settings": {},
                   "sessions": [{"id": str(i)} for i in range(101)]}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_forbidden_settings_key(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1,
                   "settings": {"api.providers.openrouter.api_key": "sk-leaked"},
                   "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_rejects_encryption_flag(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1,
                   "settings": {"app.encryption_enabled": True},
                   "sessions": []}
        with self.assertRaises(SyncBundleError):
            parse_bundle(json.dumps(payload))

    def test_bad_created_at_defaults_to_zero(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1, "settings": {},
                   "sessions": [], "created_at": "not a number"}
        parsed = parse_bundle(json.dumps(payload))
        self.assertEqual(parsed.created_at, 0.0)

    def test_bad_source_defaults_to_empty(self):
        payload = {"format": BUNDLE_FORMAT, "version": 1, "settings": {},
                   "sessions": [], "source": 123}
        parsed = parse_bundle(json.dumps(payload))
        self.assertEqual(parsed.source, "")


class TestPlanImport(unittest.TestCase):
    def test_plans_changed_settings(self):
        bundle = parse_bundle(serialize_bundle(build_bundle(make_config(), [])))
        other_config = make_config()
        other_config["app"]["theme"] = "light"
        plan = plan_import(bundle, other_config)
        self.assertIn("app.theme", plan.settings_to_apply)
        self.assertEqual(plan.settings_to_apply["app.theme"], "dark")

    def test_skips_unchanged_settings(self):
        bundle = parse_bundle(serialize_bundle(build_bundle(make_config(), [])))
        plan = plan_import(bundle, make_config())
        self.assertEqual(plan.settings_to_apply, {})

    def test_plans_new_sessions_as_additions(self):
        bundle = parse_bundle(serialize_bundle(build_bundle({}, make_sessions())))
        plan = plan_import(bundle, {}, existing_session_ids=[])
        self.assertEqual(len(plan.sessions_to_add), 2)
        self.assertEqual(plan.sessions_to_update, [])

    def test_plans_existing_sessions_as_updates(self):
        bundle = parse_bundle(serialize_bundle(build_bundle({}, make_sessions())))
        plan = plan_import(bundle, {}, existing_session_ids=["abc123"])
        self.assertEqual(plan.sessions_to_update, ["abc123"])
        self.assertEqual(len(plan.sessions_to_add), 1)

    def test_skips_invalid_sessions(self):
        bundle = SyncBundle(settings={}, sessions=[{"no_id": True}, "bad", {"id": "ok"}])
        plan = plan_import(bundle, {}, existing_session_ids=[])
        self.assertEqual(len(plan.sessions_to_add), 1)
        self.assertEqual(plan.sessions_skipped, ["<invalid>", "<invalid>"])

    def test_total_changes(self):
        bundle = parse_bundle(serialize_bundle(build_bundle(make_config(), make_sessions())))
        plan = plan_import(bundle, make_config(), existing_session_ids=[])
        self.assertEqual(plan.total_changes, 0 + 2)

    def test_import_plan_is_frozen(self):
        plan = ImportPlan()
        with self.assertRaises(AttributeError):
            plan.settings_to_apply = {}


class TestApplySettings(unittest.TestCase):
    def test_applies_whitelisted_settings(self):
        config = make_config()
        bundle = SyncBundle(settings={"app.theme": "light", "pomodoro.enabled": False})
        applied = apply_settings(bundle, config)
        self.assertEqual(config["app"]["theme"], "light")
        self.assertFalse(config["pomodoro"]["enabled"])
        self.assertEqual(applied, {"app.theme": "light", "pomodoro.enabled": False})

    def test_creates_missing_nested_dicts(self):
        config = {"app": {}}
        bundle = SyncBundle(settings={"app.theme": "light"})
        apply_settings(bundle, config)
        self.assertEqual(config["app"]["theme"], "light")

    def test_never_overwrites_credentials(self):
        config = make_config()
        original_key = config["api"]["providers"]["openrouter"]["api_key"]
        bundle = SyncBundle(settings={"app.theme": "light"})
        apply_settings(bundle, config)
        self.assertEqual(config["api"]["providers"]["openrouter"]["api_key"], original_key)

    def test_skips_forbidden_keys(self):
        config = make_config()
        bundle = SyncBundle(settings={"app.encryption_enabled": True})
        applied = apply_settings(bundle, config)
        self.assertEqual(applied, {})
        self.assertFalse(config["app"]["encryption_enabled"])

    def test_provider_keys_never_applied(self):
        config = make_config()
        bundle = SyncBundle(settings={"api.providers.openrouter.api_key": "injected"})
        applied = apply_settings(bundle, config)
        self.assertEqual(applied, {})
        self.assertEqual(config["api"]["providers"]["openrouter"]["api_key"],
                         "sk-secret-do-not-sync")


class TestMergeSessions(unittest.TestCase):
    def test_adds_new_sessions(self):
        existing = [{"id": "a", "title": "A"}]
        bundle = SyncBundle(settings={}, sessions=[{"id": "b", "title": "B"}])
        merged = merge_sessions(bundle, existing)
        ids = [s["id"] for s in merged]
        self.assertEqual(ids, ["a", "b"])

    def test_updates_existing_sessions(self):
        existing = [{"id": "a", "title": "Old title"}]
        bundle = SyncBundle(settings={}, sessions=[{"id": "a", "title": "New title"}])
        merged = merge_sessions(bundle, existing)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "New title")

    def test_preserves_existing_order(self):
        existing = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        bundle = SyncBundle(settings={}, sessions=[{"id": "d"}, {"id": "a", "updated": True}])
        merged = merge_sessions(bundle, existing)
        ids = [s["id"] for s in merged]
        self.assertEqual(ids, ["c", "a", "b", "d"])

    def test_skips_invalid_bundle_sessions(self):
        existing = [{"id": "a"}]
        bundle = SyncBundle(settings={}, sessions=[{"no_id": 1}, "bad", {"id": "b"}])
        merged = merge_sessions(bundle, existing)
        ids = [s["id"] for s in merged]
        self.assertEqual(ids, ["a", "b"])

    def test_does_not_mutate_inputs(self):
        existing = [{"id": "a", "title": "A"}]
        bundle = SyncBundle(settings={}, sessions=[{"id": "b", "title": "B"}])
        merge_sessions(bundle, existing)
        self.assertEqual(len(existing), 1)


class TestWhitelistContract(unittest.TestCase):
    def test_all_whitelist_keys_are_not_forbidden(self):
        for key, _ in SYNCABLE_SETTINGS:
            self.assertFalse(
                any(key.startswith(p) for p in ("api.providers.openrouter.api_key",)),
                f"whitelist key looks like a credential: {key}",
            )

    def test_no_whitelist_key_contains_api_key(self):
        for key, _ in SYNCABLE_SETTINGS:
            self.assertNotIn("api_key", key)

    def test_forbidden_keys_constant(self):
        self.assertIn("app.encryption_enabled", FORBIDDEN_SETTINGS_KEYS)

    def test_local_llm_whitelist_excludes_api_key(self):
        keys = [k for k, _ in SYNCABLE_SETTINGS if k.startswith("api.providers.local_llm.")]
        self.assertNotIn("api.providers.local_llm.api_key", keys)
        self.assertIn("api.providers.local_llm.model", keys)
        self.assertIn("api.providers.local_llm.base_url", keys)


class TestEndToEnd(unittest.TestCase):
    def test_export_import_round_trip_preserves_safe_data(self):
        config = make_config()
        sessions = make_sessions()
        bundle_text = serialize_bundle(build_bundle(config, sessions, source="machine-a"))
        parsed = parse_bundle(bundle_text)

        # Import into a fresh machine's config.
        new_config = {"app": {"theme": "light"}, "api": {"providers": {}}}
        applied = apply_settings(parsed, new_config)
        self.assertEqual(new_config["app"]["theme"], "dark")
        self.assertIn("app.language", applied)

        # Credentials on the destination are untouched.
        self.assertNotIn("api_key", json.dumps(new_config))

        # Sessions merge.
        merged = merge_sessions(parsed, [])
        self.assertEqual(len(merged), 2)
        self.assertNotIn("task", merged[0])

    def test_bundle_never_contains_secrets(self):
        config = make_config()
        text = serialize_bundle(build_bundle(config, make_sessions()))
        self.assertNotIn("sk-secret-do-not-sync", text)
        self.assertNotIn("api_key", text)


if __name__ == "__main__":
    unittest.main()
