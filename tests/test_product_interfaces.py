"""Product flows retain consent, conversation isolation and local-only actions."""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from src.ai_client import AIClient, AIImageRequestError, AIProviderError
from src.change_journal import ChangeJournal
from src.cli import CLIApp
from src.document_store import DocumentStore
from src.document_context import display_document_context
from src.history_store import HistoryStore
from src.i18n import set_language
from src.image_attachments import prepare_image


class ProductFixtures(unittest.TestCase):
    def setUp(self):
        set_language('en')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.history = HistoryStore(self.root / 'history.json')
        self.addCleanup(self.history.close)
        self.history.create_session('Existing conversation')
        self.history.append('user', 'Earlier question')
        self.history.append('assistant', 'Earlier answer')
        self.assertTrue(self.history.flush())
        self.index_path = self.root / 'documents' / 'index.sqlite3'
        self.documents = DocumentStore(self.index_path)
        self.addCleanup(self.documents.close)
        self.manual = self.root / 'manual.md'
        self.manual.write_text('Network setup\nunique-local-excerpt-314159 network address\n', encoding='utf-8')
        self.record = self.documents.add([self.manual])[0]
        self.image_path = self.root / 'selected.png'
        with Image.new('RGB', (9, 5), 'red') as image:
            image.save(self.image_path, format='PNG')
        self.image = prepare_image(self.image_path)

    def provider(self, ready=True):
        client = AIClient.__new__(AIClient)
        client.active_provider = Mock(return_value='openrouter')
        client.provider_ready = Mock(return_value=ready)
        client.validate_image_request = Mock(return_value=('openrouter', 'openai/gpt-4o-mini'))
        client.chat = Mock(return_value='Reviewed answer [document:{}:L1-L2]'.format(self.record['id']))
        client.stream_chat = Mock(side_effect=lambda messages, **options: iter(['Reviewed answer']))
        return client

    def offline(self):
        return SimpleNamespace(handle=Mock(), propose=Mock(), diagnostic_state=Mock(return_value=None),
                               restore_diagnostic=Mock())

    def assert_no_automatic_actions(self, app):
        app.actions.handle.assert_not_called()
        app.offline.handle.assert_not_called()
        app.offline.propose.assert_not_called()

    def assert_local_summary_only(self):
        self.assertEqual(self.history.load_messages()[-1]['content'],
                         'Local document matches were shown. Excerpts remain only in the local document index.')
        self.assertNotIn('unique-local-excerpt-314159', self.history.path.read_text())


