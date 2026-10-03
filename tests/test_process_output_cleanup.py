"""Process-group cleanup must retain ownership and report uncertainty."""

import os
from contextlib import contextmanager
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import process_output
from src.process_output import CLEANUP_UNCERTAINTY, run_bounded


PROC_MOUNT = b'1 0 0:1 / /proc rw,nosuid,nodev,noexec - proc proc rw\n'


def proc_stat(pid, state, session, group=None):
    return '{} (fixture) {} 1 {} {} 0\n'.format(
        pid, state, session if group is None else group, session).encode('ascii')


@contextmanager
def observed_proc(rows, directories=None, mountinfo=PROC_MOUNT):
    directories = directories or {'/proc': [str(pid) for pid in rows]}

    def open_fixture(path, mode):
        if path == '/proc/self/mountinfo':
            data = mountinfo
        elif path == '/proc/self/stat':
            data = proc_stat(os.getpid(), 'S', 1)
        elif path in rows:
            data = rows[path]
        else:
            pid = int(path.split('/')[2])
            data = rows[pid]
        if isinstance(data, Exception):
            raise data
        return io.BytesIO(data)

    @contextmanager
    def scan_fixture(path):
        yield iter(SimpleNamespace(name=name) for name in directories[path])

    with patch('builtins.open', side_effect=open_fixture), \
            patch('src.process_output.os.scandir', side_effect=scan_fixture):
        yield


class TestProcessOutputCleanup(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.original_popen = subprocess.Popen
        self.original_killpg = os.killpg

    def spawn(self, *args, **kwargs):
        process = self.original_popen(*args, **kwargs)
        self.children.append(process)
        return process

    def tearDown(self):
        # Denial fixtures deliberately leave a real child alive. Kill the
        # owned leader directly without repeating a group signal after reap.
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)

    def test_success_signals_the_group_before_reaping_the_exited_leader(self):
        observations = []

        def signal_group(pid, signum):
            process = self.children[-1]
            self.assertIsNone(process.returncode)
            status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            self.assertIsNotNone(status)
            self.assertEqual(status.si_code, os.CLD_EXITED)
            observations.append(status.si_status)
            return self.original_killpg(pid, signum)

        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.os.killpg', side_effect=signal_group):
            code, output, errors = run_bounded(
                [sys.executable, '-c', 'print("completed")'], 3, 4096)
        self.assertEqual((code, output, errors), (0, 'completed\n', ''))
        self.assertEqual(observations, [0])
        self.assertEqual(self.children[0].returncode, 0)

    def test_success_still_cleans_a_descendant_with_closed_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'unexpected'
            script = ('import os,time,pathlib,sys\n'
                      'if os.fork() == 0:\n'
                      ' os.close(1); os.close(2); time.sleep(.3)\n'
                      ' pathlib.Path(sys.argv[1]).write_text("survived")\n')
            code, _, errors = run_bounded(
                [sys.executable, '-c', script, str(target)], 3, 4096)
            self.assertEqual(code, 0, errors)
            time.sleep(.4)
            self.assertFalse(target.exists())

    def test_dead_leader_with_descendant_holding_the_pipe_still_times_out(self):
        observations = []

        def signal_group(pid, signum):
            self.assertIsNone(self.children[-1].returncode)
            status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            self.assertIsNotNone(status)
            observations.append(status.si_status)
            return self.original_killpg(pid, signum)

        script = 'import os,time\nif os.fork() == 0: time.sleep(10)\n'
        started = time.monotonic()
        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.os.killpg', side_effect=signal_group):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_bounded([sys.executable, '-c', script], .15, 4096)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(observations, [0])

    def test_closed_pipes_do_not_remove_the_leaders_deadline(self):
        script = 'import os,time; os.close(1); os.close(2); time.sleep(10)'
        started = time.monotonic()
        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_bounded([sys.executable, '-c', script], .15, 4096)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(self.children[0].returncode)

    def test_denied_cleanup_never_reports_success(self):
        with patch('src.process_output.os.killpg', side_effect=PermissionError('denied')), \
                patch('src.process_output._proc_session_empty', return_value=False):
            code, output, errors = run_bounded(
                [sys.executable, '-c', 'print("completed")'], 3, 4096)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, 'completed\n')
        self.assertIn(CLEANUP_UNCERTAINTY, errors)

    def test_denial_on_a_proven_zombie_only_session_preserves_success(self):
        original_observe = process_output._proc_session_empty

        def checked_observe(pid):
            with observed_proc({pid: proc_stat(pid, 'Z', pid)}):
                return original_observe(pid)

        with patch('src.process_output.os.killpg', side_effect=PermissionError('root-owned zombie')), \
                patch('src.process_output._proc_session_empty', side_effect=checked_observe):
            code, output, errors = run_bounded(
                [sys.executable, '-c', 'print("completed")'], 3, 4096)
        self.assertEqual((code, output, errors), (0, 'completed\n', ''))

    def test_exited_cleanup_supports_python_without_optional_child_status_constants(self):
        # Python 3.8 supplies waitid/WEXITED but not CLD_KILLED/CLD_DUMPED.
        compatibility_os = SimpleNamespace(**{name: getattr(os, name) for name in (
            'read', 'waitid', 'P_PID', 'WEXITED', 'WNOHANG', 'WNOWAIT', 'CLD_EXITED')})
        def deny_signal(*args):
            raise PermissionError('denied')
        compatibility_os.killpg = deny_signal
        with patch('src.process_output.os', compatibility_os), \
                patch('src.process_output._proc_session_empty', return_value=True):
            result = run_bounded([sys.executable, '-c', 'print("completed")'], 3, 4096)
        self.assertEqual(result, (0, 'completed\n', ''))

    def test_denial_with_an_exited_leader_and_live_session_member_stays_uncertain(self):
        original_observe = process_output._proc_session_empty

        def checked_observe(pid):
            rows = {pid: proc_stat(pid, 'Z', pid),
                    pid + 10000: proc_stat(pid + 10000, 'S', pid, group=pid + 10000)}
            with observed_proc(rows):
                return original_observe(pid)

        with patch('src.process_output.os.killpg', side_effect=PermissionError('denied')), \
                patch('src.process_output._proc_session_empty', side_effect=checked_observe):
            code, output, errors = run_bounded(
                [sys.executable, '-c', 'print("completed")'], 3, 4096)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, 'completed\n')
        self.assertIn(CLEANUP_UNCERTAINTY, errors)

    def test_truncation_with_denied_cleanup_reports_a_live_child(self):
        script = 'import sys,time; sys.stdout.write("x"*20000); sys.stdout.flush(); time.sleep(10)'
        started = time.monotonic()
        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.os.killpg', side_effect=PermissionError('denied')):
            code, output, errors = run_bounded([sys.executable, '-c', script], 3, 4096)
        self.assertLess(time.monotonic() - started, 2)
        self.assertNotEqual(code, 0)
        self.assertIn('truncated at 4096 bytes', output)
        self.assertIn(CLEANUP_UNCERTAINTY, errors)
        self.assertIsNone(self.children[0].poll())

    def test_timeout_with_denied_cleanup_preserves_type_and_diagnosis(self):
        started = time.monotonic()
        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.os.killpg', side_effect=PermissionError('denied')):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                run_bounded([sys.executable, '-c', 'import time; time.sleep(10)'], .1, 4096)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(raised.exception.cleanup_uncertainty, CLEANUP_UNCERTAINTY)
        self.assertIn(CLEANUP_UNCERTAINTY, raised.exception.stderr)

    def test_external_reap_disables_group_signalling(self):
        original_waitid = os.waitid
        externally_reaped = []

        def observe_after_external_reap(idtype, pid, options):
            if not externally_reaped:
                original_waitid(idtype, pid, os.WEXITED | os.WNOWAIT)
                externally_reaped.append(os.waitpid(pid, 0)[0])
            return original_waitid(idtype, pid, options)

        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.os.waitid', side_effect=observe_after_external_reap), \
                patch('src.process_output.os.killpg') as killpg:
            code, _, errors = run_bounded([sys.executable, '-c', 'print("completed")'], 3, 4096)
        killpg.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertIn(CLEANUP_UNCERTAINTY, errors)
        self.assertEqual(externally_reaped, [self.children[0].pid])

    def test_other_failure_keeps_its_identity_with_cleanup_diagnosis(self):
        failure = OSError('capture unavailable')
        with patch('src.process_output.subprocess.Popen', side_effect=self.spawn), \
                patch('src.process_output.selectors.DefaultSelector', side_effect=failure), \
                patch('src.process_output.os.killpg', side_effect=PermissionError('denied')):
            with self.assertRaises(OSError) as raised:
                run_bounded([sys.executable, '-c', 'import time; time.sleep(10)'], 3, 4096)
        self.assertIs(raised.exception, failure)
        self.assertEqual(raised.exception.cleanup_uncertainty, CLEANUP_UNCERTAINTY)


