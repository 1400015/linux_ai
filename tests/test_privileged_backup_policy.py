"""The optional backup argument cannot bypass the root helper's file policy."""

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from src.change_journal import ChangeJournal
from src.file_actions import is_allowed_path
from src.privileged_write import (_backup_file, inspect_file, is_sensitive_path,
                                  remove_file, write_file)


ACCOUNT_BACKUPS = ('/etc/passwd-', '/etc/shadow-', '/etc/group~', '/etc/gshadow.bak',
                   '/etc/sudoers.tmp', '/etc/shadow.old.1', '/etc/passwd.orig',
                   '/etc/sudoers.save', '/etc/shadow.bak-0123456789',
                   '/etc/shadow.bak2', '/etc/shadow.~1~', '/etc/.sudoers.swp',
                   '/etc/#sudoers#', '/etc/.#sudoers', '/etc/shadow.dpkg-old',
                   '/etc/sudoers.rpmsave')


class TestBackupPolicy(unittest.TestCase):
    def test_sensitive_adjacent_backup_is_rejected_before_filesystem_io(self):
        with patch('src.privileged_write.os.open') as opening:
            for destination in ('/etc/passwd', '/etc/shadow', '/etc/sudoers'):
                with self.subTest(destination=destination), self.assertRaises(PermissionError):
                    _backup_file(999, '/etc/hostname', destination)
            opening.assert_not_called()

    def test_account_backups_are_denied_even_with_an_explicit_allowlist(self):
        for path in ACCOUNT_BACKUPS:
            with self.subTest(path=path):
                self.assertTrue(is_sensitive_path(path))
                self.assertFalse(is_allowed_path(path, ['/']))

    def test_helpers_reject_backup_targets_before_opening_files(self):
        for path in ACCOUNT_BACKUPS:
            operations = (lambda: write_file('/fixture/source', path, None, (0, 0)),
                          lambda: remove_file(path, 'digest', (0, 0)),
                          lambda: inspect_file(path, 'digest', (0, 0)),
                          lambda: _backup_file(999, '/etc/hostname', path))
            for index, operation in enumerate(operations):
                with self.subTest(path=path, operation=index), \
                        patch('src.privileged_write.os.open') as opening:
                    with self.assertRaises(PermissionError):
                        operation()
                    opening.assert_not_called()

    def test_recovery_cannot_copy_sensitive_backup_to_an_allowed_target(self):
        for path in ACCOUNT_BACKUPS:
            with self.subTest(source=path), patch('src.privileged_write.os.open') as opening:
                with self.assertRaises(PermissionError):
                    write_file(path, '/fixture/target', 'digest', (0, 0))
                opening.assert_not_called()

    def test_journal_write_preview_and_recovery_refuse_preexisting_backup_records(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ChangeJournal(Path(directory) / 'changes.json')
            for path in ('/etc/shadow-', '/etc/sudoers.tmp'):
                record = {'id': 'fixture', 'path': path, 'status': 'applied',
                          'before': None, 'after': 'digest'}
                with self.subTest(path=path), \
                        patch.object(journal, '_find', return_value=(journal.path, {}, record)), \
                        patch.object(journal, '_identity', return_value=(0, 0)), \
                        patch('src.change_journal.file_digest') as digest, \
                        patch.object(journal, '_write') as writing:
                    for operation in (lambda: journal.apply('/fixture/source', path, None, ['/']),
                                      lambda: journal.preview_restore('fixture', ['/']),
                                      lambda: journal.restore('fixture', ['/'])):
                        with self.assertRaises(PermissionError):
                            operation()
                    digest.assert_not_called()
                    writing.assert_not_called()

    def test_unrelated_nearby_names_remain_eligible(self):
        for path in ('/etc/hostname.bak', '/etc/passwd.conf', '/etc/shadowing.conf',
                     '/etc/sudoers_rules.tmp', '/etc/sudoers.tmpfiles', '/tmp/shadow.bak'):
            with self.subTest(path=path):
                self.assertFalse(is_sensitive_path(path))
                self.assertTrue(is_allowed_path(path, ['/']))
