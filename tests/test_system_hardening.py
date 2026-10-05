"""Real regressions for memory fallbacks, log privacy and bounded execution."""

import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from src import file_actions
from src.device_actions import run_argv
from src.log_privacy import redact_command, redact_text
from src.package_actions import _run as package_run
from src.process_output import run_bounded
from src.system_utils import SystemUtils


class Config:
    def get(self, key, default=None):
        if key == 'permissions.allowed_commands':
            return ['fixture']
        return default


class TestSystemInformation(unittest.TestCase):
    def info(self, memory):
        payloads = {
            '/etc/os-release': '\ufeffID=debian\nPRETTY_NAME="Distribuição de ensaio"\n',
            '/proc/meminfo': memory,
            '/proc/cpuinfo': 'processor : 0\n',
            '/proc/uptime': '1234.50 0.00\n',
        }
        def read(path, mode='r', **kwargs):
            self.assertEqual(mode, 'r')
            expected = 'utf-8-sig' if path == '/etc/os-release' else 'utf-8'
            self.assertEqual(kwargs['encoding'], expected)
            return io.TextIOWrapper(io.BytesIO(payloads[path].encode('utf-8')), encoding=expected)
        with patch.dict(sys.modules, {'psutil': None}), patch('builtins.open', side_effect=read), \
                patch('src.system_utils.platform.architecture', return_value=('64bit', '')):
            return SystemUtils(Config()).get_system_info()

    def test_mem_available_overrides_mem_free_independently_of_order(self):
        for fields in ('MemAvailable: 4194304 kB\nMemFree: 131072 kB\n',
                       'MemFree: 131072 kB\nMemAvailable: 4194304 kB\n'):
            info = self.info('MemTotal: 8388608 kB\n' + fields)
            self.assertEqual(info['memory_available'], '4.00 GB')
            self.assertEqual(info['memory_total'], '8.00 GB')
            self.assertEqual(info['distro_id'], 'debian')
            self.assertEqual(info['distro'], 'Distribuição de ensaio')

    def test_old_kernels_without_mem_available_retain_free_fallback(self):
        self.assertEqual(self.info('MemFree: 131072 kB\n')['memory_available'], '0.12 GB')

    def test_zero_available_is_not_replaced_with_free(self):
        self.assertEqual(self.info('MemAvailable: 0 kB\nMemFree: 131072 kB\n')['memory_available'], '0.00 GB')

    def test_active_window_discovery_does_not_spawn_which(self):
        utils = SystemUtils(Config())
        utils.is_wayland = False
        with patch.object(utils, '_which', side_effect=lambda name: '/fixture/' + name), \
                patch('src.system_utils.run_bounded', return_value=(0, 'fixture output', '')) as run:
            self.assertEqual(utils.get_active_window_info()['title'], 'fixture output')
        self.assertEqual([call.args[0][0] for call in run.call_args_list],
                         ['xdotool', 'xdotool', 'xdotool', 'xprop'])
        self.assertTrue(all(call.args[1:] == (5, 1_000_000) for call in run.call_args_list))


class TestLogPrivacy(unittest.TestCase):
    def test_legacy_unquoted_credentials_hide_the_ambiguous_line_tail(self):
        for label in ('--password ', 'password=', 'api_key: ', '--passphrase '):
            clean = redact_text('fixture ' + label + 'correct horse battery staple --verbose\nnext line')
            for part in ('correct', 'horse', 'battery', 'staple', '--verbose'):
                self.assertNotIn(part, clean)
            self.assertIn('next line', clean)

    def test_quoted_credential_preserves_nonsecret_arguments(self):
        clean = redact_text(r'fixture --password "correct \"horse\" battery" --verbose')
        self.assertNotIn('horse', clean)
        self.assertIn('--verbose', clean)

    def test_structured_argv_preserves_boundaries_and_hides_complete_values(self):
        value = json.loads(redact_command([
            'fixture', '--password', 'correct horse battery staple', '--api-key=test-key',
            '--passphrase', 'test phrase', 'argument with spaces', 'https://person:pass@example.org/',
        ]))
        self.assertEqual(value[2], '[redacted]')
        self.assertEqual(value[3], '--api-key=[redacted]')
        self.assertEqual(value[5], '[redacted]')
        self.assertEqual(value[6], 'argument with spaces')
        self.assertNotIn('person:pass', value[7])

    def test_system_logger_does_not_receive_password_or_malformed_input(self):
        utils = SystemUtils(Config())
        with patch.object(utils, '_validate_file_args', return_value=True), \
                patch('src.system_utils.run_bounded', return_value=(0, 'ok', '')), \
                self.assertLogs('src.system_utils', level='INFO') as logs:
            self.assertEqual(utils.execute_command(
                ['fixture', '--password', 'correct horse battery staple']), (True, 'ok'))
            self.assertFalse(utils.execute_command('fixture --password "correct horse battery staple')[0])
        for part in ('correct', 'horse', 'battery', 'staple'):
            self.assertNotIn(part, '\n'.join(logs.output))

    def test_cookie_and_attached_http_credentials_are_hidden(self):
        for arguments in (['curl', '--cookie', 'session=correct horse battery staple'],
                          ['curl', '-b', 'session=correct horse battery staple'],
                          ['curl', '-bsession=correct horse battery staple'],
                          ['curl', '--user=person:correct horse battery staple'],
                          ['curl', '-uperson:correct horse battery staple']):
            value = redact_command(arguments)
            for part in ('person', 'correct', 'horse', 'battery', 'staple'):
                self.assertNotIn(part, value)

    def test_diagnostic_prose_and_token_metrics_survive_redaction(self):
        for text in ('No password required; connection refused',
                     'Token usage - openrouter: input=100, output=20'):
            self.assertEqual(redact_text(text), text)
        text = 'Token usage - input=100; password=correct horse battery staple'
        self.assertIn('input=100', redact_text(text))
        self.assertNotIn('horse', redact_text(text))

    def test_legacy_bare_credentials_are_recognized_in_command_logs(self):
        clean = redact_text('INFO Running command: fixture password correct horse battery staple')
        self.assertNotIn('horse', clean)
        self.assertIn('[redacted]', clean)

    def test_error_logging_cannot_expose_the_original_argv(self):
        utils = SystemUtils(Config())
        with patch.object(utils, '_validate_file_args', return_value=True), \
                patch('src.system_utils.run_bounded', side_effect=RuntimeError('correct horse battery staple')), \
                self.assertLogs('src.system_utils', level='ERROR') as logs:
            ok, error = utils.execute_command(['fixture', '--password', 'correct horse battery staple'])
        self.assertFalse(ok)
        self.assertNotIn('horse', error + '\n'.join(logs.output))


