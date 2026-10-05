"""Recovery evidence survives copy, publication, journal and archive failures."""

import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.change_journal import ChangeJournal, file_digest
from src.file_actions import _operation_result, _run_privileged, is_allowed_path
from src.privileged_write import (FileOperationError, is_sensitive_path, main,
                                  planned_backup_path, remove_file, write_file)


class JournalHardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.write_text('approved new')
        self.target.write_text('approved old')
        info = self.root.stat()
        self.identity = info.st_dev, info.st_ino
        self.before = file_digest(self.target)
        self.journal = ChangeJournal(self.root / 'changes.json')
        self.local = patch('src.file_actions.is_privileged_path', return_value=False)
        self.local.start()
        self.addCleanup(self.local.stop)

    def apply(self):
        return self.journal.apply(str(self.source), str(self.target), file_digest(self.target),
                                  [str(self.root)], 'conversation')

    def fail_final_directory_sync(self):
        original = os.fsync
        calls = []

        def sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                calls.append(descriptor)
                if len(calls) == 2:
                    raise OSError('directory sync failed after publication')
            return original(descriptor)

        return patch('src.privileged_write.os.fsync', side_effect=sync)

    def test_partial_backup_is_removed_before_unchanged_failure(self):
        def fail_copy(source, target, length):
            target.write(b'partial')
            raise OSError('copy failed')

        with patch('src.privileged_write.shutil.copyfileobj', side_effect=fail_copy):
            with self.assertRaises(FileOperationError) as caught:
                write_file(str(self.source), str(self.target), self.before, self.identity)
        self.assertIs(caught.exception.outcome['published'], False)
        self.assertIsNone(caught.exception.outcome['backup'])
        self.assertEqual(self.target.read_text(), 'approved old')
        self.assertEqual(list(self.root.glob('target.bak-*')), [])

    def test_destination_copy_failure_retains_complete_backup(self):
        original = shutil.copyfileobj
        calls = []

        def copy(source, target, length):
            calls.append(True)
            if len(calls) == 2:
                target.write(b'partial destination')
                raise OSError('destination copy failed')
            return original(source, target, length)

        with patch('src.privileged_write.shutil.copyfileobj', side_effect=copy):
            with self.assertRaises(FileOperationError) as caught:
                write_file(str(self.source), str(self.target), self.before, self.identity)
        self.assertIs(caught.exception.outcome['published'], False)
        self.assertEqual(Path(caught.exception.outcome['backup']).read_text(), 'approved old')
        self.assertEqual(self.target.read_text(), 'approved old')
        self.assertEqual(list(self.root.glob('.linux-ai-*')), [])

    def test_postpublication_sync_reports_changed_target_and_retained_backup(self):
        backup = planned_backup_path(str(self.target))
        with self.fail_final_directory_sync():
            with self.assertRaises(FileOperationError) as caught:
                write_file(str(self.source), str(self.target), self.before, self.identity,
                           file_digest(self.source), backup)
        self.assertIs(caught.exception.outcome['published'], True)
        self.assertEqual(caught.exception.outcome['backup'], backup)
        self.assertEqual(self.target.read_text(), 'approved new')
        self.assertEqual(Path(backup).read_text(), 'approved old')

    def test_removal_sync_failure_retains_recovery_copy(self):
        with self.fail_final_directory_sync():
            with self.assertRaises(FileOperationError) as caught:
                remove_file(str(self.target), self.before, self.identity)
        self.assertIs(caught.exception.outcome['published'], True)
        self.assertFalse(self.target.exists())
        self.assertEqual(Path(caught.exception.outcome['backup']).read_text(), 'approved old')

    def test_helper_returns_structured_failure_after_publication(self):
        args = ['helper', str(self.source), str(self.target), self.before,
                str(self.identity[0]), str(self.identity[1]), file_digest(self.source),
                planned_backup_path(str(self.target))]
        output = io.StringIO()
        with patch('sys.argv', args), patch('sys.stdout', output), patch('sys.stderr', io.StringIO()), \
                self.fail_final_directory_sync():
            self.assertEqual(main(), 1)
        result = json.loads(output.getvalue())
        self.assertIs(result['outcome']['published'], True)
        self.assertEqual(Path(result['outcome']['backup']).read_text(), 'approved old')

    def test_privileged_entry_point_requires_the_approved_source_hash(self):
        base = ['helper', str(self.source), str(self.target), self.before,
                str(self.identity[0]), str(self.identity[1])]
        for args in (base, base + ['-'], base + ['invalid']):
            with self.subTest(args=args), patch('sys.argv', args), \
                    patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
                self.assertEqual(main(), 1)
        self.assertEqual(self.target.read_text(), 'approved old')

    def test_frontend_preserves_helper_outcome_on_nonzero_exit(self):
        result = subprocess.CompletedProcess([], 1, json.dumps({'outcome': {
            'published': True, 'backup': '/fixture/backup', 'cleanup': []}}), 'sync failed')
        with self.assertRaises(FileOperationError) as caught:
            _operation_result(result, 'write failed')
        self.assertIs(caught.exception.outcome['published'], True)
        self.assertEqual(caught.exception.outcome['backup'], '/fixture/backup')

    def test_timeout_does_not_claim_unchanged_target(self):
        with patch('src.file_actions.run_bounded', side_effect=subprocess.TimeoutExpired([], 120)):
            with self.assertRaises(FileOperationError) as caught:
                _run_privileged(['fixture'])
        self.assertIsNone(caught.exception.outcome['published'])

    def test_journal_preserves_uncertain_publication_and_can_revalidate_recovery(self):
        original = ChangeJournal._write

        def uncertain(*args):
            with self.fail_final_directory_sync():
                return original(*args)

        with patch.object(ChangeJournal, '_write', side_effect=uncertain):
            with self.assertRaises(FileOperationError):
                self.apply()
        record = self.journal.list_changes()[0]
        self.assertEqual(record['status'], 'uncertain')
        self.assertIs(record['published'], True)
        self.assertEqual(Path(record['backup']).read_text(), 'approved old')
        self.journal.restore(record['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'approved old')

    def test_pending_record_already_contains_recovery_path_when_finalization_fails(self):
        from src.storage import atomic_json_write
        calls = []

        def persist(path, doc):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('journal full')
            atomic_json_write(path, doc)

        with patch('src.change_journal.atomic_json_write', side_effect=persist):
            with self.assertRaisesRegex(OSError, 'File written'):
                self.apply()
        record = self.journal.list_changes()[0]
        self.assertEqual(record['status'], 'pending')
        self.assertEqual(Path(record['backup']).read_text(), 'approved old')
        self.journal.restore(record['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'approved old')

    def test_size_reservation_refuses_write_before_touching_target(self):
        with patch('src.change_journal.MAX_JOURNAL_BYTES', 256):
            with self.assertRaisesRegex(ValueError, 'archive'):
                self.apply()
        self.assertEqual(self.target.read_text(), 'approved old')
        self.assertFalse(self.journal.path.exists())
        self.assertEqual(list(self.root.glob('target.bak-*')), [])

    def test_recovery_size_preflight_prevents_target_mutation(self):
        record = self.apply()
        size = self.journal.path.stat().st_size
        with patch('src.change_journal.MAX_JOURNAL_BYTES', size + 100):
            with self.assertRaisesRegex(ValueError, 'archive'):
                self.journal.restore(record['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'approved new')
        self.assertEqual(self.journal.list_changes()[0]['status'], 'applied')

    def test_control_characters_in_paths_reserve_their_json_encoded_size(self):
        self.target = self.root / ('control-' + '\x01' * 100)
        self.target.write_text('approved old')
        wire_sizes = []

        def refused_publication(path, doc):
            wire_sizes.append(len(json.dumps(doc, indent=2, ensure_ascii=False).encode('utf-8')))
            raise OSError('fixture refusal before publishing journal')

        with patch('src.change_journal.atomic_json_write', side_effect=refused_publication):
            with self.assertRaises(OSError):
                self.apply()
        # A byte-length margin would admit this reservation, while JSON escape
        # expansion makes the potential retained-file metadata exceed the cap.
        cap = wire_sizes[0] + 2 * len(os.fsencode(self.target)) + 400
        with patch('src.change_journal.MAX_JOURNAL_BYTES', cap):
            with self.assertRaises(ValueError):
                self.apply()
        self.assertEqual(self.target.read_text(), 'approved old')
        self.assertFalse(self.journal.path.exists())

    def test_uncertain_recovery_preserves_both_backups_and_refuses_blind_retry(self):
        record = self.apply()
        original = ChangeJournal._write

        def uncertain(*args):
            with self.fail_final_directory_sync():
                return original(*args)

        with patch.object(ChangeJournal, '_write', side_effect=uncertain):
            with self.assertRaises(FileOperationError):
                self.journal.restore(record['id'], [str(self.root)])
        stored = self.journal.list_changes()[0]
        self.assertEqual(stored['status'], 'recovery_uncertain')
        self.assertEqual(self.target.read_text(), 'approved old')
        self.assertEqual(Path(stored['backup']).read_text(), 'approved old')
        self.assertEqual(Path(stored['recovery_backup']).read_text(), 'approved new')
        with self.assertRaises(ValueError):
            self.journal.restore(record['id'], [str(self.root)])

    def test_close_failure_after_publication_retains_outcome(self):
        original = os.close

        def close(descriptor):
            directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
            original(descriptor)
            if directory:
                raise OSError('close failed after publication')

        with patch('src.privileged_write.os.close', side_effect=close):
            with self.assertRaises(FileOperationError) as caught:
                write_file(str(self.source), str(self.target), self.before, self.identity)
        self.assertIs(caught.exception.outcome['published'], True)
        self.assertEqual(Path(caught.exception.outcome['backup']).read_text(), 'approved old')
        self.assertEqual(self.target.read_text(), 'approved new')

    def test_archive_frees_capacity_and_retains_restorable_ids_and_backups(self):
        with patch('src.change_journal.MAX_RECORDS', 1):
            first = self.apply()
            original = json.loads(self.journal.path.read_text())
            with self.assertRaisesRegex(ValueError, 'full'):
                self.apply()
            result = self.journal.archive()
            self.assertEqual(result['count'], 1)
            self.assertEqual(json.loads(Path(result['path']).read_text()), original)
            self.assertEqual(Path(result['path']).stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.journal.list_changes(), [])
            self.assertEqual(self.journal.list_changes(include_archived=True)[0]['id'], first['id'])
            self.source.write_text('third content')
            second = self.apply()
            self.journal.restore(second['id'], [str(self.root)])
            self.journal.restore(first['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'approved old')
        archived = json.loads(Path(result['path']).read_text())['changes'][0]
        self.assertEqual(archived['status'], 'restored')
        self.assertEqual(archived['backup'], first['backup'])
        self.assertEqual(Path(archived['recovery_backup']).read_text(), 'approved new')

    def test_archive_refuses_unfinished_operations(self):
        record = self.apply()
        doc = json.loads(self.journal.path.read_text())
        doc['changes'][0]['status'] = 'pending'
        self.journal.path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'Unfinished'):
            self.journal.archive()
        self.assertEqual(self.journal.list_changes()[0]['id'], record['id'])

    def test_archive_refuses_symlink_directory(self):
        self.apply()
        self.journal.archive_directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PermissionError):
            self.journal.archive()
        self.assertTrue(self.journal.path.exists())

    def test_archive_sync_failure_keeps_records_discoverable_by_id(self):
        record = self.apply()
        with patch('src.change_journal.os.fsync', side_effect=OSError('directory sync failed')):
            with self.assertRaisesRegex(OSError, 'archived at'):
                self.journal.archive()
        self.assertEqual(self.journal.list_changes(), [])
        self.assertEqual(self.journal.list_changes(include_archived=True)[0]['id'], record['id'])
        self.journal.restore(record['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'approved old')

    def test_active_journal_symlink_is_preserved_and_refused(self):
        record = self.apply()
        alternate = self.root / 'other-journal.json'
        self.journal.path.rename(alternate)
        self.journal.path.symlink_to(alternate)
        with self.assertRaises(OSError):
            self.journal.archive()
        self.assertTrue(self.journal.path.is_symlink())
        self.assertEqual(json.loads(alternate.read_text())['changes'][0]['id'], record['id'])

    def test_archived_recovery_rejects_external_edits(self):
        record = self.apply()
        self.journal.archive()
        self.target.write_text('someone else changed this')
        with self.assertRaises(PermissionError):
            self.journal.restore(record['id'], [str(self.root)])
        self.assertEqual(self.target.read_text(), 'someone else changed this')

    def test_sensitive_files_are_refused_before_elevation_and_inside_helper(self):
        for path in ('/etc/passwd', '/etc/shadow', '/etc/sudoers.d/new-rule',
                     '/etc/ssh/ssh_host_ed25519_key', '/etc/ssl/private/server.pem',
                     '/usr/libexec/linux-ai-assistant/privileged_write.py',
                     '/usr/share/polkit-1/actions/org.linux_ai_assistant.policy',
                     '/usr/bin/pkexec', '/usr/bin/python3',
                     str(self.root / '.ssh' / 'id_ed25519'),
                     str(self.root / '.aws' / 'credentials')):
            with self.subTest(path=path):
                self.assertTrue(is_sensitive_path(path))
                self.assertFalse(is_allowed_path(path, ['/']))
                with patch('src.privileged_write.os.open') as opened:
                    with self.assertRaises(PermissionError):
                        write_file(str(self.source), path, None, self.identity)
                    opened.assert_not_called()

    def test_sensitive_source_cannot_bypass_target_denylist(self):
        with patch('src.privileged_write.os.open') as opened:
            with self.assertRaises(PermissionError):
                write_file('/etc/shadow', str(self.target), self.before, self.identity)
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
