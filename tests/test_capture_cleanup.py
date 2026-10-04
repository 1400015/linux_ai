"""Generated capture cleanup must not displace capture results."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.system_utils import SystemUtils


CAPTURE_METHODS = ('capture_screen', 'capture_active_window')


class TestCaptureCleanup(unittest.TestCase):
    def setUp(self):
        self.utility = object.__new__(SystemUtils)
        self.utility.is_wayland = False
        self.utility._which = lambda name: '/fixture/scrot' if name == 'scrot' else None

    @contextmanager
    def generated_capture(self, directory, contents=b'fixture-image', failure=None):
        created = []
        mkstemp = tempfile.mkstemp

        def temporary(**kwargs):
            fd, path = mkstemp(dir=directory, **kwargs)
            created.append(path)
            return fd, path

        def backend(argv, timeout):
            if failure is not None:
                raise failure
            Path(argv[-1]).write_bytes(contents)
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch('tempfile.mkstemp', side_effect=temporary), \
                patch('src.system_utils._run_process', side_effect=backend):
            yield created

    def test_disappearing_temporary_does_not_replace_the_candidate_success(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                def disappears(path):
                    Path(path).unlink()
                    raise FileNotFoundError('temporary disappeared before stat')

                with self.generated_capture(directory) as created, \
                        patch('src.system_utils.os.path.getsize', side_effect=disappears):
                    result = getattr(self.utility, method)()
                self.assertEqual(result, (True, created[0]))
                self.assertFalse(Path(created[0]).exists())

    def test_stat_permission_failure_does_not_replace_success(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                with self.generated_capture(directory) as created, \
                        patch('src.system_utils.os.path.getsize', side_effect=PermissionError('stat denied')):
                    result = getattr(self.utility, method)()
                self.assertEqual(result, (True, created[0]))
                self.assertEqual(Path(created[0]).read_bytes(), b'fixture-image')

    def test_unlink_denial_does_not_replace_the_candidate_success(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                with self.generated_capture(directory, contents=b'') as created, \
                        patch('src.system_utils.os.unlink', side_effect=PermissionError('unlink denied')):
                    result = getattr(self.utility, method)()
                self.assertEqual(result, (True, created[0]))
                self.assertTrue(Path(created[0]).exists())

    def test_stat_failure_preserves_the_handled_capture_failure(self):
        for method in CAPTURE_METHODS:
            for error in (FileNotFoundError('missing temporary'), PermissionError('stat denied')):
                with self.subTest(method=method, cleanup_error=type(error).__name__), \
                        tempfile.TemporaryDirectory() as directory:
                    with self.generated_capture(directory, failure=RuntimeError('fixture backend failed')), \
                            patch('src.system_utils.os.path.getsize', side_effect=error):
                        result = getattr(self.utility, method)()
                    label = 'screen' if method == 'capture_screen' else 'active window'
                    self.assertEqual(result, (False, 'Error capturing {}: fixture backend failed'.format(label)))

    def test_unlink_denial_preserves_the_handled_timeout(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                timeout = subprocess.TimeoutExpired(['/fixture/scrot'], 10)
                with self.generated_capture(directory, failure=timeout) as created, \
                        patch('src.system_utils.os.unlink', side_effect=PermissionError('unlink denied')):
                    result = getattr(self.utility, method)()
                label = 'screen' if method == 'capture_screen' else 'active window'
                self.assertEqual(result, (False, 'Timeout capturing ' + label))
                self.assertTrue(Path(created[0]).exists())

    def test_nonempty_generated_capture_is_retained(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                with self.generated_capture(directory) as created:
                    result = getattr(self.utility, method)()
                self.assertEqual(result, (True, created[0]))
                self.assertEqual(Path(created[0]).read_bytes(), b'fixture-image')

    def test_empty_generated_temporary_is_removed_after_failure(self):
        for method in CAPTURE_METHODS:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                with self.generated_capture(directory, failure=RuntimeError('fixture backend failed')) as created:
                    result = getattr(self.utility, method)()
                self.assertFalse(result[0])
                self.assertFalse(Path(created[0]).exists())

    def test_explicit_caller_output_is_never_removed(self):
        for method in CAPTURE_METHODS:
            for succeeded in (True, False):
                with self.subTest(method=method, succeeded=succeeded), \
                        tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / 'caller-output.png'
                    target.write_bytes(b'')
                    backend = (subprocess.CompletedProcess([], 0, '', '') if succeeded
                               else RuntimeError('fixture backend failed'))
                    with patch('tempfile.mkstemp') as create, \
                            patch('src.system_utils.os.path.getsize') as size, \
                            patch('src.system_utils.os.unlink') as unlink, \
                            patch('src.system_utils._run_process', **(
                                {'return_value': backend} if succeeded else {'side_effect': backend})):
                        result = getattr(self.utility, method)(str(target))
                    self.assertEqual(result[0], succeeded)
                    self.assertTrue(target.exists())
                    create.assert_not_called()
                    size.assert_not_called()
                    unlink.assert_not_called()


if __name__ == '__main__':
    unittest.main()
