"""Executable regressions for the second assessment, using harmless temp files."""

import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIClient, AIProviderError
from src.cli import CLIApp
from src.command_policy import validate_arguments
from src.config_manager import ConfigManager
from src.file_actions import _make_backup, preview_diff, MAX_DIFF_BYTES
from src.history_store import HistoryStore
from src.offline_assistant import DistroInfo, OfflineAssistant
from src.privileged_write import write_file
from src.process_output import run_bounded
from src.stream_events import iter_sse_json
from src.system_utils import SystemUtils


class Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class Response:
    def __init__(self, lines=(), data=None):
        self.lines = lines
        self.data = data
        self.closed = False

    def iter_lines(self):
        return iter(self.lines)

    def json(self):
        return self.data

    def close(self):
        self.closed = True


def save_history_batch(path, batch):
    HistoryStore.save_entries(path, batch)


class TestOperandPolicy(unittest.TestCase):
    def allowed(self, command):
        return validate_arguments(shlex.split(command), lambda path: path.startswith('/approved/'))

    def test_bare_relative_and_dash_prefixed_filenames_are_checked(self):
        for command in ('cat secret', 'cat ../secret', 'cat -- -secret',
                        'grep needle secret', 'head -n5 secret', 'ls --color secret'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_indirect_input_and_long_abbreviations_are_blocked(self):
        for command in ('grep --file=secret /approved/a', 'grep -ifsecret /approved/a',
                        'grep --fil=secret /approved/a', 'du --files0-fr=secret',
                        'grep --reg=needle secret', 'ip -batch secret', 'date --ref=secret'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_patterns_and_format_values_are_not_treated_as_paths(self):
        for command in ('grep -efoo /approved/a', 'grep -ie /etc/pattern /approved/a',
                        'grep /regex/ /approved/a', 'stat --format=%n /approved/a',
                        'ls --color=always /approved/a'):
            with self.subTest(command=command):
                self.assertTrue(self.allowed(command))

    def test_implicit_directory_reads_and_symlink_traversal_are_blocked(self):
        for command in ('ls', 'du', 'grep -r needle', 'grep -d recurse needle',
                        'grep --directories=recurse needle', 'ls -L /approved/a',
                        'du -L /approved/a'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_code_loading_commands_stay_blocked_with_manual_allowlist(self):
        for command in ('man --pager=sh ls', 'neofetch', 'less /approved/a'):
            self.assertFalse(self.allowed(command))

    def test_real_grep_preserves_regex_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'input'
            target.write_text('/etc/pattern\nfoo\n')
            utils = SystemUtils(Config({'permissions.allowed_commands': ['grep'],
                                        'permissions.allowed_edit_dirs': [directory]}))
            ok, output = utils.execute_command(['grep', '-e/etc/pattern', str(target)])
            self.assertTrue(ok, output)
            self.assertEqual(output, '/etc/pattern\n')

    def test_bundled_permissions_match_safe_defaults(self):
        bundled = json.loads((Path(__file__).resolve().parents[1] / 'config/config.json').read_text())
        self.assertEqual(bundled['permissions'], ConfigManager.DEFAULT_CONFIG['permissions'])


class TestBoundedDiagnostics(unittest.TestCase):
    def test_output_is_capped_before_accumulating_a_large_pipe(self):
        code, output, errors = run_bounded(
            [sys.executable, '-c', "import sys; sys.stdout.write('x'*2000000)"], 5, 8192)
        self.assertNotEqual(code, 0)
        self.assertIn('truncated at 8192 bytes', output)
        self.assertLess(len(output) + len(errors), 8300)

    def test_timeout_includes_a_child_holding_the_pipe(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_bounded([sys.executable, '-c',
                         "import os,time; os.fork(); time.sleep(10)"], .15, 100)
        self.assertLess(time.monotonic() - started, 2)

    def test_search_keeps_only_complete_nul_terminated_names(self):
        utils = SystemUtils(Config())
        with tempfile.TemporaryDirectory() as directory, patch(
                'src.system_utils.run_bounded', return_value=(1, '/complete\0/incomplete\n... truncated', '')):
            self.assertEqual(utils.search_files('x', directory), ['/complete'])

    def test_diff_caps_a_single_huge_line(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'large'
            target.write_bytes(b'x' * (3 * MAX_DIFF_BYTES))
            output = preview_diff(str(target), 'replacement\n')
            self.assertLess(len(output), MAX_DIFF_BYTES + 1000)
            self.assertIn('truncated', output)

    def test_file_reader_preserves_next_line_at_the_size_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'lines'
            target.write_text('x' * 10000 + '\nnext\n' + 'y' * 30000 + '\nlast\n')
            utils = SystemUtils(Config({'permissions.allowed_edit_dirs': [directory]}))
            ok, text = utils.read_file(str(target))
            self.assertTrue(ok, text)
            self.assertIn('\nnext\n', text)
            self.assertTrue(text.endswith('\nlast\n'))
            self.assertIn('line truncated', text)
            self.assertLess(len(text), 21000)


class TestExclusiveFileWrites(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'input'
        self.source.write_text('new contents')
        parent = self.root.stat()
        self.identity = (parent.st_dev, parent.st_ino)

    def test_new_destination_does_not_require_reference_metadata(self):
        target = self.root / 'new.conf'
        self.assertIsNone(write_file(str(self.source), str(target), None, self.identity))
        self.assertEqual(target.read_text(), 'new contents')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_existing_file_gets_backup_and_keeps_metadata(self):
        target = self.root / 'script'
        target.write_text('old contents')
        target.chmod(0o750)
        original = target.stat()
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        backup = write_file(str(self.source), str(target), digest, self.identity)
        self.assertEqual(Path(backup).read_text(), 'old contents')
        self.assertEqual(target.read_text(), 'new contents')
        self.assertEqual(target.stat().st_mode & 0o777, 0o750)
        self.assertEqual((target.stat().st_uid, target.stat().st_gid),
                         (original.st_uid, original.st_gid))

    def test_predictable_symlink_cannot_redirect_the_copy(self):
        outside = self.root / 'outside'
        outside.write_text('keep')
        target = self.root / 'target'
        (self.root / 'target.linux-ai.new').symlink_to(outside)
        write_file(str(self.source), str(target), None, self.identity)
        self.assertEqual(outside.read_text(), 'keep')
        self.assertTrue((self.root / 'target.linux-ai.new').is_symlink())

    def test_destination_symlink_is_rejected(self):
        target = self.root / 'link'
        target.symlink_to(self.source)
        with self.assertRaises(OSError):
            write_file(str(self.source), str(target), None, self.identity)

    def test_source_symlink_is_rejected(self):
        source = self.root / 'link'
        source.symlink_to(self.source)
        with self.assertRaises(OSError):
            write_file(str(source), str(self.root / 'target'), None, self.identity)

    def test_changed_contents_and_directory_are_rejected(self):
        target = self.root / 'target'
        target.write_text('changed')
        with self.assertRaises(PermissionError):
            write_file(str(self.source), str(target), hashlib.sha256(b'old').hexdigest(), self.identity)
        with self.assertRaises(PermissionError):
            write_file(str(self.source), str(target), None, (0, 0))
        self.assertEqual(target.read_text(), 'changed')

    def test_home_backups_created_in_same_second_remain_distinct(self):
        with patch('src.file_actions.time.strftime', return_value='same-second'):
            first = _make_backup(str(self.source))
            second = _make_backup(str(self.source))
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), Path(second).read_text())


class TestSharedJsonTransactions(unittest.TestCase):
    def test_multiple_processes_merge_without_lost_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'history.json')
            context = multiprocessing.get_context('spawn')
            processes = [context.Process(target=save_history_batch, args=(path, [
                {'role': 'user', 'content': str(i), 'timestamp': i}
                for i in range(batch * 25, (batch + 1) * 25)])) for batch in range(4)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(json.loads(Path(path).read_text())), 100)
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_gui_queue_and_cli_merge_share_the_same_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            store = HistoryStore(path)
            for number in range(60):
                store.append('user', 'gui-' + str(number), number)
            HistoryStore.save_entries(path, [{'role': 'assistant', 'content': 'cli', 'timestamp': 100}])
            self.assertTrue(store.close())
            self.assertEqual(len(json.loads(path.read_text())), 61)

    def test_writer_failure_is_reported_and_shutdown_does_not_hang(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                HistoryStore, 'save_entries', side_effect=OSError('disk full')):
            store = HistoryStore(Path(directory) / 'history.json')
            store.append('user', 'pending')
            self.assertFalse(store.close(2))
            self.assertFalse(store._writer.is_alive())
            self.assertIsInstance(store.last_error, OSError)

    def test_corrupt_history_is_preserved_before_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            path.write_text('{bad')
            HistoryStore.save_entries(path, [{'role': 'user', 'content': 'hello'}])
            backups = list(Path(directory).glob('history.json.corrupt-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), '{bad')
            self.assertEqual(json.loads(path.read_text())[0]['content'], 'hello')

    def test_same_entry_is_not_duplicated_when_cli_resaves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            batch = [{'role': 'user', 'content': 'same', 'timestamp': 1}]
            HistoryStore.save_entries(path, batch)
            HistoryStore.save_entries(path, batch)
            self.assertEqual(len(json.loads(path.read_text())), 1)


class TestProviderWireFormats(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.addCleanup(self.config.flush)
        with patch('src.ai_client.Path.home', return_value=Path(self.directory.name)):
            self.client = AIClient(self.config)
        self.addCleanup(self.client.session.close)
        self.addCleanup(self.client.flush_usage)

    def test_sse_optional_space_and_multiline_fields(self):
        response = Response([b': keepalive', b'event: message', b'data:{"choices":',
                             b'data: [{"delta":{"content":"ok"}}]}', b'', b'data: [DONE]'])
        self.assertEqual(list(AIClient._iter_sse_openai_style(response)), ['ok'])

    def test_sse_adjacent_events_without_blank_lines(self):
        response = Response([b'data:{"x":1}', b'data: {"x":2}', b'data:[DONE]'])
        self.assertEqual(list(iter_sse_json(response)), [{'x': 1}, {'x': 2}])

    def test_provider_error_events_are_failures_after_partial_text(self):
        response = Response([b'data:{"choices":[{"delta":{"content":"partial"}}]}', b'',
                             b'data:{"error":{"message":"unavailable"}}', b''])
        generator = AIClient._iter_sse_openai_style(response)
        self.assertEqual(next(generator), 'partial')
        with self.assertRaises(AIProviderError):
            next(generator)

    def test_empty_stream_raises_and_closes_the_response(self):
        response = Response([b'data:[DONE]'])
        self.client._make_request = Mock(return_value=response)
        with self.assertRaises(AIProviderError):
            list(self.client.stream_chat([{'role': 'user', 'content': 'hello'}], provider='local_llm'))
        self.assertTrue(response.closed)

    def test_malformed_stream_tail_is_not_saved_as_success(self):
        self.client._make_request = Mock(return_value=Response([
            b'data:{"choices":[{"delta":{"content":"partial"}}]}', b'', b'data:{invalid']))
        generator = self.client.stream_chat([{'role': 'user', 'content': 'hello'}], provider='local_llm')
        self.assertEqual(next(generator), 'partial')
        with self.assertRaises(AIProviderError):
            next(generator)

    def test_cumulative_stream_usage_is_not_added_twice(self):
        self.client._finish_stream_usage('anthropic', [
            {'input_tokens': 5, 'output_tokens': 1}, {'output_tokens': 3}], [], 'answer')
        self.assertEqual(self.client.get_token_usage('anthropic')['total'], 8)
        self.client._finish_stream_usage('google_ai_studio', [
            {'promptTokenCount': 7, 'candidatesTokenCount': 1},
            {'promptTokenCount': 7, 'candidatesTokenCount': 4}], [], 'answer')
        self.assertEqual(self.client.get_token_usage('google_ai_studio')['total'], 11)

    def test_local_backend_does_not_receive_stream_options_by_default(self):
        captured = []
        def request(url, payload, *args, **kwargs):
            captured.append(payload)
            return Response([b'data:{"choices":[{"delta":{"content":"ok"}}]}', b'', b'data:[DONE]'])
        self.client._make_request = request
        messages = [{'role': 'user', 'content': 'hi'}]
        self.assertEqual(list(self.client.stream_chat(messages, provider='local_llm')), ['ok'])
        self.assertNotIn('stream_options', captured[-1])
        self.config.set('api.providers.local_llm.stream_include_usage', True)
        list(self.client.stream_chat(messages, provider='local_llm'))
        self.assertEqual(captured[-1]['stream_options'], {'include_usage': True})

    def test_cohere_v1_ndjson_and_final_usage(self):
        events = [
            {'event_type': 'stream-start'}, {'event_type': 'text-generation', 'text': 'hello'},
            {'event_type': 'stream-end', 'finish_reason': 'COMPLETE',
             'response': {'meta': {'billed_units': {'input_tokens': 5, 'output_tokens': 2}}}},
        ]
        usage = []
        self.assertEqual(list(AIClient._iter_sse_cohere(Response(
            [json.dumps(event).encode() for event in events]), usage)), ['hello'])
        self.assertEqual(usage, [{'input_tokens': 5, 'output_tokens': 2}])

    def test_cohere_nonstream_text_and_chatbot_history(self):
        response = Response(data={'text': 'answer', 'meta': {
            'billed_units': {'input_tokens': 4, 'output_tokens': 2}}})
        self.client._make_request = Mock(return_value=response)
        messages = [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'reply'},
                    {'role': 'user', 'content': 'second'}]
        result = self.client._chat_cohere(messages, 'model', 'key', 'https://example.invalid/v1', .7, 100, 1)
        self.assertEqual(result, 'answer')
        payload = self.client._make_request.call_args.args[1]
        self.assertEqual(payload['chat_history'][1]['role'], 'CHATBOT')
        self.assertEqual(payload['message'], 'second')
        self.assertTrue(response.closed)
        self.assertEqual(self.client.get_token_usage('cohere')['total'], 6)

    def test_two_stale_clients_merge_usage_deltas(self):
        with patch('src.ai_client.Path.home', return_value=Path(self.directory.name)):
            other = AIClient(self.config)
        try:
            self.client._update_token_usage('groq', 7, 3)
            other._update_token_usage('groq', 11, 2)
            threads = [threading.Thread(target=client.flush_usage) for client in (self.client, other)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            data = json.loads(self.client._usage_path.read_text())
            self.assertEqual(data['groq']['total'], 23)
            self.client.flush_usage()
            self.assertEqual(json.loads(self.client._usage_path.read_text())['groq']['total'], 23)
        finally:
            other.flush_usage()
            other.session.close()

    def test_usage_delta_is_retained_if_disk_write_fails(self):
        self.client._update_token_usage('groq', 3, 1)
        with patch('src.ai_client.update_json', side_effect=OSError('disk full')):
            self.client.flush_usage()
        self.assertTrue(self.client._usage_dirty)
        self.client.flush_usage()
        self.assertEqual(json.loads(self.client._usage_path.read_text())['groq']['total'], 4)

    def test_malformed_existing_usage_does_not_block_new_totals(self):
        self.client._usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.client._usage_path.write_text(json.dumps({'groq': {'input': 'bad', 'output': None, 'total': []}}))
        self.client._update_token_usage('groq', 3, 1)
        self.client.flush_usage()
        self.assertEqual(json.loads(self.client._usage_path.read_text())['groq'],
                         {'input': 3, 'output': 1, 'total': 4})


class TestOfflineCorrections(unittest.TestCase):
    def assistant(self):
        return OfflineAssistant(os_release={'ID': 'ubuntu', 'PRETTY_NAME': 'Ubuntu'},
                                which=lambda name: None, is_systemd_running=False)

    def test_uninstall_never_becomes_install(self):
        for text, lang in (('uninstall htop', 'en'), ('desinstalar htop', 'pt'),
                           ('desinstalar htop', 'es'), ('désinstaller htop', 'fr'),
                           ('deinstallieren htop', 'de')):
            reply = self.assistant().handle(text, lang)
            self.assertEqual(reply.commands[0].argv, ['apt-get', 'remove', '-y', 'htop'])

    def test_service_actions_work_in_all_catalog_languages(self):
        for text, lang in (('disable service nginx', 'en'), ('desativar serviço nginx', 'pt'),
                           ('desactivar servicio nginx', 'es'), ('désactiver service nginx', 'fr'),
                           ('deaktivieren Dienst nginx', 'de')):
            self.assertEqual(self.assistant().handle(text, lang).commands[0].argv,
                             ['systemctl', 'disable', '--now', 'nginx'])

    def test_portuguese_configuration_question_keeps_its_intent(self):
        self.assertIn('/etc/netplan', self.assistant().handle('ficheiros de configuração?', 'pt').text)

    def test_keywords_at_punctuation_boundaries_are_recognized(self):
        self.assertIn('docs.voidlinux.org', OfflineAssistant(
            os_release={'ID': 'void'}, which=lambda name: None,
            is_systemd_running=False).handle('wiki?', 'en').text)
        self.assertIn('repos', self.assistant().handle('repos?', 'en').text.lower())

    def test_arbitrary_words_do_not_trigger_package_actions(self):
        for text in ('reinstalling htop', 'preinstall htop', 'the cachet is interesting'):
            self.assertEqual(self.assistant().handle(text).commands, [])

    def test_expert_cli_falls_back_for_a_configured_but_failed_provider(self):
        app = CLIApp.__new__(CLIApp)
        app.conversation_history = []
        app._build_request_messages = Mock(return_value=[{'role': 'user', 'content': 'question'}])
        app.ai_client = SimpleNamespace(chat=Mock(return_value=None), provider_ready=Mock(return_value=True))
        app._run_offline = Mock(return_value='offline answer')
        app._record_exchange = Mock()
        app._save_history = Mock()
        args = SimpleNamespace(message=['question'], provider='openrouter', model=None)
        with patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(app.handle_expert(args), 0)
        app._run_offline.assert_called_once_with('question')
        app._record_exchange.assert_called_once_with('question', 'offline answer')
        app._save_history.assert_called_once()

    def test_cli_initializes_the_configured_language(self):
        with patch('src.cli.ConfigManager', return_value=Config({'app.language': 'pt'})), \
                patch('src.cli.AIClient'), patch('src.cli.SystemUtils'), \
                patch('src.cli.offline_assistant.OfflineAssistant') as offline, \
                patch.object(CLIApp, '_load_history', return_value=[]), \
                patch('src.cli.set_language_from_config') as initialize:
            offline.return_value.distro = DistroInfo()
            app = CLIApp()
            self.addCleanup(app.actions.close)
            self.addCleanup(app.history_store.close)
            initialize.assert_called_once_with(app.config)


if __name__ == '__main__':
    unittest.main()