class TestBoundedExecution(unittest.TestCase):
    def setUp(self):
        helper = patch('src.file_actions.file_helper_command', return_value=[
            '/usr/bin/pkexec', '/usr/libexec/linux-ai-files'])
        helper.start()
        self.addCleanup(helper.stop)

    def test_environment_and_closed_stdin_reach_a_real_child(self):
        env = dict(os.environ, FIXTURE_PROCESS_VALUE='fixture-value')
        code, output, errors = run_bounded(
            [sys.executable, '-c', 'import os,sys; print(os.environ["FIXTURE_PROCESS_VALUE"]); print(repr(sys.stdin.read()))'],
            3, 4096, env=env)
        self.assertEqual(code, 0, errors)
        self.assertEqual(output, "fixture-value\n''\n")

    def test_success_cleans_a_descendant_that_closed_the_capture_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'orphan-effect'
            code = ('import os,time,pathlib,sys\n'
                    'if os.fork() == 0:\n'
                    ' os.close(1); os.close(2); time.sleep(.3)\n'
                    ' pathlib.Path(sys.argv[1]).write_text("unexpected")\n'
                    'else:\n'
                    ' time.sleep(.03)\n')
            self.assertEqual(run_bounded([sys.executable, '-c', code, str(target)], 3, 4096)[0], 0)
            time.sleep(.4)
            self.assertFalse(target.exists())

    def test_package_runner_rejects_large_output_without_staging_it_on_disk(self):
        ok, text = package_run([sys.executable, '-c', 'import sys; sys.stdout.write("x"*2000000)'], 5)
        self.assertFalse(ok)
        self.assertIn('size limit', text)

    def test_device_truncation_does_not_return_a_partial_secret(self):
        with patch('src.device_actions.run_bounded', return_value=(
                1, 'partial-secret\n... (output truncated at 1000000 bytes)', '')):
            ok, output = run_argv(['fixture'], secret='partial-secret-with-a-long-tail')
        self.assertFalse(ok)
        self.assertNotIn('partial-secret', output)

    def test_privileged_helpers_keep_metadata_and_use_the_authentication_deadline(self):
        with patch('src.file_actions.run_bounded', return_value=(
                0, '{"backup":"fixture-backup","published":true}', '')) as run, \
                patch('src.file_actions.file_helper_command', return_value=[
                    '/usr/bin/pkexec', '/usr/libexec/linux-ai-files']):
            self.assertEqual(file_actions._write_privileged(
                'fixture-source', '/fixture/target', 'digest', (1, 2)), 'fixture-backup')
            self.assertEqual(file_actions._remove_privileged('/fixture/target', 'digest', (1, 2)), 'fixture-backup')
            self.assertEqual(file_actions._inspect_privileged('/fixture/target', 'digest', (1, 2)),
                             {'backup': 'fixture-backup', 'published': True})
        self.assertTrue(all(call.args[0][:2] == ['/usr/bin/pkexec', '/usr/libexec/linux-ai-files']
                            for call in run.call_args_list))
        self.assertEqual([call.args[1:] for call in run.call_args_list],
                         [(120, 1024 * 1024), (120, 1024 * 1024),
                          (120, file_actions.MAX_PRIVILEGED_PREVIEW_BYTES)])

    def test_privileged_preview_preserves_maximum_content_inside_escaped_json(self):
        def fixture(argv, timeout, limit):
            script = ('import json,sys; print(json.dumps({"content":'
                      'chr(int(sys.argv[1]))*1048576,"truncated":False}))')
            return run_bounded([sys.executable, '-c', script, str(codepoint)], timeout, limit)
        for codepoint in (ord('x'), ord('\ufffd')):
            with self.subTest(codepoint=codepoint), \
                    patch('src.file_actions.run_bounded', side_effect=fixture):
                preview = file_actions._inspect_privileged('/fixture/target', 'digest', (1, 2))
            self.assertEqual(preview['content'], chr(codepoint) * (1024 * 1024))
            self.assertFalse(preview['truncated'])

    def test_privileged_timeout_reports_uncertain_target_state(self):
        with patch('src.file_actions.run_bounded', side_effect=subprocess.TimeoutExpired(['pkexec'], 120)):
            with self.assertRaisesRegex(PermissionError, 'Check the target state'):
                file_actions._inspect_privileged('/fixture/target', 'digest', (1, 2))

    def test_timeout_stays_bounded_when_process_group_signalling_is_denied(self):
        children = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        try:
            with patch('src.process_output.subprocess.Popen', side_effect=spawn), \
                    patch('src.process_output.os.killpg', side_effect=PermissionError('fixture denied')):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_bounded([sys.executable, '-c', 'import time; time.sleep(10)'], .1, 100)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
