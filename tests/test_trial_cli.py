"""CLI collection is explicit and never silently approves evidence export."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from src.cli import CLIApp
from src.config_manager import ConfigManager
from src.history_store import HistoryStore
from src.offline_assistant import DistroInfo
from src.trial_recorder import TrialRecorder


class TestTrialCLI(unittest.TestCase):
    def setUp(self):
        self.app = CLIApp.__new__(CLIApp)
        self.store = SimpleNamespace(active_session_id='session-active')
        self.app._store = Mock(return_value=self.store)
        self.recorder = Mock(spec=(
            'start', 'current', 'list_runs', 'select', 'begin_case',
            'end_case', 'finish', 'preview', 'attach', 'exclude_attachment', 'export',
        ))
        self.recorder.start.return_value = {'id': 'run-123'}
        self.recorder.current.return_value = {'id': 'run-123', 'cases': []}
        self.recorder.list_runs.return_value = [{'id': 'run-123'}]
        self.recorder.select.return_value = {'id': 'run-123'}
        self.recorder.begin_case.return_value = {'case_id': 'CORE-01'}
        self.recorder.end_case.return_value = {'result': 'PASS'}
        self.recorder.finish.return_value = {'id': 'run-123', 'status': 'finished'}
        self.recorder.preview.return_value = '# Reviewed evidence\nNo secrets.\n'
        self.recorder.attach.return_value = {'id': 'attachment-1', 'kind': 'text'}
        self.app._trial_recorder = Mock(return_value=self.recorder)

    def run_cli(self, *args):
        output = io.StringIO()
        errors = io.StringIO()
        with patch('sys.argv', ['linux-ai', 'trials', *args]), \
                patch('sys.stdout', output), patch('sys.stderr', errors):
            code = self.app._run_command()
        return code, output.getvalue(), errors.getvalue()

    def test_start_records_explicit_environment_and_build_without_ai_metadata(self):
        code, output, errors = self.run_cli(
            'start', '--title', 'Offline campaign', '--environment', 'VOID-X11-01',
            '--build', '66441af', '--type', 'physical',
        )
        self.assertEqual((code, output.strip(), errors), (0, 'run-123', ''))
        self.recorder.start.assert_called_once_with(
            'Offline campaign', 'VOID-X11-01', build_ref='66441af', test_type='physical',
        )

    def test_start_does_not_fabricate_a_commit_or_environment_type(self):
        code, _output, _errors = self.run_cli(
            'start', '--title', 'Smoke test', '--environment', 'ENV-02',
        )
        self.assertEqual(code, 0)
        self.recorder.start.assert_called_once_with(
            'Smoke test', 'ENV-02', build_ref='', test_type='unknown',
        )

    def test_start_requires_title_and_environment(self):
        for args in (('start',), ('start', '--title', 'Campaign'),
                     ('start', '--environment', 'ENV-01')):
            with self.subTest(args=args), patch('sys.argv', ['linux-ai', 'trials', *args]), \
                    patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.app.parse_args()
                self.assertEqual(error.exception.code, 2)
        self.app._trial_recorder.assert_not_called()

    def test_list_status_and_use_keep_structured_backend_results(self):
        for command, method, expected in (
            (('status',), self.recorder.current, {'id': 'run-123', 'cases': []}),
            (('list',), self.recorder.list_runs, [{'id': 'run-123'}]),
            (('use', 'run-123'), self.recorder.select, {'id': 'run-123'}),
        ):
            with self.subTest(command=command):
                code, output, errors = self.run_cli(*command)
                self.assertEqual(code, 0)
                self.assertEqual(errors, '')
                self.assertEqual(json.loads(output), expected)
                method.assert_called_once_with(*command[1:])

    def test_begin_uses_the_active_session_and_never_collects_logs(self):
        code, output, _errors = self.run_cli('begin', 'CORE-01')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['case_id'], 'CORE-01')
        self.recorder.begin_case.assert_called_once_with(
            'CORE-01', 'session-active', variant='', interface='cli', notes='',
        )
        self.recorder.end_case.assert_not_called()

    def test_begin_explicit_session_is_bound_without_selecting_a_conversation(self):
        code, _output, _errors = self.run_cli(
            'begin', 'MON-04', '--session', 'session-selected',
            '--variant', 'repeat-2', '--notes', 'Two monitors',
        )
        self.assertEqual(code, 0)
        self.recorder.begin_case.assert_called_once_with(
            'MON-04', 'session-selected', variant='repeat-2', interface='cli',
            notes='Two monitors',
        )
        self.assertEqual(self.store.active_session_id, 'session-active')

    def test_end_does_not_enable_logs_or_select_an_operation_implicitly(self):
        code, _output, _errors = self.run_cli('end', '--result', 'PASS')
        self.assertEqual(code, 0)
        self.recorder.end_case.assert_called_once_with(
            'PASS', notes='', operation_id=None, collect_logs=False,
        )

    def test_end_uses_explicit_operation_and_log_opt_in(self):
        code, _output, _errors = self.run_cli(
            'end', '--result', 'FAIL', '--notes', 'Unexpected rollback',
            '--operation', 'operation-8', '--logs',
        )
        self.assertEqual(code, 0)
        self.recorder.end_case.assert_called_once_with(
            'FAIL', notes='Unexpected rollback', operation_id='operation-8',
            collect_logs=True,
        )

    def test_end_accepts_protocol_outcomes_and_rejects_unrecognized_results(self):
        for result in ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'N/A'):
            with self.subTest(result=result):
                code, _output, _errors = self.run_cli('end', '--result', result)
                self.assertEqual(code, 0)
                self.assertEqual(self.recorder.end_case.call_args.args, (result,))
        for args in (('end',), ('end', '--result', 'success')):
            with self.subTest(args=args), patch('sys.argv', ['linux-ai', 'trials', *args]), \
                    patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.app.parse_args()
                self.assertEqual(error.exception.code, 2)

    def test_preview_prints_the_complete_backend_content(self):
        code, output, _errors = self.run_cli('preview')
        self.assertEqual(code, 0)
        self.assertEqual(output, self.recorder.preview.return_value + '\n')
        self.recorder.export.assert_not_called()

    def test_preview_file_is_private_and_cannot_replace_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preview.md'
            code, output, errors = self.run_cli('preview', '--output', str(path))
            self.assertEqual((code, output.strip(), errors), (0, str(path), ''))
            self.assertEqual(path.read_text(encoding='utf-8'), self.recorder.preview.return_value)
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.recorder.preview.return_value = 'New content'
            code, _output, errors = self.run_cli('preview', '--output', str(path))
            self.assertEqual(code, 1)
            self.assertIn('Cannot record or export', errors)
            self.assertEqual(path.read_text(encoding='utf-8'), '# Reviewed evidence\nNo secrets.\n')

    def test_attachment_and_exclusion_remain_explicit(self):
        code, output, _errors = self.run_cli('attach', 'selected-note.txt')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['id'], 'attachment-1')
        self.recorder.attach.assert_called_once_with(Path('selected-note.txt'))
        code, output, _errors = self.run_cli('exclude', 'attachment-1')
        self.assertEqual((code, output.strip()), (0, 'attachment-1'))
        self.recorder.exclude_attachment.assert_called_once_with('attachment-1')

    def test_finish_does_not_export_or_mark_a_case_as_success(self):
        code, output, _errors = self.run_cli('finish')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['status'], 'finished')
        self.recorder.finish.assert_called_once_with()
        self.recorder.end_case.assert_not_called()
        self.recorder.export.assert_not_called()

    def test_export_requires_a_separate_explicit_review_flag(self):
        code, output, errors = self.run_cli('export', '--output', 'results.zip')
        self.assertEqual((code, output), (1, ''))
        self.assertIn('trials preview', errors)
        self.assertIn('--reviewed is required', errors)
        self.recorder.export.assert_not_called()
        self.recorder.preview.assert_not_called()

    def test_export_passes_review_and_destination_without_finishing_the_run(self):
        self.recorder.export.return_value = Path('results.zip')
        code, output, errors = self.run_cli(
            'export', '--output', 'results.zip', '--reviewed',
        )
        self.assertEqual((code, output.strip(), errors), (0, 'results.zip', ''))
        self.recorder.export.assert_called_once_with(Path('results.zip'), reviewed=True)
        self.recorder.finish.assert_not_called()

    def test_collection_and_export_errors_reach_the_shell(self):
        for exception in (ValueError('No active run'), OSError('Cannot save'),
                          KeyError('Unknown case')):
            with self.subTest(exception=exception):
                self.recorder.begin_case.side_effect = exception
                code, output, errors = self.run_cli('begin', 'CORE-01')
                self.assertEqual((code, output), (1, ''))
                self.assertIn('Cannot record or export the test run', errors)
        self.recorder.export.side_effect = FileExistsError('Destination already exists')
        code, _output, errors = self.run_cli('export', '--output', 'results.zip', '--reviewed')
        self.assertEqual(code, 1)
        self.assertIn('Destination already exists', errors)

    def test_recorder_setup_errors_do_not_fall_through_as_success(self):
        self.app._trial_recorder.side_effect = OSError('Private test storage is unavailable')
        code, output, errors = self.run_cli('status')
        self.assertEqual((code, output), (1, ''))
        self.assertIn('Private test storage is unavailable', errors)


class TestTrialCLIIntegration(unittest.TestCase):
    """Real CLI/backend connection, restricted to a private temporary fixture."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'trials'
        self.app = CLIApp.__new__(CLIApp)
        self.app.config = ConfigManager(str(self.base / 'config.json'))
        self.addCleanup(self.app.config.flush)
        self.app.config.set_assistance_mode('offline')
        self.app.config.set('api.providers.openrouter.api_key', 'NEVER-EXPORT-TEST-SECRET')
        self.store = HistoryStore(self.base / 'history.json')
        self.store.list_sessions()
        self.addCleanup(self.store.close)
        self.app._store = Mock(return_value=self.store)
        self.app.offline = SimpleNamespace(distro=DistroInfo(distro_id='void', pkg_manager='xbps'))
        self.initialize_recorder = TrialRecorder.__init__

    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()

        def initialize_local_recorder(recorder, **kwargs):
            self.initialize_recorder(recorder, self.root, log_path=self.base / 'app.log', **kwargs)

        with patch('sys.argv', ['linux-ai', 'trials', *args]), \
                patch('sys.stdout', output), patch('sys.stderr', errors), \
                patch('src.trial_recorder.TrialRecorder.__init__', initialize_local_recorder), \
                patch('subprocess.run', side_effect=AssertionError('Unexpected system command')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
            code = self.app._run_command()
        return code, output.getvalue(), errors.getvalue()

    def test_real_cli_records_reviews_and_exports_case_without_running_actions(self):
        code, output, errors = self.run_cli(
            'start', '--title', 'CLI fixture', '--environment', 'FIXTURE-01',
            '--build', 'fixture-build', '--type', 'fixture',
        )
        self.assertEqual((code, errors), (0, ''))
        run_id = output.strip()
        self.assertTrue(run_id.startswith('run-'))
        code, output, errors = self.run_cli('begin', 'CORE-01', '--notes', 'Controlled fixture')
        self.assertEqual((code, errors), (0, ''))
        case = json.loads(output)
        self.assertEqual(case['session_id'], self.store.active_session_id)
        self.assertEqual(case['interface'], 'cli')
        code, output, errors = self.run_cli(
            'end', '--result', 'PASS', '--notes', 'Independent fixture verification',
        )
        self.assertEqual((code, errors), (0, ''))
        self.assertIsNone(json.loads(output)['log_excerpt'])
        code, output, errors = self.run_cli('finish')
        self.assertEqual((code, errors), (0, ''))
        self.assertIsNotNone(json.loads(output)['finished_at'])

        destination = self.base / 'evidence.zip'
        code, _output, errors = self.run_cli('export', '--output', str(destination), '--reviewed')
        self.assertEqual(code, 1)
        self.assertIn('review is missing or outdated', errors)
        self.assertFalse(destination.exists())

        preview = self.base / 'preview.md'
        code, output, errors = self.run_cli('preview', '--output', str(preview))
        self.assertEqual((code, output.strip(), errors), (0, str(preview), ''))
        self.assertIn('FILE: trial.json', preview.read_text(encoding='utf-8'))
        self.assertIn('Independent fixture verification', preview.read_text(encoding='utf-8'))
        self.assertNotIn('NEVER-EXPORT-TEST-SECRET', preview.read_text(encoding='utf-8'))
        code, output, errors = self.run_cli('export', '--output', str(destination), '--reviewed')
        self.assertEqual((code, output.strip(), errors), (0, str(destination), ''))
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json', 'report.md', 'results.csv', 'trial.json'})
            trial = json.loads(archive.read('trial.json'))
            self.assertEqual(trial['id'], run_id)
            self.assertEqual(trial['mode'], 'offline')
            self.assertEqual(trial['test_type'], 'fixture')
            self.assertEqual(trial['build']['reference'], 'fixture-build')
            self.assertEqual(trial['cases'][0]['result'], 'PASS')
            self.assertNotIn('NEVER-EXPORT-TEST-SECRET', archive.read('trial.json').decode('utf-8'))
        code, _output, errors = self.run_cli('export', '--output', str(destination), '--reviewed')
        self.assertEqual(code, 1)
        self.assertTrue(errors)

    def test_real_cli_refuses_to_finish_or_review_an_open_case(self):
        self.assertEqual(self.run_cli('start', '--title', 'Open case', '--environment', 'FIXTURE-02')[0], 0)
        self.assertEqual(self.run_cli('begin', 'CORE-02')[0], 0)
        for command in ('finish', 'preview'):
            with self.subTest(command=command):
                code, _output, errors = self.run_cli(command)
                self.assertEqual(code, 1)
                self.assertIn('Record the result of the open case', errors)
        self.assertEqual(self.run_cli('end', '--result', 'BLOCKED', '--notes', 'Fixture dependency absent')[0], 0)
        self.assertEqual(self.run_cli('finish')[0], 0)


if __name__ == '__main__':
    unittest.main()
