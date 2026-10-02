"""Run with GTK3 and a display (xvfb-run in CI); no external API requests."""

from collections import deque
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIProviderError
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
