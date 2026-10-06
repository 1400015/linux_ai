"""Credential warnings stay with their request and name a removable source."""

import os
from pathlib import Path
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.config_manager import ConfigManager
from src.i18n import get_language, set_language


class ChatKeyDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import GLib, Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src.chat_view import ChatView
            from src.main_window import MainWindow
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.GLib, cls.ChatView, cls.MainWindow = GLib, ChatView, MainWindow

    def setUp(self):
        self.language = get_language()
        set_language('en')
        self.addCleanup(set_language, self.language)
        self.view = self.ChatView()
        self.config = SimpleNamespace(
            stored_key_shadowed_by_empty_env=Mock(return_value=True),
            api_key_env_var=Mock(return_value='LINUX_AI_API_PROVIDERS_MISTRAL_API_KEY'),
            can_remove_empty_api_key_override=Mock(return_value=False),
        )
        self.window = SimpleNamespace(
            chat_view=self.view, _active_request=1, _cancel_event=threading.Event(),
            streaming=True, config=self.config,
            ai_client=SimpleNamespace(active_provider=Mock(return_value='google_ai_studio')),
            offline=SimpleNamespace(handle=Mock(return_value=SimpleNamespace(text='LOCAL REPLY', commands=[]))),
            _finalize_response=Mock(return_value=False),
        )
        for name in ('_add_loading_message', '_add_ai_message', '_add_system_message',
                     '_replace_ai_reply', '_add_system_message_if_active'):
            setattr(self.window, name, MethodType(getattr(self.MainWindow, name), self.window))
        self.addCleanup(self.view.scrolled.destroy)
        self.addCleanup(self.drain)

    def drain(self):
        # Exercise the real idle queue: superseding/cancelling the request must
        # happen after the worker returns and before GTK consumes its output.
        context = self.GLib.MainContext.default()
        for _ in range(1000):
            if not context.pending():
                return
            context.iteration(False)
        self.fail('The GTK idle queue did not settle')

    def text(self):
        buffer = self.view.buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def run_worker(self):
        self.MainWindow._process_message(
            self.window, 'question', [], 1, self.window._cancel_event,
            route={'provider': 'mistral', 'ready': False}, session_id='original',
        )

    def test_warning_uses_request_provider_and_not_changed_selection(self):
        self.run_worker()
        self.drain()
        self.window.ai_client.active_provider.assert_not_called()
        self.config.stored_key_shadowed_by_empty_env.assert_called_once_with('mistral')
        self.assertIn('stored key for mistral', self.text())
        self.assertNotIn('google_ai_studio', self.text())

    def test_cancel_before_idle_delivery_drops_warning(self):
        self.run_worker()
        self.window._cancel_event.set()
        self.drain()
        self.assertNotIn('stored key', self.text())
        self.assertNotIn('LOCAL REPLY', self.text())

    def test_new_conversation_before_idle_delivery_drops_warning(self):
        self.run_worker()
        self.window._active_request = 2
        self.drain()
        self.assertNotIn('stored key', self.text())
        self.assertNotIn('LOCAL REPLY', self.text())

    def test_shutdown_cancels_warning_before_destroying_widgets(self):
        self.window.history_store = SimpleNamespace(close=Mock(return_value=True))
        self.run_worker()
        self.MainWindow.close_history_writer(self.window)
        self.assertTrue(self.window._cancel_event.is_set())
        self.drain()
        self.assertNotIn('stored key', self.text())
        self.assertNotIn('LOCAL REPLY', self.text())

    def test_portuguese_warning_names_the_translated_removal_button(self):
        set_language('pt')
        self.config.can_remove_empty_api_key_override.return_value = True
        self.run_worker()
        self.drain()
        self.assertIn('Usar chave guardada (remover substituição vazia do .env)', self.text())
        self.assertNotIn('Use stored key', self.text())

    def test_no_shadowing_keeps_offline_reply_without_credential_warning(self):
        self.config.stored_key_shadowed_by_empty_env.return_value = False
        self.run_worker()
        self.drain()
        self.assertIn('LOCAL REPLY', self.text())
        self.assertNotIn('stored key', self.text())
        self.config.can_remove_empty_api_key_override.assert_not_called()

    def test_inherited_empty_variable_does_not_claim_an_env_file_or_button(self):
        with tempfile.TemporaryDirectory() as directory:
            name = 'LINUX_AI_API_PROVIDERS_MISTRAL_API_KEY'
            with patch.dict(os.environ, {name: ''}):
                config = ConfigManager(str(Path(directory) / 'config.json'))
                config.set_api_key('mistral', 'test-only-private-key')
                config.flush()
                self.window.config = config
                self.assertFalse((Path(directory) / '.env').exists())
                self.assertFalse(config.can_remove_empty_api_key_override('mistral'))
                self.run_worker()
                self.drain()
                text = self.text()
                self.assertIn(name + ' environment variable', text)
                self.assertIn('at its source, then restart', text)
                self.assertNotIn('.env', text)
                self.assertNotIn('Use stored key', text)
                self.assertNotIn('test-only-private-key', text)

    def test_owned_empty_env_assignment_offers_settings_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            name = 'LINUX_AI_API_PROVIDERS_MISTRAL_API_KEY'
            path = Path(directory)
            (path / '.env').write_text(name + '=\n', encoding='utf-8')
            with patch.dict(os.environ):
                os.environ.pop(name, None)
                config = ConfigManager(str(path / 'config.json'))
                config.set_api_key('mistral', 'test-only-private-key')
                config.flush()
                self.window.config = config
                self.assertTrue(config.can_remove_empty_api_key_override('mistral'))
                self.run_worker()
                self.drain()
                text = self.text()
                self.assertIn("choose 'Use stored key (remove empty override from .env)'", text)
                self.assertNotIn('at its source', text)
                self.assertNotIn('test-only-private-key', text)


if __name__ == '__main__':
    unittest.main()
