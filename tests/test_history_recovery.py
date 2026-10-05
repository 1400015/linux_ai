"""Bounded private history recovery without discarding unknown data or queues."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.cli import CLIApp, main
from src.history_store import HistoryStore
from src.storage import JsonLimitError, JsonWriteCommittedError, atomic_json_write, read_json, update_json


class TestBoundedHistoryRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'history.json'

    def store(self):
        store = HistoryStore(self.path)
        self.addCleanup(store.close)
        return store

    def test_large_local_file_is_refused_without_parsing_or_replacing_it(self):
        raw = b'[' + b' ' * 1024 + b']'
        self.path.write_bytes(raw)
        with patch('src.history_store.MAX_HISTORY_BYTES', 1024), \
                patch('src.storage.json.loads', side_effect=AssertionError('Parsed oversized data')):
            with self.assertRaisesRegex(JsonLimitError, 'history recover --yes'):
                self.store()
            with self.assertRaises(JsonLimitError):
                HistoryStore.save_entries(self.path, [{'role': 'user', 'content': 'new'}])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('*.corrupt-*')), [])

    def test_writer_limit_matches_reader_and_preserves_previous_document(self):
        previous = [{'role': 'user', 'content': 'small', 'timestamp': 1}]
        with patch('src.history_store.MAX_HISTORY_BYTES', 1024):
            HistoryStore.save_entries(self.path, previous)
            before = self.path.read_bytes()
            self.assertEqual(self.store().load_entries(), previous)
            with self.assertRaises(JsonLimitError):
                HistoryStore.save_entries(self.path, [{'role': 'assistant', 'content': 'é' * 1024}])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_writer_counts_serialized_utf8_bytes_including_json_escaping(self):
        self.path.write_text('[]', encoding='utf-8')
        for value in ('é' * 20, '\x00' * 20):
            with self.subTest(value=value[:1]):
                with self.assertRaises(JsonLimitError):
                    atomic_json_write(self.path, [value], max_bytes=30)
                self.assertEqual(self.path.read_text(), '[]')

    def test_exact_byte_limit_can_be_published_and_read(self):
        value = {'text': 'é\n'}
        size = len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
        atomic_json_write(self.path, value, max_bytes=size)
        self.assertEqual(read_json(self.path, max_bytes=size), value)
        with self.assertRaises(JsonLimitError):
            read_json(self.path, max_bytes=size - 1)

    def test_deeply_nested_json_remains_preserved(self):
        raw = b'[' * 2000 + b']' * 2000
        self.path.write_bytes(raw)
        with self.assertRaises(JsonLimitError):
            HistoryStore.save_entries(self.path, [{'role': 'user', 'content': 'new'}])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('*.corrupt-*')), [])

    def test_nesting_limit_applies_to_writes_and_ignores_quoted_brackets(self):
        self.path.write_text('[]')
        value = 'leaf'
        for unused in range(129):
            value = [value]
        with self.assertRaises(JsonLimitError):
            atomic_json_write(self.path, value, max_bytes=65536)
        self.assertEqual(self.path.read_text(), '[]')
        quoted = {'text': '[{\\"' * 1000}
        atomic_json_write(self.path, quoted, max_bytes=65536)
        self.assertEqual(read_json(self.path, max_bytes=65536), quoted)

    def test_read_refuses_fifo_and_symlink_without_blocking(self):
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            read_json(self.path, max_bytes=1024)
        self.path.unlink()
        target = self.directory / 'other.json'
        target.write_text('[]')
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            read_json(self.path, max_bytes=1024)
        with self.assertRaises(OSError):
            HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(target.read_text(), '[]')

    def test_explicit_recovery_retains_every_byte_of_unknown_or_invalid_data(self):
        for raw in (b'{"version":999,"future_field":[1,2,3]}', b'{invalid', b'\xff\xfe', b' ' * 2048):
            with self.subTest(raw=raw[:20]):
                self.path.write_bytes(raw)
                with self.assertRaises(PermissionError):
                    HistoryStore.recover_file(self.path)
                self.assertEqual(self.path.read_bytes(), raw)
                with patch('src.history_store.MAX_HISTORY_BYTES', 1024):
                    backup = HistoryStore.recover_file(self.path, confirmed=True)
                    self.assertEqual(read_json(self.path, 1024)['version'], 1)
                self.assertEqual(backup.read_bytes(), raw)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_recovery_does_not_parse_future_format(self):
        raw = b'{"version":999,"unsupported":true}'
        self.path.write_bytes(raw)
        with patch('src.history_store.json.loads', side_effect=AssertionError('Interpreted future data')):
            backup = HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(backup.read_bytes(), raw)

    def test_failed_backup_copy_keeps_original_and_removes_partial_backup(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)

        def interrupted(source, target, length):
            target.write(source.read(3))
            raise OSError('copy failed')

        with patch('src.history_store.shutil.copyfileobj', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('*.recovered-*')), [])

    def test_backup_sync_failure_cannot_reset_original(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        with patch('src.history_store.os.fsync', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):
                HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob('*.recovered-*')), [])

    def test_failed_reset_retains_complete_backup_and_reports_its_path(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        with patch('src.history_store.atomic_json_write', side_effect=OSError('publication denied')):
            with self.assertRaisesRegex(OSError, 'publication denied') as raised:
                HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(raised.exception.recovery_backup.read_bytes(), raw)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_post_publication_failure_reports_retained_backup(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        fsync = os.fsync
        directory_calls = []

        def synchronize(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_calls.append(fd)
                if len(directory_calls) == 2:
                    raise OSError('new history durability uncertain')
            fsync(fd)

        with patch('src.storage.os.fsync', side_effect=synchronize):
            with self.assertRaises(JsonWriteCommittedError) as raised:
                HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(raised.exception.recovery_backup.read_bytes(), raw)
        self.assertEqual(read_json(self.path, 1024)['version'], 1)

    def test_lock_close_failure_also_reports_retained_backup(self):
        raw = b'{"version":999}'
        self.path.write_bytes(raw)
        open_fd, close_fd = os.open, os.close
        lock_fds = set()

        def opened(path, flags, *args, **kwargs):
            fd = open_fd(path, flags, *args, **kwargs)
            if str(path) == str(self.path) + '.lock':
                lock_fds.add(fd)
            return fd

        def closed(fd):
            close_fd(fd)
            if fd in lock_fds:
                lock_fds.remove(fd)
                raise OSError('lock completion uncertain')

        with patch('src.storage.os.open', side_effect=opened), patch('src.storage.os.close', side_effect=closed):
            with self.assertRaises(OSError) as raised:
                HistoryStore.recover_file(self.path, confirmed=True)
        self.assertEqual(raised.exception.recovery_backup.read_bytes(), raw)
        self.assertEqual(read_json(self.path, 1024)['version'], 1)

    def test_recovery_keeps_failed_pending_messages(self):
        store = self.store()
        store.create_session('Previous session')
        raw = b'{"version":999,"private_future_data":1}'
        self.path.write_bytes(raw)
        store.append('assistant', 'pending command result', timestamp=4)
        self.assertFalse(store.flush())
        backup = store.recover(confirmed=True)
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(store.load_messages(), [{'role': 'assistant', 'content': 'pending command result'}])

    def test_append_after_close_cannot_appear_successfully_queued(self):
        store = self.store()
        store.append('user', 'saved before shutdown', timestamp=1)
        self.assertTrue(store.close())
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'history is closed'):
            store.append('assistant', 'cannot be saved')
        self.assertEqual(self.path.read_bytes(), before)

    def test_recovery_cannot_discard_another_writer_queued_before_reset(self):
        store = self.store()
        store.create_session('Before recovery')
        entered, release = threading.Event(), threading.Event()
        save = HistoryStore.save_entries

        def delayed(path, entries, max_messages=1000, session_id=None):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test writer not released')
            return save(path, entries, max_messages, session_id)

        with patch.object(HistoryStore, 'save_entries', side_effect=delayed):
            store.append('user', 'already queued', timestamp=1)
            self.assertTrue(entered.wait(5))
            try:
                backup = HistoryStore.recover_file(self.path, confirmed=True)
            finally:
                release.set()
            self.assertTrue(store.flush(5))
        self.assertEqual(json.loads(backup.read_text())['sessions'][0]['messages'], [])
        self.assertEqual(store.load_messages(), [{'role': 'user', 'content': 'already queued'}])
        reopened = self.store()
        self.assertEqual(store.active_session_id, reopened.active_session_id)

    def test_corrupt_automatic_quarantine_is_private_and_durable_before_replacement(self):
        raw = b'{broken'
        self.path.write_bytes(raw)
        replace = os.replace

        def publish(source, target):
            backup, = self.directory.glob('*.corrupt-*')
            self.assertEqual(backup.read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            replace(source, target)

        with patch('src.storage.os.replace', side_effect=publish):
            update_json(self.path, lambda old: ['repaired'], [], max_bytes=1024)
        self.assertEqual(read_json(self.path, 1024), ['repaired'])

    def test_failed_corrupt_backup_sync_never_replaces_original(self):
        raw = b'{broken'
        self.path.write_bytes(raw)
        with patch('src.storage.os.fsync', side_effect=OSError('backup sync failed')):
            with self.assertRaises(OSError):
                update_json(self.path, lambda old: ['repaired'], [], max_bytes=1024)
        self.assertEqual(self.path.read_bytes(), raw)


class TestHistoryRecoveryCLI(unittest.TestCase):
    def test_parser_preserves_existing_history_syntax(self):
        parsed = CLIApp.parse_args(['history', '--limit', '4', '--clear'])
        self.assertIsNone(parsed.history_action)
        self.assertTrue(parsed.clear)
        self.assertEqual(parsed.limit, 4)

    def test_recovery_runs_without_constructing_app_or_loading_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            raw = b'{"version":999,"future":true}'
            path.write_bytes(raw)
            with patch('sys.argv', ['linux-ai', 'history', 'recover', '--yes']), \
                    patch('src.cli.CLIApp.__init__', side_effect=AssertionError('Loaded application before recovery')), \
                    patch.object(CLIApp, '_history_file', return_value=path), \
                    patch('src.setup_file_logging'), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    main()
            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(Path(output.getvalue().strip()).read_bytes(), raw)
            self.assertEqual(read_json(path, 1024)['version'], 1)

    def test_recovery_requires_yes_before_constructing_app(self):
        with patch('sys.argv', ['linux-ai', 'history', 'recover']), \
                patch('src.cli.CLIApp.__init__', side_effect=AssertionError('Loaded app')), \
                patch('src.cli.HistoryStore.recover_file') as recover, patch('src.setup_file_logging'), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 1)
        recover.assert_not_called()
        self.assertIn('--yes', output.getvalue())

    def test_journal_archive_and_archived_list_are_explicit(self):
        app = CLIApp.__new__(CLIApp)
        app.config = Mock()
        journal = Mock()
        journal.archive.return_value = {'archive_id': 'private', 'count': 2}
        journal.list_changes.return_value = [{'id': 'saved', 'status': 'applied'}]
        with patch('src.cli.ChangeJournal', return_value=journal), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.handle_changes(CLIApp.parse_args(['changes', 'archive'])), 1)
            journal.archive.assert_not_called()
            self.assertEqual(app.handle_changes(CLIApp.parse_args(['changes', 'archive', '--yes'])), 0)
            journal.archive.assert_called_once_with()
            self.assertEqual(app.handle_changes(CLIApp.parse_args(['changes', 'list', '--archived'])), 0)
            journal.list_changes.assert_called_with(include_archived=True)
            self.assertEqual(app.handle_changes(CLIApp.parse_args(['changes', 'show', 'saved'])), 0)
            journal.list_changes.assert_called_with(include_archived=True)


if __name__ == '__main__':
    unittest.main()