class TestProductCli(ProductFixtures):
    def setUp(self):
        super().setUp()
        self.app = CLIApp.__new__(CLIApp)
        self.app.history_store = self.history
        self.app.conversation_history = self.history.load_entries()
        self.app._pending_exchanges = []
        self.app.expert_mode = False
        self.app.config = SimpleNamespace(get=lambda key, default=None: default)
        self.app.offline = self.offline()
        self.app.actions = SimpleNamespace(handle=Mock())
        self.app.ai_client = self.provider()
        self.app._get_context_message = Mock(return_value=None)
        self.app._offer_model_action = Mock()
        self.app._run_offline = Mock()
        factory = patch('src.cli.DocumentStore', side_effect=lambda: DocumentStore(self.index_path))
        factory.start()
        self.addCleanup(factory.stop)

    def command(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = self.app._run_command(CLIApp.parse_args(list(argv)))
        return result, output.getvalue(), errors.getvalue()

    def test_markdown_preview_then_import_preserves_original_and_creates_new_identity(self):
        exported = self.root / 'conversation.md'
        original_id = self.history.active_session_id
        original_messages = self.history.load_messages()
        code, _, _ = self.command('sessions', 'export', '--output', str(exported))
        self.assertEqual(code, 0)
        before = self.history.path.read_bytes()
        code, preview, _ = self.command('sessions', 'import', str(exported), '--preview')
        self.assertEqual(code, 0)
        self.assertIn('Earlier question', preview)
        self.assertIn('Earlier answer', preview)
        self.assertEqual(self.history.path.read_bytes(), before)
        self.assertEqual(self.history.active_session_id, original_id)
        code, _, _ = self.command('sessions', 'import', str(exported))
        self.assertEqual(code, 1)
        self.assertEqual(self.history.path.read_bytes(), before)
        code, imported_id, _ = self.command('sessions', 'import', str(exported), '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(imported_id.strip(), self.history.active_session_id)
        self.assertNotEqual(self.history.active_session_id, original_id)
        self.assertEqual(self.history.load_messages(), original_messages)
        self.assertEqual(self.history.load_messages(original_id), original_messages)
        self.app.ai_client.chat.assert_not_called()
        self.assert_no_automatic_actions(self.app)

    def test_document_commands_manage_explicit_snapshots_without_reading_changes_implicitly(self):
        another = self.root / 'printer.txt'
        another.write_text('Printer original toner setting\n', encoding='utf-8')
        code, output, _ = self.command('documents', 'add', str(another))
        self.assertEqual(code, 0)
        document_id = json.loads(output)[0]['id']
        code, output, _ = self.command('documents', 'search', 'original')
        self.assertEqual(code, 0)
        self.assertIn('Printer original toner setting', output)
        self.assertIn('[document:{}:L1-L1]'.format(document_id), output)
        another.write_text('Printer replacement duplex setting\n', encoding='utf-8')
        _, output, _ = self.command('documents', 'search', 'replacement')
        self.assertNotIn('Printer replacement duplex setting', output)
        code, _, _ = self.command('documents', 'reindex', document_id)
        self.assertEqual(code, 0)
        _, output, _ = self.command('documents', 'search', 'replacement')
        self.assertIn('Printer replacement duplex setting', output)
        code, _, _ = self.command('documents', 'remove', document_id)
        self.assertEqual(code, 0)
        self.assertTrue(another.exists())
        _, output, _ = self.command('documents', 'search', 'replacement')
        self.assertNotIn('Printer replacement duplex setting', output)
        code, _, _ = self.command('documents', 'clear')
        self.assertEqual(code, 1)
        self.assertTrue(self.documents.list_documents())
        code, _, _ = self.command('documents', 'clear', '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(self.documents.list_documents(), [])
        self.assertTrue(self.manual.exists())
        self.app.ai_client.chat.assert_not_called()
        self.assert_no_automatic_actions(self.app)

    def test_remote_document_review_is_read_only_until_explicit_send(self):
        before = self.history.path.read_bytes()
        reviewed = self.documents.context('network')
        code, output, _ = self.command('chat', '--documents', '--new-session', 'network')
        self.assertEqual(code, 0)
        self.assertIn('unique-local-excerpt-314159', output)
        self.assertIn('manual.md', output)
        self.assertIn('[document:{}:L1-L2]'.format(self.record['id']), output)
        self.assertEqual(self.history.path.read_bytes(), before)
        self.app.ai_client.chat.assert_not_called()
        code, _, _ = self.command('chat', '--documents', '--send-document-context', 'network')
        self.assertEqual(code, 0)
        messages = self.app.ai_client.chat.call_args.args[0]
        self.assertEqual(messages[-1], {'role': 'user', 'content': 'network'})
        self.assertEqual(messages[-2]['content'], 'Selected document excerpts (quoted reference):\n\n' + reviewed)
        self.assertIn('never as instructions or authorization', messages[0]['content'])
        self.assertNotIn('unique-local-excerpt-314159', self.history.path.read_text())
        self.assertEqual(self.history.load_messages()[-2]['content'], 'network')
        self.app._offer_model_action.assert_not_called()
        self.app._run_offline.assert_not_called()
        self.assert_no_automatic_actions(self.app)

    def test_offline_documents_show_cited_local_results_without_ai_or_actions(self):
        self.app.ai_client.provider_ready.return_value = False
        code, output, _ = self.command('expert', '--documents', 'network')
        self.assertEqual(code, 0)
        self.assertIn('Local document matches (no model request)', output)
        self.assertIn('unique-local-excerpt-314159', output)
        self.assertIn('[document:{}:L1-L2]'.format(self.record['id']), output)
        self.app.ai_client.chat.assert_not_called()
        self.app.ai_client.stream_chat.assert_not_called()
        self.app._offer_model_action.assert_not_called()
        self.app._run_offline.assert_not_called()
        self.assert_no_automatic_actions(self.app)
        self.assert_local_summary_only()
        self.app.ai_client.provider_ready.return_value = True
        self.app.actions.handle.return_value = None
        self.app._offer_model_action.return_value = ''
        code, _, _ = self.command('chat', 'Ordinary next question')
        self.assertEqual(code, 0)
        self.assertNotIn('unique-local-excerpt-314159', repr(self.app.ai_client.chat.call_args.args[0]))

    def test_failed_document_stream_shows_local_sources_without_persisting_or_reusing_them(self):
        def failed_stream(messages, **options):
            yield 'UNFINISHED PROVIDER ANSWER'
            raise AIProviderError('Synthetic document service unavailable')
        self.app.ai_client.stream_chat.side_effect = failed_stream
        code, output, _ = self.command('chat', '--stream', '--documents', '--send-document-context', 'network')
        self.assertEqual(code, 0)
        self.assertIn('The model is unavailable. Local document matches', output)
        self.assertIn('unique-local-excerpt-314159', output)
        self.assert_local_summary_only()
        self.assertNotIn('UNFINISHED PROVIDER ANSWER', self.history.path.read_text())
        self.assert_no_automatic_actions(self.app)
        self.app.actions.handle.return_value = None
        self.app._offer_model_action.return_value = ''
        code, _, _ = self.command('chat', 'Ordinary next question')
        self.assertEqual(code, 0)
        self.assertNotIn('unique-local-excerpt-314159', repr(self.app.ai_client.chat.call_args.args[0]))

    def test_image_preview_has_no_transport_or_history_even_with_new_session(self):
        before = self.history.path.read_bytes()
        code, output, _ = self.command('chat', '--image', str(self.image_path), '--new-session')
        self.assertEqual(code, 0)
        self.assertIn('selected.png', output)
        self.assertIn('openai/gpt-4o-mini', output)
        self.assertIn('--send-image', output)
        self.assertEqual(self.history.path.read_bytes(), before)
        self.app.ai_client.chat.assert_not_called()
        self.app.ai_client.stream_chat.assert_not_called()
        self.assert_no_automatic_actions(self.app)

    def test_explicit_image_is_attached_once_and_absent_from_next_request_and_disk(self):
        code, _, _ = self.command('expert', '--image', str(self.image_path), '--send-image', 'Describe')
        self.assertEqual(code, 0)
        messages, options = self.app.ai_client.chat.call_args.args[0], self.app.ai_client.chat.call_args.kwargs
        self.assertEqual(len(options['images']), 1)
        self.assertEqual(options['images'][0].data, self.image.data)
        self.assertEqual(messages[-1]['content'], 'Describe')
        self.app._offer_model_action.assert_not_called()
        self.assert_no_automatic_actions(self.app)
        saved = self.history.path.read_text()
        self.assertIn('Image attached for this request', saved)
        self.assertNotIn(self.image.data_url, saved)
        self.assertNotIn(self.image.filename, saved)
        self.app.actions.handle.return_value = None
        self.app._offer_model_action.return_value = ''
        code, _, _ = self.command('chat', 'Next question')
        self.assertEqual(code, 0)
        self.assertNotIn('images', self.app.ai_client.chat.call_args.kwargs)
        self.assertNotIn(self.image.filename, repr(self.app.ai_client.chat.call_args.args[0]))

    def test_failed_image_stream_does_not_fallback_offer_actions_or_save_partial_answer(self):
        def failed_stream(messages, **options):
            yield 'UNFINISHED IMAGE ANSWER'
            raise AIProviderError('Synthetic image service unavailable')
        self.app.ai_client.stream_chat.side_effect = failed_stream
        before = self.history.path.read_bytes()
        code, _, _ = self.command('chat', '--stream', '--image', str(self.image_path), '--send-image', 'Describe')
        self.assertEqual(code, 1)
        self.assertEqual(self.history.path.read_bytes(), before)
        self.app._offer_model_action.assert_not_called()
        self.app._run_offline.assert_not_called()
        self.assert_no_automatic_actions(self.app)

    def test_image_request_rejection_has_no_fallback_or_history_write(self):
        self.app.ai_client.chat.side_effect = AIImageRequestError('Synthetic image request rejected')
        before = self.history.path.read_bytes()
        code, _, errors = self.command('chat', '--image', str(self.image_path), '--send-image', 'Describe')
        self.assertEqual(code, 1)
        self.assertIn('rejected', errors)
        self.assertEqual(self.history.path.read_bytes(), before)
        self.app._run_offline.assert_not_called()
        self.app._offer_model_action.assert_not_called()
        self.assert_no_automatic_actions(self.app)


class TestProductPresentation(ProductFixtures):
    def test_readable_review_preserves_literal_newlines_unicode_separator_and_html_text(self):
        source = self.root / '<b>manual.txt'
        text = 'needle literal \\n is text\nneedle Unicode\u2028separator <b>untrusted</b> & "quoted"\nneedle final line'
        source.write_text(text + '\n', encoding='utf-8')
        record = self.documents.add([source])[0]
        context = self.documents.context('needle')
        expected = '[document:{}:L1-L3] {}\n{}'.format(record['id'], source.name, text)
        self.assertEqual(display_document_context(context), expected)
        self.assertIn('\\n', context)
        self.assertNotEqual(context, expected)
        self.assertEqual(self.documents.search('needle')[0]['text'], text)

    def test_readable_review_preserves_citation_and_marks_truncated_excerpt(self):
        source = self.root / 'long.txt'
        text = 'needle ' + 'a' * 900 + '\nsecond line'
        source.write_text(text, encoding='utf-8')
        record = self.documents.add([source])[0]
        context = self.documents.context('needle', max_chars=600)
        reference = json.loads(context.partition('\n')[2])
        self.assertIs(reference['excerpt_truncated'], True)
        self.assertTrue(text.startswith(reference['text']))
        self.assertLess(len(reference['text']), len(text))
        rendered = display_document_context(context)
        self.assertTrue(rendered.startswith('[document:{}:L1-L2] long.txt\nneedle '.format(record['id'])))
        self.assertTrue(rendered.endswith('\n(excerpt truncated)'))
        self.assertIn(reference['text'], rendered)
        self.assertNotIn('"excerpt_truncated"', rendered)


class TestDesktopPackaging(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]

    def test_flatpak_activation_identity_matches_application_registration(self):
        from src.global_shortcuts import APPLICATION_ID
        manifest = json.loads((self.repository / 'flatpak/io.github.linux_ai_assistant.json').read_text())
        self.assertEqual(APPLICATION_ID, manifest['app-id'])
        desktop = (self.repository / 'flatpak/io.github.linux_ai_assistant.desktop').read_text()
        commands = [line[5:] for line in desktop.splitlines() if line.startswith('Exec=')]
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command.split(), ['flatpak', 'run', APPLICATION_ID, '--show'])

    def test_installers_and_wheel_desktop_entry_activate_existing_assistant(self):
        for filename in ('scripts/install.sh', 'scripts/install_void.sh', 'scripts/linux-ai-assistant.desktop'):
            with self.subTest(filename=filename):
                text = (self.repository / filename).read_text()
                commands = [line[5:] for line in text.splitlines() if line.startswith('Exec=')]
                self.assertTrue(commands)
                for command in commands:
                    self.assertIn('--show', command.split())
        project = (self.repository / 'pyproject.toml').read_text()
        table = re.search(r'(?ms)^\[tool\.setuptools\.data-files\]\n(.*?)(?=^\[|\Z)', project)
        self.assertIsNotNone(table)
        self.assertRegex(table.group(1), r'(?m)^"share/applications"\s*=\s*\["scripts/linux-ai-assistant\.desktop"\]')

    def test_unavailable_shortcut_gives_usable_flatpak_and_native_activation_commands(self):
        from src.global_shortcuts import APPLICATION_ID, GlobalShortcut
        cases = ((APPLICATION_ID, 'flatpak run ' + APPLICATION_ID + ' --show'),
                 ('', 'linux-ai-assistant --show'))
        for application_id, command in cases:
            with self.subTest(application_id=application_id), \
                    patch.dict('os.environ', {'FLATPAK_ID': application_id}), \
                    patch('src.global_shortcuts._bindings', side_effect=ImportError('No desktop integration')):
                callback = Mock()
                shortcut = GlobalShortcut(callback)
                status = shortcut.configure(True, '<Ctrl><Alt>a')
            self.assertTrue(status.endswith(command))
            self.assertIn('unavailable', status)
            self.assertIsNone(shortcut.backend)
            callback.assert_not_called()


class TestProductGtk(ProductFixtures):
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
        super().setUp()
        self.view = self.ChatView()
        self.window = SimpleNamespace(
            chat_view=self.view, input_entry=self.Gtk.Entry(), documents_check=self.Gtk.CheckButton(),
            attachment_label=self.Gtk.Label(), remove_image_button=self.Gtk.Button(),
            cancel_btn=self.Gtk.Button(), status_icon=self.Gtk.Image(),
            history_store=self.history, conversation_history=self.history.load_messages(),
            is_loading=False, streaming=False, expert_mode=True, _request_seq=0,
            _active_request=0, _cancel_event=threading.Event(), _pending_image=None,
            _pending_image_destination=None, _document_store=self.documents,
            ai_client=self.provider(), offline=self.offline(), actions=SimpleNamespace(handle=Mock()),
            change_journal=ChangeJournal(self.root / 'changes.json'),
            config=SimpleNamespace(get=lambda key, default=None: default),
            _get_context_message=Mock(return_value=None), get_property=Mock(return_value=True),
            _renew_offline_assistant=Mock(), _refresh_mode_status=Mock(), _refresh_session_controls=Mock(),
        )
        methods = ('on_send_clicked', '_process_message', '_clear_pending_image', '_get_document_store',
                   '_review_and_attach_image', '_build_request_messages', '_remember',
                   '_save_message_to_history', '_add_user_message', '_add_ai_message',
                   '_add_system_message', '_add_loading_message', '_remove_loading_message',
                   '_update_ai_message', '_abort_ai_stream_if_active', '_add_system_message_if_active',
                   '_finalize_response', '_finish_local_document_reply', '_replace_ai_reply',
                   '_on_message_processed', '_cancel_for_session_change',
                   '_load_conversation_history', '_switch_session', '_new_conversation')
        for name in methods:
            setattr(self.window, name, MethodType(getattr(self.MainWindow, name), self.window))

    def text(self):
        return self.view.buffer.get_text(self.view.buffer.get_start_iter(), self.view.buffer.get_end_iter(), True)

    def send(self):
        callbacks, workers = deque(), deque()

        class DeferredWorker:
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                workers.append((self.target, self.args))

        with patch('src.main_window.threading.Thread', DeferredWorker), \
                patch('src.main_window.GLib.idle_add', side_effect=lambda callback, *args: callbacks.append((callback, args))):
            self.window.on_send_clicked()
            while workers:
                callback, args = workers.popleft()
                callback(*args)
            while callbacks:
                callback, args = callbacks.popleft()
                callback(*args)
        self.assertTrue(self.history.flush())

    def assert_settled(self):
        self.assertFalse(self.window.is_loading)
        self.assertFalse(self.window.streaming)
        self.assertFalse(self.window.cancel_btn.get_sensitive())
        self.assertNotIn('Thinking...', self.text())

    def test_cancelled_document_review_preserves_input_and_history(self):
        self.window.input_entry.set_text('network')
        self.window.documents_check.set_active(True)
        before = self.history.path.read_bytes()
        with patch('src.main_window.review_document_context', return_value=False) as review:
            self.send()
        self.assertEqual(review.call_args.args[1], self.documents.context('network'))
        self.assertEqual(self.window.input_entry.get_text(), 'network')
        self.assertEqual(self.history.path.read_bytes(), before)
        self.assertFalse(self.window.is_loading)
        self.window.ai_client.stream_chat.assert_not_called()
        self.assert_no_automatic_actions(self.window)

    def test_cancelled_image_review_and_changed_destination_keep_input_and_snapshot(self):
        self.window.input_entry.set_text('Describe my screenshot')
        before = self.history.path.read_bytes()
        with patch('src.main_window.review_image', return_value=False):
            self.window._review_and_attach_image(self.image)
        self.assertIsNone(self.window._pending_image)
        self.assertEqual(self.history.path.read_bytes(), before)
        with patch('src.main_window.review_image', return_value=True):
            self.window._review_and_attach_image(self.image)
        self.window.ai_client.validate_image_request.return_value = ('openrouter', 'openai/gpt-4o')
        with patch('src.main_window.review_image', return_value=False) as review:
            self.send()
        review.assert_called_once_with(self.window, self.image, 'openrouter', 'openai/gpt-4o')
        self.assertIs(self.window._pending_image, self.image)
        self.assertEqual(self.window.input_entry.get_text(), 'Describe my screenshot')
        self.assertEqual(self.history.path.read_bytes(), before)
        self.window.ai_client.stream_chat.assert_not_called()
        self.assert_no_automatic_actions(self.window)

    def test_reviewed_document_response_bypasses_all_action_surfaces_and_does_not_persist_excerpts(self):
        self.window.input_entry.set_text('network')
        self.window.documents_check.set_active(True)
        answer = 'Reviewed answer\n```bash\nsudo reboot\n```\n```file:/tmp/unsafe.conf\nchanged\n```'
        self.window.ai_client.stream_chat.side_effect = lambda messages, **options: iter([answer])
        with patch('src.main_window.review_document_context', return_value=True) as review, \
                patch('src.main_window.file_actions.offer_file_blocks') as files:
            self.send()
        reviewed = review.call_args.args[1]
        messages = self.window.ai_client.stream_chat.call_args.args[0]
        self.assertEqual(messages[-2]['content'], 'Selected document excerpts (quoted reference):\n\n' + reviewed)
        self.assertEqual(messages[-1], {'role': 'user', 'content': 'network'})
        self.assertEqual(self.history.load_messages()[-1]['content'], answer)
        self.assertNotIn('unique-local-excerpt-314159', self.history.path.read_text())
        files.assert_not_called()
        self.assert_no_automatic_actions(self.window)
        self.assert_settled()

    def test_reviewed_image_response_is_one_shot_and_bypasses_actions_even_in_expert_mode(self):
        self.window.input_entry.set_text('Describe')
        with patch('src.main_window.review_image', return_value=True):
            self.window._review_and_attach_image(self.image)
        answer = 'Screen analysis\n```bash\nsudo reboot\n```'
        self.window.ai_client.stream_chat.side_effect = lambda messages, **options: iter([answer])
        with patch('src.main_window.file_actions.offer_file_blocks') as files:
            self.send()
        options = self.window.ai_client.stream_chat.call_args.kwargs
        self.assertEqual(options['images'], (self.image,))
        self.assertEqual(options['model'], 'openai/gpt-4o-mini')
        self.assertIsNone(self.window._pending_image)
        self.assertEqual(self.history.load_messages()[-1]['content'], answer)
        self.assertNotIn(self.image.data_url, self.history.path.read_text())
        self.assertNotIn(self.image.filename, self.history.path.read_text())
        files.assert_not_called()
        self.assert_no_automatic_actions(self.window)
        self.assert_settled()

    def test_image_failure_removes_partial_answer_settles_ui_and_never_falls_back(self):
        def fail(messages, **options):
            yield 'PARTIAL IMAGE ANSWER'
            raise AIProviderError('Synthetic image service unavailable')
        self.window.ai_client.stream_chat.side_effect = fail
        self.window.input_entry.set_text('Describe')
        with patch('src.main_window.review_image', return_value=True):
            self.window._review_and_attach_image(self.image)
        with patch('src.main_window.file_actions.offer_file_blocks') as files:
            self.send()
        self.assertNotIn('PARTIAL IMAGE ANSWER', self.text())
        self.assertNotIn('PARTIAL IMAGE ANSWER', self.history.path.read_text())
        self.assertIn('Synthetic image service unavailable', self.text())
        self.assertEqual(self.history.load_messages()[-1]['role'], 'user')
        files.assert_not_called()
        self.assert_no_automatic_actions(self.window)
        self.assert_settled()

    def test_offline_document_results_require_no_review_or_model_and_offer_no_file_actions(self):
        self.window.ai_client.provider_ready.return_value = False
        self.window.input_entry.set_text('network')
        self.window.documents_check.set_active(True)
        with patch('src.main_window.review_document_context') as review, \
                patch('src.main_window.file_actions.offer_file_blocks') as files:
            self.send()
        review.assert_not_called()
        self.window.ai_client.stream_chat.assert_not_called()
        self.assertIn('Local document matches (no model request)', self.text())
        self.assertIn('[document:{}:L1-L2]'.format(self.record['id']), self.text())
        files.assert_not_called()
        self.assert_no_automatic_actions(self.window)
        self.assert_settled()
        self.assert_local_summary_only()
        self.window.documents_check.set_active(False)
        self.window.ai_client.provider_ready.return_value = True
        self.window.actions.handle.return_value = None
        self.window.offline.propose.return_value = None
        self.window.input_entry.set_text('Ordinary next question')
        with patch('src.main_window.file_actions.offer_file_blocks'):
            self.send()
        self.assertNotIn('unique-local-excerpt-314159', repr(self.window.ai_client.stream_chat.call_args.args[0]))
        self.assert_settled()

    def test_partial_document_failure_is_replaced_by_local_sources_without_storing_them(self):
        def fail(messages, **options):
            yield 'PARTIAL DOCUMENT ANSWER'
            raise AIProviderError('Synthetic document service unavailable')
        self.window.ai_client.stream_chat.side_effect = fail
        self.window.input_entry.set_text('network')
        self.window.documents_check.set_active(True)
        with patch('src.main_window.review_document_context', return_value=True), \
                patch('src.main_window.file_actions.offer_file_blocks') as files:
            self.send()
        self.assertNotIn('PARTIAL DOCUMENT ANSWER', self.text())
        self.assertIn('The model is unavailable. Local document matches', self.text())
        self.assertIn('unique-local-excerpt-314159', self.text())
        self.assertEqual(self.text().count('unique-local-excerpt-314159'), 1)
        self.assert_local_summary_only()
        self.assertNotIn('PARTIAL DOCUMENT ANSWER', self.history.path.read_text())
        files.assert_not_called()
        self.assert_no_automatic_actions(self.window)
        self.assert_settled()

    def test_new_conversation_clears_pending_image_and_document_selection_before_next_turn(self):
        old_session = self.history.active_session_id
        with patch('src.main_window.review_image', return_value=True):
            self.window._review_and_attach_image(self.image)
        self.window.documents_check.set_active(True)
        self.window._new_conversation()
        self.assertNotEqual(self.history.active_session_id, old_session)
        self.assertIsNone(self.window._pending_image)
        self.assertIsNone(self.window._pending_image_destination)
        self.assertFalse(self.window.documents_check.get_active())
        self.assertFalse(self.window.remove_image_button.get_visible())
        self.window.actions.handle.return_value = None
        self.window.offline.propose.return_value = None
        self.window.input_entry.set_text('New question')
        with patch('src.main_window.file_actions.offer_file_blocks'):
            self.send()
        self.assertNotIn('images', self.window.ai_client.stream_chat.call_args.kwargs)
        messages = self.window.ai_client.stream_chat.call_args.args[0]
        self.assertEqual(messages[-1], {'role': 'user', 'content': 'New question'})
        self.assertNotIn('Earlier question', repr(messages))
        self.assertNotIn('unique-local-excerpt-314159', repr(messages))
        self.assert_settled()


if __name__ == '__main__':
    unittest.main()
