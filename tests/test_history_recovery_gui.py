"""GTK remains responsive while history recovery preserves the original bytes."""

import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.history_store import HistoryStore, MAX_HISTORY_BYTES
from src.i18n import set_language


class HistoryRecoveryGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib, Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest("GTK display unavailable; run with xvfb-run")
            from src import history_recovery
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest("GTK3 unavailable: " + str(error))
        cls.Gtk, cls.GLib, cls.module = Gtk, GLib, history_recovery

    def setUp(self):
        set_language("en")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "history.json"
        self.failures = []

    def _run_with_driver(self, operation, action):
        """Drive actual GTK responses; propagate callback assertions to unittest."""
        deadline = time.monotonic() + 8

        def drive():
            dialogs = []
            try:
                dialogs = [window for window in self.Gtk.Window.list_toplevels()
                           if isinstance(window, self.module.HistoryRecoveryDialog)]
                if not dialogs:
                    return True
                dialog = dialogs[-1]
                if time.monotonic() > deadline:
                    raise AssertionError("History recovery dialog did not finish")
                action(dialog)
            except BaseException as error:
                self.failures.append(error)
                for dialog in dialogs:
                    if not dialog.busy:
                        dialog.response(self.Gtk.ResponseType.CANCEL)
            return True

        timer = self.GLib.timeout_add(5, drive)
        try:
            result = operation()
        finally:
            self.GLib.source_remove(timer)
        if self.failures:
            raise self.failures[0]
        if result.store is not None:
            self.addCleanup(result.store.close)
        return result

    def _approve_or_close(self, dialog):
        if dialog.busy:
            return
        if dialog.result is not None:
            dialog.response(self.Gtk.ResponseType.CLOSE)
        elif dialog.error is not None:
            dialog.response(self.Gtk.ResponseType.APPLY)

    def test_healthy_startup_validates_in_worker_without_showing_dialog(self):
        main_thread = threading.get_ident()
        observed = []
        original = self.module._open_validated_store

        def checked(path):
            observed.append(threading.get_ident())
            return original(path)

        with patch.object(self.module, "_open_validated_store", side_effect=checked), \
                patch.object(self.module.HistoryRecoveryDialog, "show_all") as show:
            result = self.module.open_history_store(path=self.path)
        self.addCleanup(result.store.close)
        self.assertEqual(result.status, "ready")
        self.assertTrue(result.store.list_sessions())
        self.assertEqual(len(observed), 1)
        self.assertNotEqual(observed[0], main_thread)
        show.assert_not_called()

    def test_cancel_preserves_unknown_version_and_never_invokes_recovery(self):
        raw = b'{"version":999,"future_secret":"original data"}'
        self.path.write_bytes(raw)

        def cancel(dialog):
            if not dialog.busy and dialog.error is not None:
                self.assertIn("invalid or unsupported", dialog.explanation.get_text())
                self.assertIn("Unknown formats are not converted", dialog.detail.get_text())
                self.assertNotIn("original data", dialog.detail.get_text())
                dialog.response(self.Gtk.ResponseType.CANCEL)

        with patch.object(HistoryStore, "recover_file") as recover:
            result = self._run_with_driver(
                lambda: self.module.open_history_store(path=self.path), cancel)
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.store)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.path.parent.glob("history.json.recovered-*")), [])
        recover.assert_not_called()

    def test_explicit_recovery_preserves_unknown_and_invalid_documents(self):
        for raw in (b'{"version":999,"future":"unaltered"}',
                    b'{"version":1,"sessions":[],"active_session_id":"missing"}'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                result = self._run_with_driver(
                    lambda: self.module.open_history_store(path=self.path), self._approve_or_close)
                self.assertEqual(result.status, "recovered")
                self.assertEqual(result.backup.read_bytes(), raw)
                self.assertEqual(result.backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(self.path.read_text())["version"], 1)
                self.assertEqual(result.store.load_entries(), [])
                result.store.close()

    def test_oversized_history_offers_recovery_before_constructing_window(self):
        raw = b" " * (MAX_HISTORY_BYTES + 1)
        self.path.write_bytes(raw)
        inspected = []

        def approve(dialog):
            if not dialog.busy and dialog.result is None:
                inspected.append(dialog.explanation.get_text())
                self.assertEqual(self.path.stat().st_size, len(raw))
            self._approve_or_close(dialog)

        result = self._run_with_driver(lambda: self.module.open_history_store(path=self.path), approve)
        self.assertEqual(result.status, "recovered")
        self.assertTrue(any("16 MiB" in message for message in inspected))
        self.assertEqual(result.backup.read_bytes(), raw)
        self.assertTrue(result.store.list_sessions())

    def test_excessive_nesting_is_explained_without_claiming_file_is_large(self):
        raw = b"[" * 129 + b"]" * 129
        self.path.write_bytes(raw)
        inspected = []

        def cancel(dialog):
            if not dialog.busy and dialog.error is not None:
                inspected.append(dialog.explanation.get_text())
                dialog.response(self.Gtk.ResponseType.CANCEL)

        result = self._run_with_driver(lambda: self.module.open_history_store(path=self.path), cancel)
        self.assertEqual(result.status, "cancelled")
        self.assertIn("size or nesting limit", inspected[0])
        self.assertIn("128 levels", inspected[0])
        self.assertEqual(self.path.read_bytes(), raw)

    def test_backup_failure_is_visible_and_does_not_reset_original(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        inspected = []

        def respond(dialog):
            if dialog.result is not None:
                inspected.append(dialog.notice.get_text())
                self.assertIn("recovery failed", dialog.explanation.get_text())
            self._approve_or_close(dialog)

        with patch.object(HistoryStore, "recover_file", side_effect=PermissionError("denied")):
            result = self._run_with_driver(lambda: self.module.open_history_store(path=self.path), respond)
        self.assertEqual(result.status, "failed")
        self.assertIn("It was not reset", inspected[0])
        self.assertEqual(self.path.read_bytes(), raw)

    def test_failure_after_backup_reports_durable_backup_path(self):
        raw = b'{"version":999,"future":"preserve"}'
        self.path.write_bytes(raw)
        inspected = []

        def respond(dialog):
            if dialog.result is not None:
                inspected.append(dialog.notice.get_text())
            self._approve_or_close(dialog)

        with patch("src.history_store.atomic_json_write", side_effect=OSError("reset failed")):
            result = self._run_with_driver(lambda: self.module.open_history_store(path=self.path), respond)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.backup.read_bytes(), raw)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertIn(str(result.backup), inspected[0])

    def test_approved_recovery_runs_outside_gtk_and_cannot_be_cancelled_mid_write(self):
        self.path.write_bytes(b'{"version":999}')
        started, release = threading.Event(), threading.Event()
        original = HistoryStore.recover_file
        ticks = []
        worker_threads = []
        worker_daemons = []

        def delayed(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            worker_daemons.append(threading.current_thread().daemon)
            started.set()
            if not release.wait(3):
                raise RuntimeError("GTK did not remain responsive")
            return original(*args, **kwargs)

        def heartbeat(dialog):
            if started.is_set() and not release.is_set():
                ticks.append(threading.get_ident())
                dialog.response(self.Gtk.ResponseType.CANCEL)
                if len(ticks) >= 3:
                    release.set()
            else:
                self._approve_or_close(dialog)

        with patch.object(HistoryStore, "recover_file", side_effect=delayed):
            result = self._run_with_driver(lambda: self.module.open_history_store(path=self.path), heartbeat)
        self.assertEqual(result.status, "recovered")
        self.assertGreaterEqual(len(ticks), 3)
        self.assertTrue(all(identifier == threading.get_ident() for identifier in ticks))
        self.assertNotEqual(worker_threads, ticks[:1])
        self.assertEqual(worker_daemons, [False])

    def test_live_recovery_retains_pending_messages_and_original_bytes(self):
        store = HistoryStore(self.path)
        self.addCleanup(store.close)
        store.list_sessions()
        raw = b'{"version":999,"future":"preserve"}'
        self.path.write_bytes(raw)
        store.append("user", "queued before recovery")
        self.assertFalse(store.flush(2))
        result = self._run_with_driver(
            lambda: self.module.recover_history(None, store), self._approve_or_close)
        self.assertEqual(result.status, "recovered")
        self.assertIs(result.store, store)
        self.assertEqual(result.backup.read_bytes(), raw)
        self.assertEqual([item["content"] for item in store.load_entries()], ["queued before recovery"])
        self.assertTrue(store.flush(2))

    def _shutdown_application(self, window=None):
        from src.app import LinuxAIAssistant
        app = object.__new__(LinuxAIAssistant)
        app._quitting = False
        app._shutdown_pending = False
        app._history_shutdown_source = None
        app._global_shortcut = None
        app.config = Mock()
        app.main_window = window
        app.float_button_window = None
        app.tray_icon = None

        def remove_timer():
            if app._history_shutdown_source is not None:
                self.GLib.source_remove(app._history_shutdown_source)
                app._history_shutdown_source = None

        self.addCleanup(remove_timer)
        return app

    def _finish_shutdown(self, app):
        deadline = time.monotonic() + 3
        while not app._quitting and time.monotonic() < deadline:
            self.GLib.MainContext.default().iteration(True)
        self.assertTrue(app._quitting)
        app.config.flush.assert_called_once()

    def test_quit_dismisses_unapproved_confirmation_before_shutdown(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        app = self._shutdown_application()

        def request_quit(dialog):
            if not dialog.busy and dialog.error is not None:
                app.quit()
                self.assertTrue(app._shutdown_pending)
                self.assertFalse(app._quitting)
                app.config.flush.assert_not_called()

        result = self._run_with_driver(
            lambda: self.module.open_history_store(path=self.path), request_quit)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.path.read_bytes(), raw)
        self._finish_shutdown(app)

    def test_quit_waits_for_approved_worker_and_live_recovery_caller(self):
        store = HistoryStore(self.path)
        self.addCleanup(store.close)
        store.list_sessions()
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        store.append("user", "keep pending during shutdown")
        self.assertFalse(store.flush(2))
        window = SimpleNamespace(_history_recovering=True,
                                 close_history_writer=Mock(wraps=store.close), destroy=Mock())
        app = self._shutdown_application(window)
        started, release = threading.Event(), threading.Event()
        original = HistoryStore.recover_file
        checks = []

        def delayed(*args, **kwargs):
            started.set()
            if not release.wait(3):
                raise RuntimeError("Shutdown blocked GTK")
            return original(*args, **kwargs)

        def request_quit(dialog):
            if started.is_set() and not release.is_set():
                app.quit()
                checks.append(True)
                self.assertTrue(app._shutdown_pending)
                self.assertFalse(app._quitting)
                window.close_history_writer.assert_not_called()
                if len(checks) >= 3:
                    release.set()
            else:
                self._approve_or_close(dialog)

        with patch.object(HistoryStore, "recover_file", side_effect=delayed):
            result = self._run_with_driver(
                lambda: self.module.recover_history(None, store), request_quit)
        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.backup.read_bytes(), raw)
        self.assertEqual([item["content"] for item in store.load_entries()],
                         ["keep pending during shutdown"])
        window.close_history_writer.assert_not_called()
        window._history_recovering = False
        self._finish_shutdown(app)
        window.close_history_writer.assert_called_once()
        window.destroy.assert_called_once()


class HistoryStartupExitGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            from src import app, history_recovery
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest("GTK3 unavailable: " + str(error))
        cls.Gtk, cls.app_module, cls.recovery = Gtk, app, history_recovery

    def application(self):
        app = object.__new__(self.app_module.LinuxAIAssistant)
        app._quitting = False
        app._show_requested = False
        app.main_window = None
        app.config = Mock()
        app.config.get.return_value = False
        app.ai_client = Mock()
        app.system_utils = Mock()
        app.quit = Mock()
        app._create_float_button = Mock()
        app.configure_global_shortcut = Mock()
        return app

    def test_cancelled_startup_returns_failure_without_creating_main_window(self):
        app = self.application()
        result = self.recovery.HistoryRecoveryResult("cancelled", error=ValueError("future"))
        with patch.object(self.Gtk, "init_check", return_value=(True, [])), \
                patch.object(self.recovery, "open_history_store", return_value=result), \
                patch.object(self.app_module, "MainWindow") as window:
            self.assertEqual(app.run(), 1)
        window.assert_not_called()
        app.quit.assert_called_once()

    def test_recovered_startup_transfers_store_and_shows_backup_notice(self):
        app = self.application()
        store = Mock()
        backup = Path("/private/history.json.recovered-original")
        result = self.recovery.HistoryRecoveryResult("recovered", store=store, backup=backup)
        window = SimpleNamespace(_add_system_message=Mock())
        with patch.object(self.Gtk, "init_check", return_value=(True, [])), \
                patch.object(self.Gtk, "main"), \
                patch.object(self.recovery, "open_history_store", return_value=result), \
                patch.object(self.app_module, "MainWindow", return_value=window) as constructor, \
                patch.object(self.app_module, "TrayIcon"):
            self.assertEqual(app.run(), 0)
        self.assertIs(constructor.call_args.kwargs["history_store"], store)
        self.assertIn(str(backup), window._add_system_message.call_args.args[0])
        store.close.assert_not_called()

    def test_failed_main_window_constructor_closes_startup_writer(self):
        app = self.application()
        store = Mock()
        result = self.recovery.HistoryRecoveryResult("ready", store=store)
        with patch.object(self.Gtk, "init_check", return_value=(True, [])), \
                patch.object(self.recovery, "open_history_store", return_value=result), \
                patch.object(self.app_module, "MainWindow", side_effect=RuntimeError("constructor failed")):
            self.assertEqual(app.run(), 1)
        store.close.assert_called_once()
        app.quit.assert_called_once()

    def test_headless_initialization_returns_failure(self):
        app = self.application()
        with patch.object(self.Gtk, "init_check", return_value=(False, [])), \
                patch.object(self.recovery, "open_history_store") as history:
            self.assertEqual(app.run(), 1)
        history.assert_not_called()

    def test_main_propagates_startup_failure_exit_status(self):
        app = Mock()
        app.run.return_value = 1
        activation = Mock()
        activation.register.return_value = True
        with patch("src.global_shortcuts.DesktopActivation", return_value=activation), \
                patch.object(self.app_module, "LinuxAIAssistant", return_value=app), \
                patch("src.setup_file_logging"), patch.object(self.app_module.GLib, "unix_signal_add"):
            self.assertEqual(self.app_module.main([]), 1)
        app.run.assert_called_once_with(show=False)
        activation.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
