"""End-to-end CLI/session boundaries and shared context; no remote services."""

import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.assistant_context import build_system_message
from src.cli import CLIApp
from src.conversation_io import MAX_IMPORT_BYTES, read_conversation, write_conversation
from src.offline_assistant import detect_distro
from src.i18n import set_language


class TestFeatureInterfaces(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.home = patch('pathlib.Path.home', return_value=self.root)
        self.home.start()
        set_language('pt')
        self.app = CLIApp()
        self.app.config.set('app.language', 'pt')
        self.app.config.set_assistance_mode('offline')
        self.app.config.set_api_key('openrouter', 'configured-but-offline')
        set_language('pt')

    def tearDown(self):
        self.app.history_store.close()
        self.app.config.flush()
        self.app.ai_client.flush_usage()
        self.home.stop()
        self.directory.cleanup()
        set_language('en')

    def args(self, *arguments):
        with patch('sys.argv', ['linux-ai', *arguments]):
            return self.app.parse_args()

    def test_new_session_stdin_is_saved_without_contacting_a_provider(self):
        old = self.app._store().active_session_id
        self.app._store().append('user', 'old secret context')
        self.app._store().flush()
        args = self.args('chat', '--new-session', '--stdin', 'guia local rotas')
        with patch('sys.stdin', io.StringIO('erro de rota local')), \
                patch('sys.stdout', io.StringIO()), \
                patch('requests.Session.request', side_effect=AssertionError('Unexpected network')):
            self.assertEqual(self.app.handle_chat(args), 0)
        current = self.app._store().active_session_id
        self.assertNotEqual(old, current)
        entries = self.app._store().load_messages()
        self.assertEqual(len(entries), 2)
        self.assertIn('erro de rota local', entries[0]['content'])
        context = self.app._build_request_messages('continua')
        self.assertFalse(any('old secret context' in entry['content'] for entry in context))
        self.assertEqual(self.app._store().load_messages(old)[0]['content'], 'old secret context')

    def test_resume_session_and_no_history_preserve_existing_messages(self):
        store = self.app._store()
        first = store.active_session_id
        store.append('user', 'retomar esta conversa')
        store.flush()
        second = store.create_session('outra')['id']
        args = self.args('chat', '--session', first, '--no-history', 'guia local rede')
        with patch('sys.stdout', io.StringIO()):
            self.app.handle_chat(args)
        self.assertEqual(store.active_session_id, first)
        self.assertEqual(len(store.load_messages(first)), 1)
        self.assertEqual(store.load_messages(second), [])

    def test_history_clear_keeps_other_sessions(self):
        store = self.app._store()
        first = store.active_session_id
        store.append('user', 'keep')
        store.flush()
        second = store.create_session('clear this')['id']
        store.append('user', 'discard')
        store.flush()
        with patch('sys.stdout', io.StringIO()):
            self.app.handle_history(self.args('history', '--clear'))
        self.assertEqual(store.load_messages(second), [])
        self.assertEqual(store.load_messages(first)[0]['content'], 'keep')

    def test_cli_continues_a_guide_across_process_instances(self):
        self.app.offline.system_utils = None
        with patch('sys.stdout', io.StringIO()):
            self.app.handle_chat(self.args('chat', 'guia network-interface'))
        self.app.config.flush()
        self.assertEqual(self.app._store().get_diagnostic_state(), {'id': 'network-interface', 'step': 0})
        resumed = CLIApp()
        resumed.offline.system_utils = None
        try:
            with patch('sys.stdout', io.StringIO()):
                resumed.handle_chat(self.args('chat', 'e depois?'))
            self.assertEqual(resumed._store().get_diagnostic_state(), {'id': 'network-interface', 'step': 1})
        finally:
            resumed.history_store.close()
            resumed.config.flush()
            resumed.ai_client.flush_usage()

    def test_export_import_roundtrip_through_cli_files(self):
        store = self.app._store()
        store.append('user', 'conteúdo português')
        store.flush()
        destination = self.root / 'conversation.json'
        with patch('sys.stdout', io.StringIO()):
            self.app.handle_sessions(self.args('sessions', 'export', '--format', 'json', '--output', str(destination)))
            self.app.handle_sessions(self.args('sessions', 'import', str(destination)))
        self.assertEqual(store.load_messages()[0]['content'], 'conteúdo português')
        self.assertIsInstance(json.loads(destination.read_text(encoding='utf-8')), dict)

    def test_input_is_bounded_and_empty_message_rejected(self):
        source = self.root / 'error.log'
        source.write_text('x' * 65537, encoding='utf-8')
        with self.assertRaises(ValueError):
            self.app._message_from_args(self.args('chat', '--input', str(source)))
        with self.assertRaises(ValueError):
            self.app._message_from_args(self.args('chat'))
        with patch('sys.stdin', io.StringIO('é' * 40000)), self.assertRaises(ValueError):
            self.app._message_from_args(self.args('chat', '--stdin'))

    def test_cli_context_has_language_and_curated_references_without_probes(self):
        self.app.offline._distro = detect_distro({'ID': 'ubuntu', 'VERSION_ID': '24.04'},
                                               which=lambda name: None, is_systemd_running=False)
        with patch('requests.Session.request', side_effect=AssertionError('Unexpected network')):
            message = self.app._get_context_message('DNS rede')
        self.assertIn('Portuguese', message['content'])
        self.assertIn('24.04', message['content'])
        self.assertIn('network-', message['content'])
        self.assertEqual(message, build_system_message(False, self.app.offline.distro, 'DNS rede', 'pt'))


class TestConversationFiles(unittest.TestCase):
    def test_existing_export_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.md'
            path.write_text('keep', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                write_conversation(path, 'replace')
            self.assertEqual(path.read_text(encoding='utf-8'), 'keep')

    def test_import_read_limit_and_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'import.json'
            path.write_bytes(b'x' * (MAX_IMPORT_BYTES + 1))
            with self.assertRaises(ValueError):
                read_conversation(path)
            path.write_bytes(b'\xff')
            with self.assertRaises(UnicodeDecodeError):
                read_conversation(path)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission check')
    def test_export_permissions_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.md'
            write_conversation(path, 'conteúdo')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TestGuiFeatureInterfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable')
            from src.main_window import MainWindow
            from src.chat_view import ChatView
            from src.provider_settings import ProviderSettings
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest(str(error))
        cls.Gtk, cls.MainWindow, cls.ChatView, cls.ProviderSettings = Gtk, MainWindow, ChatView, ProviderSettings

    def test_switch_session_invalidates_late_reply_and_resets_diagnostic(self):
        import threading
        from src.config_manager import ConfigManager
        from src.ai_client import AIClient
        from src.history_store import HistoryStore
        from src.offline_assistant import OfflineAssistant
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / 'config.json'))
            config.set_assistance_mode('offline')
            with patch('pathlib.Path.home', return_value=Path(directory)):
                client = AIClient(config)
            store = HistoryStore(Path(directory) / 'history.json')
            window = self.MainWindow.__new__(self.MainWindow)
            self.Gtk.Window.__init__(window)
            window.config, window.ai_client = config, client
            window.system_utils = None
            window.history_store = store
            store.list_sessions()
            store.append('user', 'old session secret')
            store.flush()
            old_id = store.active_session_id
            store.set_diagnostic_state({'id': 'network-route', 'step': 0})
            window.offline = OfflineAssistant(os_release={'ID': 'ubuntu'}, which=lambda name: None,
                                              is_systemd_running=False)
            previous_assistant = window.offline
            window._cancel_event = threading.Event()
            window._request_seq = window._active_request = 5
            window._refreshing_sessions = False
            window.expert_mode = False
            window.cancel_btn = self.Gtk.Button()
            window.status_icon = self.Gtk.Image()
            window.session_combo = self.Gtk.ComboBoxText()
            window.mode_label = self.Gtk.Label()
            window.chat_view = self.ChatView()
            window.chat_view.show_loading()
            window.status_icon.set_from_icon_name('process-working', self.Gtk.IconSize.MENU)
            window.conversation_history = store.load_messages()
            new = store.create_session('new', select=False)
            try:
                window._switch_session(new['id'])
                window._finalize_response(5, 'late response', False)
                store.flush()
                self.assertEqual(window.conversation_history, [])
                self.assertEqual(store.load_messages(), [])
                self.assertIsNot(window.offline, previous_assistant)
                self.assertGreater(window._active_request, 5)
                self.assertEqual(window.session_combo.get_active_id(), new['id'])
                self.assertNotIn('old session secret', window._get_context_message()['content'])
                self.assertFalse(window.chat_view.has_loading())
                self.assertEqual(window.status_icon.get_icon_name()[0], 'emblem-ok')
                window._switch_session(old_id)
                self.assertEqual(window.offline.diagnostic_state(), {'id': 'network-route', 'step': 0})
            finally:
                store.close()
                config.flush()
                client.flush_usage()
                window.destroy()

    def test_complete_window_and_cancelled_settings_preserve_configuration(self):
        import copy
        from src.config_manager import ConfigManager
        from src.ai_client import AIClient
        from src.system_utils import SystemUtils
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)):
            config = ConfigManager(str(Path(directory) / 'config.json'))
            client = AIClient(config)
            window = self.MainWindow(SimpleNamespace(tray_icon=None), config, client, SystemUtils(config))
            original = copy.deepcopy(config.config)
            try:
                with patch.object(self.Gtk.Dialog, 'run', return_value=self.Gtk.ResponseType.CANCEL):
                    window._show_config_dialog()
                self.assertEqual(config.config, original)
                self.assertEqual(window.session_combo.get_active_id(), window.history_store.active_session_id)
                self.assertTrue(window.mode_label.get_text())
            finally:
                window.history_store.close()
                client.flush_usage()
                config.flush()
                window.destroy()

    def test_connection_test_uses_draft_without_persisting_or_overwriting_status(self):
        from src.config_manager import ConfigManager
        from src.provider_modes import ProviderStatus
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / 'config.json'))
            original = config.get_local_model_settings().copy()
            client = Mock()
            settings = self.ProviderSettings(config, client)
            try:
                settings.url.set_text('http://127.0.0.1:9999/v1')
                draft = settings.draft()
                self.assertEqual(config.get_local_model_settings(), original)
                result = ProviderStatus('local', 'local_llm', 'ready', True, True,
                                        draft['model'], ('llama3.2:latest',), '')
                generation = settings._generation
                settings._show_result(generation, result, None)
                self.assertIn('ready', settings.status.get_text().lower())
                self.assertEqual(settings.model.get_child().get_text(), draft['model'])
                self.assertEqual(config.get_local_model_settings(), original)
            finally:
                settings.destroy()
                config.flush()

    def test_confirmed_command_sequence_stops_after_first_failure(self):
        import threading
        from src.offline_assistant import Command
        commands = [Command(['apt-get', 'update']), Command(['apt-get', 'upgrade'])]
        window = SimpleNamespace(_active_request=1, _record_offline_result=lambda *args: None)
        with patch('src.main_window.offline_assistant.OfflineAssistant.run_privileged', return_value=(False, 'failed')) as run, \
                patch('src.main_window.GLib.idle_add'):
            self.MainWindow._run_offline_commands(window, commands, 1, threading.Event())
        self.assertEqual(run.call_count, 1)

    def test_offline_request_keeps_original_route_and_cancelled_request_sends_nothing(self):
        import threading
        from unittest.mock import Mock
        client = SimpleNamespace(provider_ready=Mock(return_value=True),
                                 stream_chat=Mock(return_value=iter(['remote response'])))
        offline = SimpleNamespace(handle=Mock(return_value=SimpleNamespace(text='local answer', commands=[])))
        window = SimpleNamespace(_active_request=1, ai_client=client, offline=offline,
                                 _add_loading_message=Mock(), _replace_ai_reply=Mock(),
                                 _finalize_response=Mock(), _update_ai_message=Mock())
        with patch('src.main_window.GLib.idle_add'):
            self.MainWindow._process_message(window, 'private question', [], 1, threading.Event(),
                                             offline, {'ready': False, 'provider': None})
        client.stream_chat.assert_not_called()
        client.provider_ready.assert_not_called()
        offline.handle.assert_called_once()
        cancelled = threading.Event()
        cancelled.set()
        with patch('src.main_window.GLib.idle_add') as dispatch:
            self.MainWindow._process_message(window, 'private question', [], 1, cancelled,
                                             offline, {'ready': True, 'provider': 'openrouter'})
        client.stream_chat.assert_not_called()
        dispatch.assert_not_called()

    def test_request_uses_captured_provider_and_stale_command_result_stays_in_original_session(self):
        import threading
        from unittest.mock import Mock
        client = SimpleNamespace(provider_ready=Mock(return_value=True),
                                 stream_chat=Mock(return_value=iter(['response'])))
        window = SimpleNamespace(_active_request=1, ai_client=client, offline=Mock(),
                                 _add_loading_message=Mock(), _replace_ai_reply=Mock(),
                                 _finalize_response=Mock(), _update_ai_message=Mock(),
                                 _cancel_event=threading.Event(), history_store=Mock(),
                                 _add_system_message=Mock(), _remember=Mock())
        window.history_store.list_sessions.return_value = [{'id': 'original-session'}]
        with patch('src.main_window.GLib.idle_add'):
            self.MainWindow._process_message(window, 'question', [], 1, threading.Event(),
                                             window.offline, {'ready': True, 'provider': 'local_llm'})
        client.stream_chat.assert_called_once_with([], provider='local_llm')
        self.MainWindow._record_offline_result(window, 0, 'old result', 'original-session')
        window._add_system_message.assert_not_called()
        window._remember.assert_not_called()
        window.history_store.append.assert_called_once_with('assistant', 'old result', session_id='original-session')
        window.history_store.append.reset_mock()
        window.history_store.list_sessions.return_value = []
        self.MainWindow._record_offline_result(window, 0, 'old result', 'deleted-session')
        window.history_store.append.assert_not_called()
