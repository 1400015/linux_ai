"""Observed action choices remain bounded, private to a conversation and local."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.display_actions import DisplayMode
from src.history_store import HistoryStore
from src.package_actions import PackageCandidate
from src.task_state import MAX_TASK_OPTIONS, validate_task_state


def package_state():
    return {"version": 1, "kind": "packages", "operation": "install", "created_at": 1000.0,
            "query": "editor", "options": [PackageCandidate("nano", "8.3-1", "https://repo.example", "Editor").to_dict()]}


def display_state():
    return {"version": 1, "kind": "display", "operation": "inspect", "created_at": 1000.0,
            "query": "monitors", "options": [DisplayMode("DP-1", 1920, 1080, 60.0, "1920x1080").to_dict()]}


class TestTaskStateSchema(unittest.TestCase):
    def test_valid_states_are_independent_copies_of_observed_options(self):
        for state in (package_state(), display_state()):
            clean = validate_task_state(state)
            self.assertEqual(clean, state)
            self.assertIsNot(clean, state)
            self.assertIsNot(clean["options"][0], state["options"][0])
            clean["query"] = "different"
            self.assertNotEqual(clean["query"], state["query"])

    def test_command_secrets_unknown_keys_and_malformed_state_are_rejected(self):
        invalid = []
        for key, value in (("version", True), ("kind", "shell"), ("operation", "remove"),
                           ("created_at", float("nan")), ("created_at", float("inf")),
                           ("created_at", True), ("created_at", -1), ("query", "bad\nquery"),
                           ("options", []), ("options", {})):
            state = package_state()
            state[key] = value
            invalid.append(state)
        state = package_state()
        state["argv"] = ["sh", "-c", "id"]
        invalid.append(state)
        for key in ("argv", "password"):
            state = package_state()
            state["options"][0][key] = "untrusted"
            invalid.append(state)
        state = package_state()
        state["options"] *= MAX_TASK_OPTIONS + 1
        invalid.append(state)
        for state in invalid:
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_task_state(state)

    def test_package_metadata_must_also_be_accepted_by_backend(self):
        for key, value in (("name", "nano-"), ("name", "nano;id"), ("name", "nano:amd64"),
                           ("version", "1.0;id"), ("version", ""), ("source", ""),
                           ("source", "repo\nrun command"), ("summary", "x" * 513)):
            state = package_state()
            state["options"][0][key] = value
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                validate_task_state(state)

    def test_display_metadata_must_also_be_accepted_by_backend(self):
        for key, value in (("output", "DP-1;id"), ("identifier", "1920x1080;id"),
                           ("width", True), ("width", 0), ("height", 40000),
                           ("refresh", float("nan")), ("refresh", 0.01),
                           ("scale", 0.1), ("current", 1)):
            state = display_state()
            state["options"][0][key] = value
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                validate_task_state(state)


class TestTaskStateHistory(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history.json"
        self.store = HistoryStore(self.path)
        self.addCleanup(self.store.close)
        self.store.list_sessions()
        self.session = self.store.active_session_id

    def test_task_is_session_scoped_and_available_to_reopened_store(self):
        original = package_state()
        self.store.set_task_state(original, self.session)
        other = self.store.create_session("Monitor")
        self.store.set_task_state(display_state(), other["id"])
        self.assertEqual(self.store.get_task_state(self.session), original)
        self.assertEqual(self.store.get_task_state()["kind"], "display")
        self.assertTrue(self.store.close())
        reopened = HistoryStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.active_session_id, other["id"])
        self.assertEqual(reopened.get_task_state(self.session), original)
        self.assertEqual(reopened.get_task_state()["kind"], "display")

    def test_clear_session_removes_choices_without_erasing_other_session(self):
        self.store.append("user", "Install editor", session_id=self.session)
        self.store.set_task_state(package_state(), self.session)
        other = self.store.create_session("Monitor")
        self.store.set_task_state(display_state(), other["id"])
        self.store.clear_session(self.session)
        self.assertEqual(self.store.load_messages(self.session), [])
        self.assertIsNone(self.store.get_task_state(self.session))
        self.assertEqual(self.store.get_task_state(other["id"])["kind"], "display")

    def test_task_metadata_is_absent_from_provider_context_and_exports(self):
        self.store.append("user", "Find editor", timestamp=2, session_id=self.session)
        self.store.set_task_state(package_state(), self.session)
        self.assertEqual(self.store.load_messages(self.session), [{"role": "user", "content": "Find editor"}])
        exported = json.loads(self.store.export_session(self.session, format="json"))
        self.assertNotIn("task", exported["session"])
        self.assertNotIn("https://repo.example", self.store.export_session(self.session, format="markdown"))
        self.assertEqual(self.store.get_task_state(self.session), package_state())

    def test_import_never_revives_pending_actions_even_if_payload_supplies_task(self):
        self.store.append("assistant", "Found programs", timestamp=3, session_id=self.session)
        exported = json.loads(self.store.export_session(self.session, format="json"))
        exported["session"]["task"] = package_state()
        imported = self.store.import_session(json.dumps(exported))
        self.assertIsNone(self.store.get_task_state(imported["id"]))
        self.assertEqual(self.store.load_messages(), [{"role": "assistant", "content": "Found programs"}])
        exported["session"]["task"] = {"kind": "shell", "argv": ["rm", "-rf", "/"]}
        imported = self.store.import_session(json.dumps(exported))
        self.assertIsNone(self.store.get_task_state(imported["id"]))

    def test_invalid_stored_task_is_discarded_without_losing_user_messages(self):
        self.store.append("user", "Retain my conversation", timestamp=5, session_id=self.session)
        self.assertTrue(self.store.flush())
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["sessions"][0]["task"] = {"kind": "shell", "argv": ["id"]}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertIsNone(self.store.get_task_state(self.session))
        self.assertEqual(self.store.load_messages(self.session), [{"role": "user", "content": "Retain my conversation"}])
        self.store.rename_session(self.session, "Still usable")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("task", document["sessions"][0])

    def test_invalid_set_does_not_replace_existing_valid_choices(self):
        self.store.set_task_state(package_state(), self.session)
        invalid = copy.deepcopy(package_state())
        invalid["options"][0]["argv"] = ["id"]
        with self.assertRaises(ValueError):
            self.store.set_task_state(invalid, self.session)
        self.assertEqual(self.store.get_task_state(self.session), package_state())

    def test_returned_and_supplied_objects_cannot_mutate_saved_choices(self):
        supplied = package_state()
        self.store.set_task_state(supplied, self.session)
        supplied["options"][0]["name"] = "other"
        returned = self.store.get_task_state(self.session)
        returned["options"][0]["name"] = "third"
        self.assertEqual(self.store.get_task_state(self.session), package_state())

    def test_explicit_clear_and_deletion_remove_only_target_state(self):
        self.store.set_task_state(package_state(), self.session)
        other = self.store.create_session("Other")
        self.store.set_task_state(display_state(), other["id"])
        self.store.set_task_state(None, self.session)
        self.assertIsNone(self.store.get_task_state(self.session))
        self.store.delete_session(self.session)
        with self.assertRaises(ValueError):
            self.store.get_task_state(self.session)
        self.assertEqual(self.store.get_task_state(other["id"])["kind"], "display")


if __name__ == "__main__":
    unittest.main()