class TestProcSessionObservation(unittest.TestCase):
    def test_live_member_of_another_process_group_is_not_empty(self):
        rows = {101: proc_stat(101, 'Z', 101), 202: proc_stat(202, 'S', 101, group=202)}
        with observed_proc(rows):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_unrelated_live_session_does_not_hide_a_zombie_only_session(self):
        rows = {101: proc_stat(101, 'Z', 101), 202: proc_stat(202, 'S', 202)}
        with observed_proc(rows):
            self.assertTrue(process_output._proc_session_empty(101))

    def test_zombie_descendant_with_a_live_thread_is_not_empty(self):
        rows = {
            101: proc_stat(101, 'Z', 101), 202: proc_stat(202, 'Z', 101),
            '/proc/202/task/202/stat': proc_stat(202, 'Z', 101),
            '/proc/202/task/203/stat': proc_stat(203, 'S', 101),
        }
        directories = {'/proc': ['101', '202'], '/proc/202/task': ['202', '203']}
        with observed_proc(rows, directories):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_unreadable_row_cannot_establish_an_empty_session(self):
        rows = {101: proc_stat(101, 'Z', 101), 202: PermissionError('unreadable')}
        with observed_proc(rows):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_missing_leader_cannot_establish_an_empty_session(self):
        with observed_proc({202: proc_stat(202, 'Z', 202)}):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_restricted_proc_mount_cannot_establish_an_empty_session(self):
        mountinfo = PROC_MOUNT.rstrip() + b',hidepid=2\n'
        with observed_proc({101: proc_stat(101, 'Z', 101)}, mountinfo=mountinfo):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_entry_budget_is_conservative(self):
        with observed_proc({101: proc_stat(101, 'Z', 101)}), \
                patch('src.process_output._PROC_SCAN_MAX_PIDS', 0):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_time_budget_is_conservative(self):
        with observed_proc({101: proc_stat(101, 'Z', 101)}), \
                patch('src.process_output._PROC_SCAN_TIMEOUT', 0):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_stat_read_budget_is_conservative(self):
        with observed_proc({101: proc_stat(101, 'Z', 101) + b'x' * 4096}):
            self.assertFalse(process_output._proc_session_empty(101))

    def test_malformed_stat_cannot_establish_an_empty_session(self):
        with observed_proc({101: b'101 (fixture) Z 1 101 malformed\n'}):
            self.assertFalse(process_output._proc_session_empty(101))


if __name__ == '__main__':
    unittest.main()
