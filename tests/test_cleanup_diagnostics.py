"""Cleanup uncertainty survives wrappers without exposing partial secrets."""

import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.device_actions import run_argv
from src.display_actions import DisplayService
from src.offline_assistant import Command, OfflineAssistant
from src.package_actions import _run as package_run
from src.process_output import CLEANUP_UNCERTAINTY
from src.system_utils import SystemUtils


class TestCleanupDiagnostics(unittest.TestCase):
    def exception_runners(self):
        config = SimpleNamespace(get=lambda name, default=None: {
            'permissions.allowed_commands': ['fixture'],
            'permissions.allowed_edit_dirs': [],
        }.get(name, default))
        return [('src.package_actions', package_run),
                ('src.device_actions', run_argv),
                ('src.display_actions', DisplayService(environ={})._run),
                ('src.system_utils', SystemUtils(config).execute_command),
                ('src.offline_assistant', lambda argv: OfflineAssistant.run_privileged(
                    Command(list(argv), privileged=True)))]

    def test_truncated_package_output_keeps_cleanup_warning(self):
        with patch('src.package_actions.run_bounded', return_value=(
                1, 'private-fragment\n... (output truncated at 64 bytes)',
                'another-private-fragment\n' + CLEANUP_UNCERTAINTY)):
            ok, text = package_run(['fixture'])
        self.assertFalse(ok)
        self.assertIn('size limit', text)
        self.assertIn(CLEANUP_UNCERTAINTY, text)
        self.assertNotIn('private-fragment', text)

    def test_truncated_device_output_keeps_warning_without_secret_fragments(self):
        with patch('src.device_actions.run_bounded', return_value=(
                1, 'private-fragment\n... (output truncated at 64 bytes)',
                'another-private-fragment\n' + CLEANUP_UNCERTAINTY)):
            ok, text = run_argv(['fixture'], secret='private-fragment-with-a-long-tail')
        self.assertFalse(ok)
        self.assertIn('output exceeded', text)
        self.assertIn(CLEANUP_UNCERTAINTY, text)
        self.assertNotIn('private-fragment', text)

    def test_timeout_warning_survives_package_and_device_wrappers(self):
        for module, runner in self.exception_runners():
            error = subprocess.TimeoutExpired(['fixture'], 1)
            error.cleanup_uncertainty = CLEANUP_UNCERTAINTY
            with self.subTest(module=module), patch(module + '.run_bounded', side_effect=error):
                ok, text = runner(['fixture'])
            self.assertFalse(ok)
            self.assertRegex(text, r'timed out|Timeout')
            self.assertIn(CLEANUP_UNCERTAINTY, text)

    def test_capture_failure_warning_survives_package_and_device_wrappers(self):
        for module, runner in self.exception_runners():
            error = OSError('capture unavailable')
            error.cleanup_uncertainty = CLEANUP_UNCERTAINTY
            with self.subTest(module=module), patch(module + '.run_bounded', side_effect=error):
                ok, text = runner(['fixture'])
            self.assertFalse(ok)
            self.assertRegex(text, r'capture unavailable|Error running command: OSError')
            self.assertIn(CLEANUP_UNCERTAINTY, text)


if __name__ == '__main__':
    unittest.main()
