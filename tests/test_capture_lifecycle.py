"""Capture workers cannot write into another GTK conversation or request."""

from collections import deque
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from src.ai_client import AIClient
from src.history_store import HistoryStore
from src.i18n import set_language
from src.image_attachments import prepare_image_bytes


class CaptureLifecycleTests(unittest.TestCase):
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
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / 'screen.png'
        output = io.BytesIO()
        with Image.new('RGB', (12, 8), 'blue') as image:
            image.save(output, format='PNG')
        self.image_bytes = output.getvalue()
        self.source.write_bytes(self.image_bytes)
        self.store = HistoryStore(self.root / 'history.json')
        self.store.list_sessions()
        self.original_session = self.store.active_session_id
        self.window = self.MainWindow.__new__(self.MainWindow)
        self.Gtk.Window.__init__(self.window)
        window = self.window
        window.config = SimpleNamespace(get=lambda key, default=None: default)
        window.system_utils = Mock()
        window.system_utils.capture_screen.return_value = (True, str(self.source))
        window.system_utils.extract_text_from_image.return_value = (True, 'PRIVATE OCR CONTENT')
        window.history_store = self.store
        window.chat_view = self.ChatView()
        window.conversation_history = []
        window._request_seq = window._active_request = 3
        window._cancel_event = threading.Event()
        window.is_loading = window.streaming = False
        window._audit_closed = False
        window.cancel_btn = self.Gtk.Button()
        window.status_icon = self.Gtk.Image()
        window.attachment_label = self.Gtk.Label()
        window.remove_image_button = self.Gtk.Button()
        window._pending_image = window._pending_image_destination = None
        window.offline = Mock()
        window._renew_offline_assistant = Mock()
        window._refresh_session_controls = Mock()
        window._refresh_mode_status = Mock()
        window.show_notification = Mock()
        window.ai_client = AIClient.__new__(AIClient)
        window.ai_client.validate_image_request = Mock(return_value=('openrouter', 'openai/gpt-4o-mini'))
        self.callbacks = deque()
        self.threads = []
        self.releases = []
        original_thread = threading.Thread

        def thread_factory(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            self.threads.append(thread)
            return thread

        self.idle_patch = patch('src.main_window.GLib.idle_add', side_effect=self.queue)
        self.thread_patch = patch('src.main_window.threading.Thread', side_effect=thread_factory)
        self.idle_patch.start()
        self.thread_patch.start()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for thread in self.threads:
            thread.join(2)
        self.window._audit_closed = True
        self.drain()
        self.thread_patch.stop()
        self.idle_patch.stop()
        self.store.close()
        self.window.destroy()
        self.directory.cleanup()
        set_language('en')

    def queue(self, callback, *args):
        self.callbacks.append((callback, args))
        return 1

    def drain(self):
        while self.callbacks:
            callback, args = self.callbacks.popleft()
            callback(*args)

    def join_worker(self):
        self.threads[-1].join(2)
        self.assertFalse(self.threads[-1].is_alive(), 'Capture worker did not finish')

    def text(self):
        buffer = self.window.chat_view.buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def block_ocr(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def ocr(path):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('Test did not release its OCR worker')
            return True, 'PRIVATE OCR CONTENT'

        self.window.system_utils.extract_text_from_image.side_effect = ocr
        return entered, release

    def test_capture_after_cancel_uses_fresh_event_and_saves_ocr_once(self):
        previous = self.window._cancel_event
        self.window.on_cancel_streaming(None)
        self.assertTrue(previous.is_set())
        self.window.on_capture_screen_clicked(None)
        self.assertIsNot(self.window._cancel_event, previous)
        self.assertFalse(self.window._cancel_event.is_set())
        self.assertTrue(self.window.cancel_btn.get_sensitive())
        self.join_worker()
        self.drain()
        self.store.flush()
        self.assertEqual(self.text().count('PRIVATE OCR CONTENT'), 1)
        self.assertEqual(self.store.load_messages()[0]['content'], '[Screen capture]\nPRIVATE OCR CONTENT')
        self.assertFalse(self.window.is_loading)
        self.assertFalse(self.window.cancel_btn.get_sensitive())
        self.assertEqual(self.window.status_icon.get_tooltip_text(), 'Ready')
        self.assertFalse(self.source.exists())

    def test_delayed_ocr_session_switch_preserves_both_histories(self):
        entered, release = self.block_ocr()
        self.window.on_capture_screen_clicked(None)
        self.assertTrue(entered.wait(1))
        new = self.store.create_session('Other conversation', select=False)
        self.window._switch_session(new['id'])
        release.set()
        self.join_worker()
        self.drain()
        self.store.flush()
        self.assertNotIn('PRIVATE OCR CONTENT', self.text())
        self.assertNotIn('Screen captured:', self.text())
        self.assertEqual(self.store.load_messages(self.original_session), [])
        self.assertEqual(self.store.load_messages(new['id']), [])
        self.assertFalse(self.source.exists())

    def test_cancelled_ocr_cannot_finish_or_overwrite_new_request_state(self):
        entered, release = self.block_ocr()
        self.window.on_capture_screen_clicked(None)
        self.assertTrue(entered.wait(1))
        self.window.on_cancel_streaming(None)
        self.window._request_seq += 1
        self.window._active_request = self.window._request_seq
        self.window._cancel_event = threading.Event()
        self.window.is_loading = self.window.streaming = True
        self.window.cancel_btn.set_sensitive(True)
        self.window.status_icon.set_from_icon_name('process-working', self.Gtk.IconSize.MENU)
        self.window.status_icon.set_tooltip_text('Processing new question')
        release.set()
        self.join_worker()
        self.drain()
        self.assertNotIn('PRIVATE OCR CONTENT', self.text())
        self.assertEqual(self.store.load_messages(), [])
        self.assertTrue(self.window.is_loading)
        self.assertTrue(self.window.streaming)
        self.assertTrue(self.window.cancel_btn.get_sensitive())
        self.assertEqual(self.window.status_icon.get_tooltip_text(), 'Processing new question')
        self.assertFalse(self.source.exists())

    def test_cancelled_capture_preserves_cancelled_status(self):
        entered, release = self.block_ocr()
        self.window.on_capture_screen_clicked(None)
        self.assertTrue(entered.wait(1))
        self.window.on_cancel_streaming(None)
        release.set()
        self.join_worker()
        self.drain()
        self.assertEqual(self.window.status_icon.get_tooltip_text(), 'Cancelled')
        self.assertFalse(self.window.is_loading)
        self.assertFalse(self.window.cancel_btn.get_sensitive())
        self.assertNotIn('PRIVATE OCR CONTENT', self.text())
        self.assertEqual(self.store.load_messages(), [])

    def test_prepared_image_cancelled_before_dispatch_never_opens_review(self):
        self.window.on_capture_screen_clicked(None, for_ai=True)
        self.join_worker()
        self.window.on_cancel_streaming(None)
        with patch('src.main_window.review_image') as review:
            self.drain()
        review.assert_not_called()
        self.assertIsNone(self.window._pending_image)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.window.status_icon.get_tooltip_text(), 'Cancelled')

    def test_prepared_image_from_old_session_never_opens_review(self):
        self.window.on_capture_screen_clicked(None, for_ai=True)
        self.join_worker()
        new = self.store.create_session('Other conversation', select=False)
        self.window._switch_session(new['id'])
        with patch('src.main_window.review_image') as review:
            self.drain()
        review.assert_not_called()
        self.assertIsNone(self.window._pending_image)
        self.assertEqual(self.store.load_messages(), [])

    def test_cancel_during_modal_review_does_not_attach_approved_image(self):
        self.window.on_capture_screen_clicked(None, for_ai=True)
        self.join_worker()

        def review(*args):
            self.window.on_cancel_streaming(None)
            return True

        with patch('src.main_window.review_image', side_effect=review):
            self.drain()
        self.assertIsNone(self.window._pending_image)
        self.assertEqual(self.window.attachment_label.get_text(), '')
        self.assertEqual(self.window.status_icon.get_tooltip_text(), 'Cancelled')

    def test_successful_capture_attaches_snapshot_after_source_cleanup(self):
        self.window.on_capture_screen_clicked(None, for_ai=True)
        self.join_worker()
        self.assertFalse(self.source.exists())
        with patch('src.main_window.review_image', return_value=True) as review:
            self.drain()
        review.assert_called_once()
        self.assertEqual(self.window._pending_image.data,
                         prepare_image_bytes(self.image_bytes).data)
        self.assertEqual(self.window._pending_image_destination, ('openrouter', 'openai/gpt-4o-mini'))
        self.assertEqual(self.store.load_messages(), [])
        self.assertFalse(self.window.is_loading)
        self.assertFalse(self.window.cancel_btn.get_sensitive())


if __name__ == '__main__':
    unittest.main()
