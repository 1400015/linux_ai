"""The optional backup argument cannot bypass the root helper's file policy."""

import unittest
from unittest.mock import patch

from src.privileged_write import _backup_file


class TestBackupPolicy(unittest.TestCase):
    def test_sensitive_adjacent_backup_is_rejected_before_filesystem_io(self):
        with patch('src.privileged_write.os.open') as opening:
            for destination in ('/etc/passwd', '/etc/shadow', '/etc/sudoers'):
                with self.subTest(destination=destination), self.assertRaises(PermissionError):
                    _backup_file(999, '/etc/hostname', destination)
            opening.assert_not_called()
