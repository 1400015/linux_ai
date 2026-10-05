"""Run with GTK3 and a display (xvfb-run in CI); no external API requests."""

from collections import deque
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIProviderError, AIClient, AIRequestCancelled
from src.i18n import set_language


class TestGtkStream(unittest.TestCase):
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
        cls.ChatView = ChatView
        cls.MainWindow = MainWindow

    def setUp(self):
        set_language('en')
        self.view = self.ChatView()

    def text(self):
        return self.view.buffer.get_text(self.view.buffer.get_start_iter(),
                                         self.view.buffer.get_end_iter(), True)

    def window(self, chunks):
        remembered = []
        window = SimpleNamespace(
            chat_view=self.view, _active_request=1, _cancel_event=threading.Event(),
            streaming=True, expert_mode=False, cancel_btn=Mock(), status_icon=Mock(),
            ai_client=SimpleNamespace(provider_ready=lambda: True, stream_chat=lambda messages: chunks),
            offline=SimpleNamespace(handle=lambda *args: SimpleNamespace(text='OFFLINE COMPLETE', commands=[])),
            _remember=lambda role, text: remembered.append((role, text)),
            _on_message_processed=Mock(),
        )
        for name in ('_add_loading_message', '_add_ai_message', '_add_system_message',
                     '_update_ai_message', '_abort_ai_stream_if_active', '_replace_ai_reply',
                     '_finalize_response', '_add_system_message_if_active'):
            setattr(window, name, MethodType(getattr(self.MainWindow, name), window))
        return window, remembered

    def run_worker(self, window, before_dispatch=None):
        callbacks = deque()
        def idle_add(callback, *args):
            callbacks.append((callback, args))
            return 1
        with patch('src.main_window.GLib.idle_add', side_effect=idle_add):
            self.MainWindow._process_message(window, 'question',
                                             [{'role': 'user', 'content': 'question'}],
                                             1, window._cancel_event)
            if before_dispatch:
                before_dispatch()
            while callbacks:
                callback, args = callbacks.popleft()
                callback(*args)

    def test_abort_removes_partial_reply_and_preserves_other_messages(self):
        self.view.append_message('User', 'question', 'user-message')
        self.view.show_loading()
        self.view.insert_stream_chunk('PARTIAL')
        self.view.append_message('System', 'keep this', 'system-message')
        self.view.insert_stream_chunk(' MORE')
        self.view.abort_stream()
        self.assertIn('question', self.text())
        self.assertIn('keep this', self.text())
        self.assertNotIn('PARTIAL', self.text())
        self.assertNotIn('Thinking', self.text())
        self.assertNotIn('[AI]', self.text())

    def test_failure_removes_flushed_and_pending_chunks_before_fallback(self):
        def chunks():
            yield 'FLUSHED PARTIAL'
            yield 'PENDING PARTIAL'
            raise AIProviderError('provider unavailable')
        window, remembered = self.window(chunks())
        self.run_worker(window)
        self.assertNotIn('PARTIAL', self.text())
        self.assertEqual(self.text().count('OFFLINE COMPLETE'), 1)
        self.assertEqual(remembered, [('assistant', 'OFFLINE COMPLETE')])

    def test_empty_stream_falls_back_and_is_persisted_once(self):
        window, remembered = self.window(iter([]))
        self.run_worker(window)
        self.assertEqual(remembered, [('assistant', 'OFFLINE COMPLETE')])
        self.assertEqual(self.text().count('OFFLINE COMPLETE'), 1)

    def test_cancel_after_worker_finishes_prevents_queued_persistence(self):
        window, remembered = self.window(iter(['CANCELLED ANSWER']))
        self.run_worker(window, lambda: self.MainWindow.on_cancel_streaming(window, None))
        self.assertEqual(remembered, [])
        self.assertNotIn('CANCELLED ANSWER', self.text())
        self.assertNotIn('Thinking', self.text())

    def test_unexpected_error_removes_partial_without_saving_it(self):
        def chunks():
            yield 'PARTIAL'
            raise RuntimeError('internal failure')
        window, remembered = self.window(chunks())
        self.run_worker(window)
        self.assertEqual(remembered, [])
        self.assertNotIn('PARTIAL', self.text())
        self.assertIn('internal failure', self.text())

    def test_success_preserves_text_code_tags_and_one_history_entry(self):
        answer = 'hello `code`'
        window, remembered = self.window(iter(['hello ', '`code`']))
        self.run_worker(window)
        self.assertEqual(remembered, [('assistant', answer)])
        self.assertIn(answer + '\n\n', self.text())
        offset = self.text().index('code')
        tags = self.view.buffer.get_iter_at_offset(offset).get_tags()
        self.assertIn('inline-code', [tag.get_property('name') for tag in tags])

    def test_oversized_response_is_removed_without_offline_fallback_or_persistence(self):
        window, remembered = self.window(iter(['shown first', 'x' * 131072]))
        window.offline.handle = Mock()
        self.run_worker(window)
        self.assertEqual(remembered, [])
        window.offline.handle.assert_not_called()
        self.assertNotIn('shown first', self.text())
        self.assertIn('local history limit', self.text())
        window._on_message_processed.assert_called_once()

    def test_cancelled_client_does_not_trigger_offline_fallback(self):
        window, remembered = self.window(iter([]))
        client = AIClient.__new__(AIClient)
        client._stream_chat_response = Mock(side_effect=AIRequestCancelled('cancelled'))
        client.provider_ready = Mock(return_value=True)
        window.ai_client = client
        window.offline.handle = Mock()
        self.run_worker(window)
        self.assertEqual(remembered, [])
        window.offline.handle.assert_not_called()
        window._on_message_processed.assert_called_once_with(1, True)

    def test_history_failure_settles_loading_and_keeps_rendered_response(self):
        window, remembered = self.window(iter(['complete response']))
        window.is_loading = True
        window.get_property = Mock(return_value=True)
        window._remove_loading_message = lambda: self.view.clear_loading()
        window._on_message_processed = MethodType(self.MainWindow._on_message_processed, window)
        window._remember = Mock(side_effect=OSError('synthetic history failure'))
        self.run_worker(window)
        self.assertFalse(window.is_loading)
        self.assertFalse(window.streaming)
        window.cancel_btn.set_sensitive.assert_called_with(False)
        self.assertIn('complete response', self.text())
        self.assertIn('Could not save the response', self.text())

    def test_final_callback_guards_oversized_offline_reply_and_always_settles(self):
        window, remembered = self.window(iter([]))
        self.MainWindow._finalize_response(window, 1, 'x' * 131073, False)
        self.assertEqual(remembered, [])
        self.assertIn('local history limit', self.text())
        window._on_message_processed.assert_called_once_with(1, False)

    def test_rejected_history_entry_does_not_enter_in_memory_context(self):
        window = SimpleNamespace(conversation_history=[],
                                 _save_message_to_history=Mock(side_effect=ValueError('too large')))
        with self.assertRaises(ValueError):
            self.MainWindow._remember(window, 'assistant', 'synthetic response')
        self.assertEqual(window.conversation_history, [])

    def test_completed_command_result_remains_visible_after_cancel(self):
        window, remembered = self.window(iter([]))
        window.history_store = Mock(active_session_id='original-session')
        window._record_offline_result = MethodType(self.MainWindow._record_offline_result, window)
        first, second = Mock(), Mock()
        first.display.return_value = 'synthetic first command'
        second.display.return_value = 'synthetic second command'
        def execute(command):
            window._cancel_event.set()
            return True, 'completed despite cancellation'
        with patch('src.main_window.offline_assistant.OfflineAssistant.run_privileged', side_effect=execute) as run, \
                patch('src.main_window.GLib.idle_add', side_effect=lambda callback, *args: callback(*args)):
            self.MainWindow._run_offline_commands(window, [first, second], 1, window._cancel_event,
                                                   'original-session')
        self.assertEqual(run.call_count, 1)
        self.assertIn('completed despite cancellation', self.text())
        self.assertEqual(remembered, [('assistant', '$ synthetic first command\ncompleted despite cancellation')])

    def test_cancel_during_started_local_operation_keeps_busy_until_result(self):
        window, remembered = self.window(iter([]))
        window._local_running_request = 1
        window.is_loading = True
        self.MainWindow.on_cancel_streaming(window, None)
        self.assertTrue(window.is_loading)
        self.assertIn('already started may still finish', self.text())

    def test_theme_updates_real_text_tags(self):
        self.view.set_style('DejaVu Sans Mono', 16, {'user': '#123456', 'ai': '#abcdef',
                            'system': '#999999', 'code': '#ffffff', 'code_background': '#333333'})
        tag = self.view.buffer.get_tag_table().lookup('ai-message')
        color = tag.get_property('foreground-rgba')
        self.assertAlmostEqual(color.red, 0xab / 255, places=5)
        self.assertEqual(tag.get_property('font-desc').get_family(), 'DejaVu Sans Mono')

    def test_privileged_preview_retains_the_dialog_parent(self):
        # The directory stat must never replace the Gtk.Window argument.
        from src import file_actions
        from src.render_core import FileBlock
        parent = Mock()
        dialog = Mock()
        dialog.run.return_value = -6  # Gtk.ResponseType.CANCEL
        with patch.object(file_actions, 'is_allowed_path', return_value=True), \
                patch.object(file_actions, 'is_privileged_path', return_value=True), \
                patch.object(file_actions.Gtk, 'Dialog', return_value=dialog) as factory:
            status, _ = file_actions.confirm_and_write(parent, FileBlock('/tmp/review-new.conf', 'content'))
        self.assertEqual(status, 'cancelled')
        self.assertIs(factory.call_args.kwargs['transient_for'], parent)

    def _layer_shell(self, sticks):
        return SimpleNamespace(
            init_for_window=Mock(),
            set_layer=Mock(),
            set_namespace=Mock(),
            set_anchor=Mock(),
            set_margin=Mock(),
            is_layer_window=Mock(return_value=sticks),
            Layer=SimpleNamespace(TOP='top'),
            Edge=SimpleNamespace(LEFT='left', RIGHT='right', TOP='top', BOTTOM='bottom'),
        )

    def test_float_button_falls_back_when_layer_shell_does_not_stick(self):
        from src import dock
        shell = self._layer_shell(False)
        with patch.object(dock, 'HAS_LAYER_SHELL', True), \
                patch.object(dock, 'is_wayland', return_value=True), \
                patch.object(dock, 'GtkLayerShell', shell, create=True):
            self.assertFalse(dock.apply_float_button(Mock(), 'right'))
        shell.init_for_window.assert_called_once()

    def test_float_button_uses_layer_shell_when_init_sticks(self):
        from src import dock
        shell = self._layer_shell(True)
        with patch.object(dock, 'HAS_LAYER_SHELL', True), \
                patch.object(dock, 'is_wayland', return_value=True), \
                patch.object(dock, 'GtkLayerShell', shell, create=True):
            self.assertTrue(dock.apply_float_button(Mock(), 'left'))
        shell.set_margin.assert_called_once()
        self.assertEqual(shell.set_anchor.call_args.args[1], 'left')


if __name__ == '__main__':
    unittest.main()
