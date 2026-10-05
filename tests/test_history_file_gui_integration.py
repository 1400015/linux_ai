"""History warnings and completed file operations in their owning conversation."""

from pathlib import Path
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.history_store import HistoryStore
from src.i18n import set_language


class TestHistoryFileGuiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src.chat_view import ChatView
            from src.main_window import MainWindow
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.Gtk, cls.ChatView, cls.MainWindow = Gtk, ChatView, MainWindow

    def setUp(self):
        set_language('en')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = HistoryStore(Path(self.temp.name) / 'history.json')
        self.addCleanup(self.store.close)
        self.store.list_sessions()
        self.view = self.ChatView()
        self.window = SimpleNamespace(
            history_store=self.store, conversation_history=[], chat_view=self.view,
            history_warning=self.Gtk.InfoBar(), _history_recovering=False,
            _active_request=1, _cancel_event=threading.Event(),
            _cancel_for_session_change=Mock(), _renew_offline_assistant=Mock(),
            _refresh_session_controls=Mock(),
        )
        for name in ('_add_system_message', '_remember', '_save_message_to_history',
                     '_record_file_action_result', '_show_history_warning',
                     '_check_history_writer', '_recover_history'):
            setattr(self.window, name, MethodType(getattr(self.MainWindow, name), self.window))
        self.window._load_conversation_history = lambda: setattr(
            self.window, 'conversation_history', self.store.load_messages())

    def text(self):
        buffer = self.view.buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def fail_writer(self):
        self.store.path.write_text('{"version": 999, "valuable": "original"}')
        self.store.append('user', 'Pending message must survive')
        self.assertFalse(self.store.flush())

    def test_failed_async_writer_shows_persistent_warning_and_preserves_input(self):
        self.fail_writer()
        self.assertTrue(self.window._check_history_writer())
        self.assertTrue(self.window.history_warning.get_visible())
        self.window.input_entry = self.Gtk.Entry()
        self.window.input_entry.set_text('Keep my next question')
        self.MainWindow.on_send_clicked(self.window)
        self.assertEqual(self.window.input_entry.get_text(), 'Keep my next question')
        self.assertNotIn('Keep my next question', self.store.path.read_text())

    def test_live_recovery_reloads_pending_messages_and_shows_original_backup(self):
        self.fail_writer()
        self.store.append('assistant', 'Pending answer must remain visible')
        self.assertFalse(self.store.flush())
        original = self.store.path.read_bytes()
        self.window._request_seq = 1
        self.window.is_loading = self.window.streaming = False
        self.window.cancel_btn = self.Gtk.Button()
        self.window.status_icon = self.Gtk.Image()
        self.window._clear_pending_image = Mock()
        self.window._refresh_mode_status = Mock()
        self.window.offline = SimpleNamespace(restore_diagnostic=Mock())
        for name in ('_cancel_for_session_change', '_load_conversation_history',
                     '_add_user_message', '_add_ai_message'):
            setattr(self.window, name, MethodType(getattr(self.MainWindow, name), self.window))
        old_event = self.window._cancel_event
        def recover(parent, store, error):
            backup = store.recover(confirmed=True)
            return SimpleNamespace(status='recovered', backup=backup)
        with patch('src.history_recovery.recover_history', side_effect=recover):
            self.window._recover_history()
        self.assertFalse(self.window.history_warning.get_visible())
        self.assertFalse(self.window._history_recovering)
        self.assertEqual(self.window.conversation_history,
                         [{'role': 'user', 'content': 'Pending message must survive'},
                          {'role': 'assistant', 'content': 'Pending answer must remain visible'}])
        self.assertTrue(old_event.is_set())
        self.assertFalse(self.window._cancel_event.is_set())
        self.assertIn('Pending answer must remain visible', self.text())
        backups = list(self.store.path.parent.glob('history.json.recovered-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertIn(str(backups[0]), self.text())

    def test_cancelled_recovery_keeps_failed_history_and_pending_queue(self):
        self.fail_writer()
        original = self.store.path.read_bytes()
        self.window._check_history_writer()
        with patch('src.history_recovery.recover_history',
                   return_value=SimpleNamespace(status='cancelled')):
            self.window._recover_history()
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertTrue(self.window.history_warning.get_visible())
        self.assertIsNotNone(self.store.last_error)
        self.assertFalse(self.window._history_recovering)

    def test_completed_file_write_after_switch_is_saved_only_in_owning_conversation(self):
        original = self.store.active_session_id
        current = self.store.create_session('Another conversation')['id']
        self.window._record_file_action_result(original, 'written', 'File written: /tmp/reviewed.conf')
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.load_messages(current), [])
        self.assertEqual(self.store.load_messages(original),
                         [{'role': 'assistant', 'content': 'File written: /tmp/reviewed.conf'}])
        self.assertEqual(self.window.conversation_history, [])
        self.assertIn('another conversation', self.text())

    def test_completed_restore_after_cancel_is_visible_and_saved_once(self):
        self.window._cancel_event.set()
        self.window._record_file_action_result(self.store.active_session_id, 'restored',
                                               'File recovered. Recovery backup: /tmp/recovery.bak')
        self.assertTrue(self.store.flush())
        self.assertEqual(len(self.store.load_messages()), 1)
        self.assertIn('File recovered.', self.text())
        self.assertEqual(len(self.window.conversation_history), 1)

    def test_unapproved_cancellation_creates_no_completed_operation_entry(self):
        self.window._record_file_action_result(self.store.active_session_id, 'cancelled', '')
        self.assertEqual(self.store.load_messages(), [])
        self.assertEqual(self.text(), '')

    def test_history_reset_waits_for_an_approved_file_transaction(self):
        self.window._file_action_controllers = [SimpleNamespace(active=True)]
        with patch('src.history_recovery.recover_history') as recover:
            self.window._recover_history()
        recover.assert_not_called()
        self.assertIn('Wait for the approved file operation', self.text())
        self.window._file_action_controllers = []
        self.window._file_recovery_active = 1
        with patch('src.history_recovery.recover_history') as recover:
            self.window._recover_history()
        recover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
