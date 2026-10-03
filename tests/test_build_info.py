"""Build identity, bounded Git observation, and saved trial compatibility."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from src._version import __version__
from src.build_info import BuildObservation, observe_checkout_build
from src.process_output import run_bounded
from src.trial_recorder import TrialRecorder


COMMIT = 'a' * 40


class BuildInfoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        (self.checkout / '.git').mkdir()
        self.module = self.checkout / 'src' / 'trial_recorder.py'
        self.module.parent.mkdir()
        self.module.write_text('# synthetic source\n', encoding='utf-8')
        self.git = patch('src.build_info.shutil.which', return_value='/usr/bin/git')
        self.git.start()
        self.addCleanup(self.git.stop)

    def runner(self, status='', status_code=0):
        return Mock(side_effect=[(0, 'src/trial_recorder.py\n', ''),
                                 (0, COMMIT + '\n', ''), (1, '', ''),
                                 (0, '100644 ' + COMMIT + ' 0\tsrc/trial_recorder.py\n', ''),
                                 (status_code, status, '')])

    def test_clean_checkout_records_full_commit_of_executed_module_and_ignores_caller_cwd(self):
        runner = self.runner()
        # A wheel caller can be anywhere; only the supplied module locates the
        # repository, with no cwd discovery or caller-controlled Git options.
        with patch('os.getcwd', return_value=str(self.root / 'unrelated')):
            observation = observe_checkout_build(self.module, runner=runner)
        self.assertEqual(observation.metadata(), {'source': 'checkout', 'commit': COMMIT,
                         'reference': COMMIT, 'worktree': 'clean'})
        for call in runner.call_args_list:
            argv, timeout, limit = call.args
            self.assertEqual(argv[argv.index('-C') + 1], str(self.checkout))
            self.assertEqual(timeout, 3)
            self.assertEqual(limit, 65536)
            self.assertIn('core.fsmonitor=false', argv)
            self.assertIn('core.hooksPath=/dev/null', argv)
            self.assertEqual(call.kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
            self.assertEqual(call.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
            self.assertNotIn('GIT_DIR', call.kwargs['env'])
        self.assertEqual(runner.call_args_list[0].args[0][-4:],
                         ['ls-files', '--error-unmatch', '--', 'src/trial_recorder.py'])

    def test_modified_and_untracked_files_make_reference_distinct_from_clean_commit(self):
        for status in (' M src/trial_recorder.py\n', '?? src/local_adapter.py\n'):
            with self.subTest(status=status):
                observation = observe_checkout_build(self.module, runner=self.runner(status))
                self.assertEqual(observation.reference, COMMIT + '-dirty')
                self.assertEqual(observation.commit, COMMIT)
                self.assertEqual(observation.worktree, 'dirty')

    def test_missing_checkout_and_untracked_installed_module_are_unknown(self):
        runner = Mock()
        with patch('pathlib.Path.exists', return_value=False):
            self.assertEqual(observe_checkout_build(self.root / 'installed.py', runner=runner), BuildObservation())
        runner.assert_not_called()
        wheel = self.checkout / '.venv' / 'lib' / 'trial_recorder.py'
        wheel.parent.mkdir(parents=True)
        wheel.write_text('# installed wheel\n', encoding='utf-8')
        runner = Mock(return_value=(1, '', 'untracked module'))
        self.assertEqual(observe_checkout_build(wheel, runner=runner), BuildObservation())
        self.assertEqual(runner.call_count, 1)

    def test_worktree_git_file_is_recognized(self):
        (self.checkout / '.git').rmdir()
        (self.checkout / '.git').write_text('gitdir: /synthetic/common/worktrees/test\n', encoding='utf-8')
        self.assertEqual(observe_checkout_build(self.module, runner=self.runner()).commit, COMMIT)

    def test_git_errors_are_unknown_and_do_not_copy_stderr_into_trial(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(['git'], 3))
        observation = observe_checkout_build(self.module, runner=runner)
        self.assertEqual(observation.reference, 'unknown')
        self.assertTrue(observation.warning)
        runner = Mock(side_effect=[(0, 'source\n', ''), (1, '', 'credential=private-value')])
        observation = observe_checkout_build(self.module, runner=runner)
        self.assertEqual(observation.reference, 'unknown')
        self.assertNotIn('private-value', observation.warning)

    def test_status_failure_preserves_commit_but_does_not_assert_clean(self):
        for failure in ((1, '', 'status failed'), subprocess.TimeoutExpired(['git'], 3)):
            runner = self.runner()
            runner.side_effect = [*list(runner.side_effect)[:-1], failure]
            observation = observe_checkout_build(self.module, runner=runner)
            self.assertEqual(observation.reference, COMMIT + '-worktree-unknown')
            self.assertEqual(observation.worktree, 'unknown')
            self.assertEqual(observation.commit, COMMIT)
            self.assertTrue(observation.warning)

    def test_configured_filters_skip_status_without_reading_or_exporting_filter_values(self):
        runner = Mock(side_effect=[(0, 'source\n', ''), (0, COMMIT + '\n', ''),
                                 (0, 'filter.fixture.clean\0filter.fixture.process\0', '')])
        observation = observe_checkout_build(self.module, runner=runner)
        self.assertEqual(observation.reference, COMMIT + '-worktree-unknown')
        self.assertEqual(runner.call_count, 3)
        query = runner.call_args.args[0]
        self.assertIn('--name-only', query)
        self.assertNotIn('--get-all', query)

    def test_gitlinks_skip_status_and_submodule_helpers(self):
        runner = Mock(side_effect=[(0, 'source\n', ''), (0, COMMIT + '\n', ''), (1, '', ''),
                                 (0, '160000 ' + COMMIT + ' 0\tplugins/submodule\n', '')])
        observation = observe_checkout_build(self.module, runner=runner)
        self.assertEqual(observation.worktree, 'unknown')
        self.assertIn('submodules', observation.warning)
        self.assertEqual(runner.call_count, 4)

    def test_partial_clone_configuration_skips_status_without_inspecting_values(self):
        for name in ('remote.fixture.promisor\0', 'extensions.partialclone\0'):
            with self.subTest(name=name):
                runner = Mock(side_effect=[(0, 'source\n', ''), (0, COMMIT + '\n', ''), (0, name, '')])
                observation = observe_checkout_build(self.module, runner=runner)
                self.assertEqual(observation.reference, COMMIT + '-worktree-unknown')
                self.assertIn('partial-clone', observation.warning)
                self.assertEqual(runner.call_count, 3)

    def test_failed_or_truncated_filter_and_index_queries_do_not_launch_status(self):
        for code, names in ((128, ''), (1, 'filter.partial.clean\0'), (0, '')):
            with self.subTest(code=code, names=names):
                runner = Mock(side_effect=[(0, 'source\n', ''), (0, COMMIT + '\n', ''), (code, names, '')])
                observation = observe_checkout_build(self.module, runner=runner)
                self.assertEqual(observation.worktree, 'unknown')
                self.assertEqual(runner.call_count, 3)
        runner = Mock(side_effect=[(0, 'source\n', ''), (0, COMMIT + '\n', ''), (1, '', ''),
                                 (1, '100644 partial-output', '')])
        self.assertEqual(observe_checkout_build(self.module, runner=runner).worktree, 'unknown')
        self.assertEqual(runner.call_count, 4)

    def test_real_checkout_filters_never_run_during_observation(self):
        # Synthetic Git history is confined to this test's temporary directory.
        executable = shutil.which('git')
        if not executable or not Path(executable).is_file():
            self.skipTest('Git is required for the local filter regression')
        environment = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}

        def git(*arguments):
            result = subprocess.run([executable, '-C', str(self.checkout), *arguments],
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=5, env=environment, check=True)
            return result.stdout.decode('utf-8').strip()

        git('init', '-q')
        git('add', 'src/trial_recorder.py')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgSign=false', 'commit', '-q', '-m', 'Synthetic fixture')
        sentinel = self.checkout / 'FILTER-MUST-NOT-RUN'
        for option in ('clean', 'process'):
            with self.subTest(option=option):
                git('config', 'filter.fixture.' + option, 'touch FILTER-MUST-NOT-RUN; cat')
                (self.checkout / '.gitattributes').write_text('*.py filter=fixture\n', encoding='utf-8')
                # Keep the size unchanged so status would have to compare
                # content rather than declaring a size-only modification.
                self.module.write_text('# different source\n', encoding='utf-8')
                observation = observe_checkout_build(self.module)
                self.assertEqual(observation.source, 'checkout')
                self.assertEqual(observation.worktree, 'unknown')
                self.assertIn('filters', observation.warning)
                self.assertFalse(sentinel.exists())
                git('config', '--unset', 'filter.fixture.' + option)

    def test_real_partial_clone_keys_skip_status_without_remote_access(self):
        executable = shutil.which('git')
        if not executable or not Path(executable).is_file():
            self.skipTest('Git is required for the local partial-clone regression')
        environment = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}

        def git(*arguments):
            subprocess.run([executable, '-C', str(self.checkout), *arguments],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=5, env=environment, check=True)

        git('init', '-q')
        git('add', 'src/trial_recorder.py')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgSign=false', 'commit', '-q', '-m', 'Synthetic fixture')
        # No remote URL is configured or contacted. Each key alone is enough
        # to avoid worktree reads that might trigger an implicit fetch.
        for key in ('remote.fixture.promisor', 'extensions.partialclone'):
            with self.subTest(key=key):
                git('config', key, 'true' if key.endswith('.promisor') else 'fixture')
                runner = Mock(wraps=run_bounded)
                observation = observe_checkout_build(self.module, runner=runner)
                self.assertEqual(observation.source, 'checkout')
                self.assertEqual(observation.worktree, 'unknown')
                self.assertIn('partial-clone', observation.warning)
                self.assertEqual(runner.call_count, 3)
                git('config', '--unset', key)


class TrialBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.observer = Mock(return_value=BuildObservation(COMMIT + '-dirty', 'checkout', COMMIT, 'dirty'))
        self.recorder = TrialRecorder(self.root / 'trials', context_factory=lambda: {},
                                      build_observer=self.observer)

    def test_blank_build_observes_once_and_cases_review_export_do_not_execute(self):
        run = self.recorder.start('Trial', 'ENV-01', build_ref='   ')
        self.assertEqual(run['build']['version'], __version__)
        self.assertEqual(run['build']['reference'], COMMIT + '-dirty')
        self.assertEqual(run['build']['source'], 'checkout')
        self.observer.assert_called_once()
        self.assertEqual(Path(self.observer.call_args.args[0]).name, 'trial_recorder.py')
        with patch('subprocess.run', side_effect=AssertionError('Unexpected command')), \
                patch('subprocess.Popen', side_effect=AssertionError('Unexpected command')):
            self.recorder.begin_case('CASE-01', 'session-one')
            self.recorder.end_case('PASS')
            self.recorder.finish()
            preview = self.recorder.preview()
            self.assertIn('Build reference source: checkout', preview)
            self.assertIn('Observed checkout changes: dirty', preview)
            destination = self.root / 'trial.zip'
            self.recorder.export(destination, reviewed=True)
            with zipfile.ZipFile(destination) as archive:
                exported = json.loads(archive.read('trial.json'))
                self.assertEqual(exported['build']['commit'], COMMIT)
        self.observer.assert_called_once()

    def test_explicit_operator_reference_skips_all_git_observation(self):
        with patch('subprocess.Popen', side_effect=AssertionError('Unexpected Git command')):
            run = self.recorder.start('Trial', 'ENV-01', build_ref='verified-artifact-42')
        self.observer.assert_not_called()
        self.assertEqual(run['build']['reference'], 'verified-artifact-42')
        self.assertEqual(run['build']['source'], 'operator')
        self.assertEqual(run['build']['commit'], '')
        self.assertEqual(run['build']['worktree'], 'unknown')

    def test_old_trial_build_shape_remains_readable_reviewable_and_exportable(self):
        run = self.recorder.start('Old trial', 'ENV-01', build_ref='old-wheel')
        self.recorder.finish()
        stored = self.root / 'trials' / run['id'] / 'run.json'
        value = json.loads(stored.read_text(encoding='utf-8'))
        value['build'] = {'version': '1.3.1', 'reference': 'old-wheel', 'python': '3.8.20'}
        stored.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(self.recorder.current()['build'], value['build'])
        self.recorder.preview()
        self.recorder.export(self.root / 'old.zip', reviewed=True)

    def test_unknown_observation_and_warning_are_recorded_without_claiming_a_commit(self):
        self.observer.return_value = BuildObservation(warning='Build reference unavailable: Git failed')
        run = self.recorder.start('Trial', 'ENV-01')
        self.assertEqual(run['build']['reference'], 'unknown')
        self.assertEqual(run['build']['source'], 'unknown')
        self.assertEqual(run['build']['worktree'], 'unknown')
        self.assertTrue(run['warnings'])

    def test_invalid_new_provenance_is_rejected_and_original_is_preserved(self):
        run = self.recorder.start('Trial', 'ENV-01', build_ref='explicit-build')
        stored = self.root / 'trials' / run['id'] / 'run.json'
        original = stored.read_text(encoding='utf-8')
        for update in ({'source': 'invented'}, {'commit': COMMIT}, {'worktree': 'clean'}, {'extra': 'field'}):
            with self.subTest(update=update):
                value = json.loads(original)
                value['build'].update(update)
                stored.write_text(json.dumps(value), encoding='utf-8')
                before = stored.read_bytes()
                with self.assertRaises(ValueError):
                    self.recorder.current()
                self.assertEqual(stored.read_bytes(), before)
