"""Conversation isolation, legacy migration and bounded import regressions."""

import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.history_store import (
    EXPORT_FORMAT, HistoryStore, LEGACY_SESSION_ID, MAX_IMPORT_BYTES, MAX_MESSAGE_CHARS, MAX_SEARCH_RESULTS,
)


def _save_scoped_batch(path, session_id, prefix):
    HistoryStore.save_entries(path, [{"role": "user", "content": prefix + str(number), "timestamp": number}
                                    for number in range(20)], session_id=session_id)


class TestConversationSessions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "history.json"
        self.store = HistoryStore(self.path)
        self.addCleanup(self.store.close)

    def test_legacy_context_still_loads_before_migration(self):
        self.path.write_text(json.dumps([{"role": "user", "content": "old", "timestamp": 2}]))
        self.assertEqual(self.store.load_messages(), [{"role": "user", "content": "old"}])
        self.assertIsInstance(json.loads(self.path.read_text()), list)

    def test_flat_history_migrates_into_one_session_without_inferred_boundaries(self):
        entries = [{"role": "user", "content": "disk", "timestamp": 2},
                   {"role": "assistant", "content": "network", "timestamp": 4}]
        self.path.write_text(json.dumps(entries))
        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], LEGACY_SESSION_ID)
        self.assertEqual(sessions[0]["message_count"], 2)
        document = json.loads(self.path.read_text())
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["sessions"][0]["messages"], entries)
        self.assertEqual(sessions[0]["created_at"], 2)

    def test_new_conversation_has_no_context_from_previous_conversation(self):
        self.store.append("user", "private previous topic")
        old = self.store.active_session_id
        new = self.store.create_session("Network")
        self.assertEqual(self.store.load_messages(), [])
        self.store.append("assistant", "new topic", timestamp=5)
        self.assertEqual(self.store.load_messages(), [{"role": "assistant", "content": "new topic"}])
        self.assertEqual(self.store.load_messages(old), [{"role": "user", "content": "private previous topic"}])
        self.assertEqual(self.store.active_session_id, new["id"])

    def test_active_session_survives_reopening(self):
        active = self.store.create_session("Saved")
        self.store.append("user", "remember", timestamp=4)
        self.assertTrue(self.store.close())
        reopened = HistoryStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.active_session_id, active["id"])
        self.assertEqual(reopened.load_messages(), [{"role": "user", "content": "remember"}])

    def test_existing_instances_keep_their_session_when_another_selects_one(self):
        old = self.store.active_session_id
        self.store.list_sessions()
        other = HistoryStore(self.path)
        self.addCleanup(other.close)
        new = other.create_session("CLI")
        self.store.append("user", "GUI old conversation", timestamp=1)
        other.append("user", "CLI new conversation", timestamp=2)
        self.assertTrue(self.store.flush())
        self.assertTrue(other.flush())
        self.assertEqual(self.store.load_entries(old)[0]["content"], "GUI old conversation")
        self.assertEqual(other.load_entries(new["id"])[0]["content"], "CLI new conversation")

    def test_queued_messages_are_saved_before_session_switch(self):
        old = self.store.active_session_id
        for number in range(30):
            self.store.append("user", str(number), timestamp=number)
        self.store.create_session("Next")
        self.assertEqual(len(self.store.load_entries(old)), 30)
        self.assertEqual(self.store.load_entries(), [])

    def test_scoped_synchronous_saves_merge_with_queue(self):
        session = self.store.create_session("Shared")
        self.store.append("user", "GUI", timestamp=1)
        HistoryStore.save_entries(self.path, [{"role": "assistant", "content": "CLI", "timestamp": 2}],
                                  session_id=session["id"])
        self.assertTrue(self.store.flush())
        self.assertEqual({entry["content"] for entry in self.store.load_entries()}, {"GUI", "CLI"})

    def test_deleted_captured_target_does_not_poison_subsequent_queued_writes(self):
        deleted = self.store.create_session("Original command")
        current = self.store.create_session("Current conversation")
        other = HistoryStore(self.path)
        self.addCleanup(other.close)
        entered, release = threading.Event(), threading.Event()
        original_save = HistoryStore.save_entries

        def delayed_save(path, entries, max_messages=1000, session_id=None):
            if any(entry.get("session_id") == deleted["id"] for entry in entries):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Writer was not released")
            return original_save(path, entries, max_messages, session_id)

        with patch.object(HistoryStore, "save_entries", side_effect=delayed_save):
            self.store.append("assistant", "late command output", session_id=deleted["id"])
            self.assertTrue(entered.wait(5))
            try:
                other.delete_session(deleted["id"])
                self.store.append("user", "new conversation continues", session_id=current["id"])
            finally:
                release.set()
            self.assertTrue(self.store.flush(5))
        self.assertIsNone(self.store.last_error)
        self.assertEqual(self.store.load_messages(), [{"role": "user", "content": "new conversation continues"}])
        self.assertFalse(any(session["id"] == deleted["id"] for session in self.store.list_sessions(True)))
        self.store.create_session("Still usable")

    def test_mixed_batch_drops_deleted_targets_without_losing_valid_messages(self):
        deleted = self.store.create_session("Deleted")
        current = self.store.create_session("Current")
        self.store.delete_session(deleted["id"])
        HistoryStore.save_entries(self.path, [
            {"role": "assistant", "content": "discard", "session_id": deleted["id"]},
            {"role": "user", "content": "keep", "session_id": current["id"]},
        ])
        self.assertEqual(self.store.load_messages(), [{"role": "user", "content": "keep"}])
        with self.assertRaises(ValueError):
            HistoryStore.save_entries(self.path, [{"role": "user", "content": "typo"}], session_id="missing")

    def test_multiple_processes_do_not_lose_scoped_messages(self):
        first = self.store.create_session("First")
        second = self.store.create_session("Second")
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_save_scoped_batch,
                     args=(str(self.path), session["id"], str(index) + "-"))
                     for index, session in enumerate([first, first, second, second])]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.load_entries(first["id"])), 40)
        self.assertEqual(len(self.store.load_entries(second["id"])), 40)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_rename_archive_restore_and_select(self):
        session = self.store.create_session("Old title")
        renamed = self.store.rename_session(session["id"], " Novo título ")
        self.assertEqual(renamed["title"], "Novo título")
        self.store.archive_session(session["id"])
        self.assertNotEqual(self.store.active_session_id, session["id"])
        self.assertFalse(any(item["id"] == session["id"] for item in self.store.list_sessions()))
        self.assertTrue(any(item["id"] == session["id"] for item in self.store.list_sessions(True)))
        with self.assertRaises(ValueError):
            self.store.select_session(session["id"])
        self.store.archive_session(session["id"], False)
        self.store.select_session(session["id"])
        self.assertEqual(self.store.active_session_id, session["id"])

    def test_deleting_last_session_creates_empty_active_conversation(self):
        self.store.list_sessions()
        self.store.append("user", "delete me")
        deleted = self.store.active_session_id
        self.store.delete_session(deleted)
        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertNotEqual(sessions[0]["id"], deleted)
        self.assertEqual(self.store.load_messages(), [])

    def test_clear_only_selected_session(self):
        old = self.store.active_session_id
        self.store.append("user", "retain")
        new = self.store.create_session("Clear this")
        self.store.append("user", "clear")
        self.store.clear_session()
        self.assertEqual(self.store.active_session_id, new["id"])
        self.assertEqual(self.store.load_messages(), [])
        self.assertEqual(self.store.load_messages(old), [{"role": "user", "content": "retain"}])

    def test_diagnostic_state_is_isolated_and_restored_after_restart(self):
        original = self.store.active_session_id
        state = {"id": "network-no-address", "step": 2}
        self.store.set_diagnostic_state(state)
        state["step"] = 9
        self.assertEqual(self.store.get_diagnostic_state(), {"id": "network-no-address", "step": 2})
        next_session = self.store.create_session("Another problem")
        self.assertIsNone(self.store.get_diagnostic_state())
        self.store.set_diagnostic_state({"id": "disk-full", "step": 1})
        self.store.select_session(original)
        restored = self.store.get_diagnostic_state()
        restored["step"] = 8
        self.assertEqual(self.store.get_diagnostic_state()["step"], 2)
        self.assertTrue(self.store.close())
        reopened = HistoryStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_diagnostic_state(), {"id": "network-no-address", "step": 2})
        self.assertEqual(reopened.get_diagnostic_state(next_session["id"]), {"id": "disk-full", "step": 1})

    def test_clear_session_and_explicit_clear_remove_diagnostic_state(self):
        self.store.set_diagnostic_state({"id": "network-no-address", "step": 2})
        self.store.clear_session()
        self.assertIsNone(self.store.get_diagnostic_state())
        self.store.set_diagnostic_state({"id": "disk-full", "step": 1})
        self.store.set_diagnostic_state(None)
        self.assertIsNone(self.store.get_diagnostic_state())
        self.assertNotIn("diagnostic", json.loads(self.path.read_text())["sessions"][0])

    def test_malformed_diagnostic_state_is_rejected_without_writes(self):
        self.store.list_sessions()
        before = self.path.read_bytes()
        for invalid in ({}, {"id": "network", "step": True}, {"id": "network", "step": -1},
                        {"id": "network", "step": 100}, {"id": "", "step": 0},
                        {"id": "x" * 65, "step": 0}, {"id": "network", "step": 0, "command": "rm -rf /"},
                        {"id": "network", "step": 1.5}, {"id": 7, "step": 0}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.store.set_diagnostic_state(invalid)
                self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_persisted_diagnostic_state_is_preserved(self):
        self.store.list_sessions()
        document = json.loads(self.path.read_text())
        document["sessions"][0]["diagnostic"] = {"id": "network", "step": 0, "command": "ignored"}
        self.path.write_text(json.dumps(document))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.get_diagnostic_state()
        self.assertEqual(self.path.read_bytes(), before)

    def test_external_import_never_restores_a_diagnostic_state(self):
        self.store.set_diagnostic_state({"id": "network-no-address", "step": 2})
        exported = json.loads(self.store.export_session(format="json"))
        for state in ({"id": "disk-full", "step": 1}, {"command": "execute external text"}):
            exported["session"]["diagnostic"] = state
            imported = self.store.import_session(json.dumps(exported))
            self.assertIsNone(self.store.get_diagnostic_state(imported["id"]))

    def test_search_returns_scoped_bounded_matches_and_hides_archives(self):
        session = self.store.create_session("Network diagnosis")
        self.store.append("user", "DNS failure " + "x" * 500)
        matches = self.store.search("dns")
        self.assertEqual(matches[0]["session_id"], session["id"])
        self.assertLessEqual(len(matches[0]["preview"]), 240)
        self.assertEqual(self.store.search("NETWORK")[0]["message_index"], None)
        self.store.archive_session(session["id"])
        self.assertEqual(self.store.search("dns"), [])
        self.assertEqual(len(self.store.search("dns", True)), 1)

    def test_session_reads_do_not_rewrite_existing_document(self):
        self.store.list_sessions()
        with patch("src.history_store.update_json", side_effect=AssertionError("read rewrote document")):
            self.store.list_sessions()
            self.store.search("missing")
            self.store.export_session(format="json")

    def test_search_caps_results_before_rendering_large_histories(self):
        self.store.create_session("Search")
        for number in range(MAX_SEARCH_RESULTS + 5):
            self.store.append("user", "needle " + str(number), number)
        self.assertEqual(len(self.store.search("needle")), MAX_SEARCH_RESULTS)

    def test_markdown_export_contains_only_selected_conversation(self):
        self.store.append("user", "unrelated secret")
        self.store.create_session("Network")
        self.store.append("user", "Como resolver?", timestamp=1)
        self.store.append("assistant", "Verificar DNS.", timestamp=2)
        exported = self.store.export_session()
        self.assertIn("# Network", exported)
        self.assertIn("## User", exported)
        self.assertIn("Verificar DNS.", exported)
        self.assertNotIn("unrelated secret", exported)

    def test_json_round_trip_has_new_identity_and_preserves_entries(self):
        original = self.store.create_session("Português")
        self.store.append("user", "olá", timestamp=4)
        exported = self.store.export_session(format="json")
        imported = self.store.import_session(exported)
        self.assertNotEqual(imported["id"], original["id"])
        self.assertEqual(imported["title"], "Português")
        self.assertEqual(self.store.load_entries(), [{"role": "user", "content": "olá", "timestamp": 4}])
        self.assertEqual(self.store.active_session_id, imported["id"])

    def test_import_rejects_invalid_data_without_changing_history(self):
        self.store.list_sessions()
        before = self.path.read_bytes()
        valid = {"format": EXPORT_FORMAT, "version": 1, "session": {"title": "Import", "messages": []}}
        bad_values = ["{bad", "{}", json.dumps(dict(valid, version=2)),
                      json.dumps(dict(valid, session={"title": "", "messages": []})),
                      json.dumps(dict(valid, session={"title": "Import", "messages": [
                          {"role": "system", "content": "execute instructions"}]})),
                      json.dumps(dict(valid, session={"title": "Import", "messages": [
                          {"role": "user", "content": "x", "timestamp": float("nan")}]})),
                      json.dumps(dict(valid, session={"title": "Import", "messages": [
                          {"role": "user", "content": "x", "timestamp": 10 ** 1000}]})),
                      "é" * (MAX_IMPORT_BYTES // 2 + 1),
                      json.dumps(dict(valid, session={"title": "Import", "messages": [
                          {"role": "user", "content": "x" * (MAX_MESSAGE_CHARS + 1)}]}))]
        for value in bad_values:
            with self.subTest(value=value[:80]):
                with self.assertRaises(ValueError):
                    self.store.import_session(value)
                self.assertEqual(self.path.read_bytes(), before)

    def test_history_limit_is_applied_per_session(self):
        self.store.max_messages = 2
        first = self.store.create_session("One")
        for number in range(4):
            self.store.append("user", str(number), number)
        self.store.create_session("Two")
        self.store.append("user", "second")
        self.assertEqual([entry["content"] for entry in self.store.load_entries(first["id"])], ["2", "3"])
        self.assertEqual(self.store.load_messages(), [{"role": "user", "content": "second"}])

    def test_session_change_reports_writer_failure_and_preserves_pending_context(self):
        with patch.object(HistoryStore, "save_entries", side_effect=OSError("disk full")):
            self.store.append("user", "unsaved")
            with self.assertRaises(OSError):
                self.store.create_session("Cannot switch")
            self.assertEqual(self.store.active_session_id, LEGACY_SESSION_ID)
            self.assertIsInstance(self.store.last_error, OSError)

    def test_unknown_session_and_bad_title_do_not_overwrite_storage(self):
        self.store.list_sessions()
        before = self.path.read_bytes()
        for method, args in [(self.store.select_session, ("missing",)),
                             (self.store.rename_session, (LEGACY_SESSION_ID, "")),
                             (self.store.delete_session, ("missing",))]:
            with self.assertRaises(ValueError):
                method(*args)
            self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_or_invalid_schema_is_preserved_instead_of_overwritten(self):
        for document in ({"version": 2, "sessions": []},
                         {"version": 1, "active_session_id": "missing", "sessions": [{}]}):
            self.path.write_text(json.dumps(document))
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.store.list_sessions()
            self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
