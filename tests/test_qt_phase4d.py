"""Phase 4d: Qt settings and history dialogs on the shared backends."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.qt_dialogs import history_rows, provider_rows, save_api_key


def config(stored=True, override=None, broken=False):
    manager = Mock()
    if broken:
        manager.get_api_key = Mock(side_effect=RuntimeError("boom"))
        manager.api_key_env_var = Mock(side_effect=RuntimeError("boom"))
        manager.get_api_key_env_override = Mock(side_effect=RuntimeError("boom"))
        return manager
    manager.get_api_key = Mock(return_value="key" if stored else "")
    manager.api_key_env_var = Mock(return_value="PROVIDER_API_KEY")
    manager.get_api_key_env_override = Mock(return_value=override)
    return manager


class TestProviderRows(unittest.TestCase):
    def test_rows_hide_the_key_but_report_status(self):
        rows = provider_rows(config(stored=True))
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn("key", row.values())
            self.assertEqual(row["status"], "configured")
            self.assertEqual(row["env_var"], "PROVIDER_API_KEY")

    def test_missing_key_reports_missing(self):
        rows = provider_rows(config(stored=False))
        self.assertTrue(all(row["status"] == "missing" for row in rows))

    def test_env_override_is_reported(self):
        rows = provider_rows(config(stored=True, override="PROVIDER_API_KEY"))
        self.assertTrue(all(row["overridden"] for row in rows))

    def test_broken_config_degrades_to_missing(self):
        rows = provider_rows(config(broken=True))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "missing" for row in rows))
        self.assertEqual(rows[0]["overridden"], False)


class TestSaveApiKey(unittest.TestCase):
    def test_valid_key_is_stored(self):
        manager = config()
        ok, message = save_api_key(manager, "openrouter", "  sk-123  ")
        self.assertTrue(ok)
        self.assertEqual(message, "stored")
        manager.set_api_key.assert_called_once_with("openrouter", "sk-123")

    def test_empty_key_is_rejected_without_storing(self):
        manager = config()
        for value in ("", "   ", None):
            with self.subTest(value=value):
                ok, message = save_api_key(manager, "openrouter", value)
                self.assertFalse(ok)
                self.assertEqual(message, "empty-key")
        manager.set_api_key.assert_not_called()

    def test_invalid_provider_is_rejected(self):
        ok, message = save_api_key(config(), "", "sk-1")
        self.assertFalse(ok)
        self.assertEqual(message, "invalid-provider")

    def test_store_failure_is_reported_not_raised(self):
        manager = config()
        manager.set_api_key = Mock(side_effect=RuntimeError("disk?"))
        ok, message = save_api_key(manager, "openrouter", "sk-1")
        self.assertFalse(ok)
        self.assertEqual(message, "store-failed")


class TestHistoryRows(unittest.TestCase):
    def setUp(self):
        from src.history_store import HistoryStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = HistoryStore(path=Path(self.directory.name) / "history.json")
        self.addCleanup(self.store.close)
        self.store.append("user", "primeira")
        self.new_id = self.store.create_session("Sessão dois")["id"]

    def test_rows_list_sessions_with_active_marker(self):
        rows = history_rows(self.store)
        self.assertEqual(len(rows), 2)
        active = [row for row in rows if row["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], self.new_id)
        self.assertEqual(active[0]["title"], "Sessão dois")

    def test_rows_exclude_archived_by_default(self):
        rows = history_rows(self.store)
        self.assertTrue(all(not row["archived"] for row in rows))


class TestQtDialogsContract(unittest.TestCase):
    def test_module_imports_without_pyside6(self):
        import src.qt_dialogs as qt_dialogs
        self.assertIn(qt_dialogs.QT_AVAILABLE, (True, False))

    def test_dialogs_raise_without_pyside6(self):
        import src.qt_dialogs as qt_dialogs
        if qt_dialogs.QT_AVAILABLE:
            self.skipTest("PySide6 installed in this environment")
        with self.assertRaises(RuntimeError):
            qt_dialogs.QtSettingsDialog(SimpleNamespace())
        with self.assertRaises(RuntimeError):
            qt_dialogs.QtHistoryDialog(SimpleNamespace())


class TestDialogsWhenQtAvailable(unittest.TestCase):
    def setUp(self):
        import src.qt_dialogs as qt_dialogs
        if not qt_dialogs.QT_AVAILABLE:
            self.skipTest("PySide6 unavailable in this environment")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.qt_dialogs = qt_dialogs
        self.config = Mock()
        self.config.get_api_key = Mock(return_value="")
        self.config.api_key_env_var = Mock(return_value="X_KEY")
        self.config.get_api_key_env_override = Mock(return_value=None)

    def test_settings_dialog_lists_providers(self):
        dialog = self.qt_dialogs.QtSettingsDialog(self.config)
        self.assertEqual(dialog.provider_combo.count(), 2)
        self.assertEqual(dialog.selected_provider(), "openrouter")

    def test_settings_save_persists_through_contract(self):
        self.config.set_api_key = Mock(return_value=None)
        dialog = self.qt_dialogs.QtSettingsDialog(self.config)
        dialog.key_edit.setText("sk-test")
        dialog._save()
        self.config.set_api_key.assert_called_once_with("openrouter", "sk-test")

    def test_history_dialog_lists_sessions(self):
        from src.history_store import HistoryStore
        import tempfile as tf
        from pathlib import Path as P
        directory = tf.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = HistoryStore(path=P(directory.name) / "h.json")
        self.addCleanup(store.close)
        store.append("user", "msg")
        dialog = self.qt_dialogs.QtHistoryDialog(store)
        self.assertEqual(dialog.list_widget.count(), 1)


if __name__ == "__main__":
    unittest.main()
