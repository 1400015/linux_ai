"""Distinguish a failed JSON write from failure after atomic publication."""

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from src.storage import JsonWriteCommittedError, atomic_json_write, update_json


@contextmanager
def sidecar_close_failure(path, error):
    original_open, original_close = os.open, os.close
    lock_fds, closed = set(), []

    def open_descriptor(opened_path, flags, *args, **kwargs):
        fd = original_open(opened_path, flags, *args, **kwargs)
        if str(opened_path) == str(path) + '.lock':
            lock_fds.add(fd)
        return fd

    def close_descriptor(fd):
        original_close(fd)
        if fd in lock_fds:
            lock_fds.remove(fd)
            closed.append(fd)
            raise error

    with patch('src.storage.os.open', side_effect=open_descriptor), \
            patch('src.storage.os.close', side_effect=close_descriptor):
        yield closed


class TestStoragePublication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'document.json'
        self.previous, self.next = {'count': 1}, {'count': 2}
        self.path.write_text(json.dumps(self.previous), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def assert_closed(self, descriptors):
        self.assertTrue(descriptors)
        for fd in descriptors:
            with self.assertRaises(OSError) as raised:
                os.fstat(fd)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def assert_committed(self, error, cause):
        self.assertIsInstance(error, OSError)
        self.assertIs(error.__cause__, cause)
        self.assertEqual(error.errno, cause.errno)
        self.assertIs(error.value, self.next)
        self.assertEqual(self.read(), self.next)

    def test_file_fsync_failure_remains_unpublished(self):
        failure = OSError(errno.EIO, 'file synchronization failed')
        with patch('src.storage.os.fsync', side_effect=failure):
            with self.assertRaises(OSError) as raised:
                atomic_json_write(self.path, self.next)
        self.assertIs(raised.exception, failure)
        self.assertNotIsInstance(raised.exception, JsonWriteCommittedError)
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_replace_failure_remains_unpublished(self):
        failure = OSError(errno.EACCES, 'replace denied')
        with patch('src.storage.os.replace', side_effect=failure):
            with self.assertRaises(OSError) as raised:
                atomic_json_write(self.path, self.next)
        self.assertIs(raised.exception, failure)
        self.assertNotIsInstance(raised.exception, JsonWriteCommittedError)
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_directory_open_failure_carries_published_value(self):
        original_open = os.open
        failure = OSError(errno.EIO, 'directory open failed')

        def open_descriptor(path, flags, *args, **kwargs):
            if flags & os.O_DIRECTORY:
                raise failure
            return original_open(path, flags, *args, **kwargs)

        with patch('src.storage.os.open', side_effect=open_descriptor):
            with self.assertRaises(JsonWriteCommittedError) as raised:
                atomic_json_write(self.path, self.next)
        self.assert_committed(raised.exception, failure)

    def test_directory_fsync_failure_carries_published_value(self):
        original_fsync = os.fsync
        failure = OSError(errno.EIO, 'directory synchronization failed')

        def synchronize(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise failure
            return original_fsync(fd)

        with patch('src.storage.os.fsync', side_effect=synchronize):
            with self.assertRaises(JsonWriteCommittedError) as raised:
                atomic_json_write(self.path, self.next)
        self.assert_committed(raised.exception, failure)

    def test_directory_close_failure_carries_published_value_without_leaking_fd(self):
        original_close = os.close
        failure = OSError(errno.EIO, 'directory close failed')
        closed = []

        def close_descriptor(fd):
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            original_close(fd)
            if directory:
                closed.append(fd)
                raise failure

        with patch('src.storage.os.close', side_effect=close_descriptor):
            with self.assertRaises(JsonWriteCommittedError) as raised:
                atomic_json_write(self.path, self.next)
        self.assert_committed(raised.exception, failure)
        self.assert_closed(closed)

    def test_secondary_temporary_cleanup_failure_preserves_primary_write_error(self):
        failure = OSError(errno.EACCES, 'replace denied')
        cleanup = OSError(errno.EIO, 'temporary cleanup failed')
        with patch('src.storage.os.replace', side_effect=failure), \
                patch('src.storage.os.unlink', side_effect=cleanup):
            with self.assertRaises(OSError) as raised:
                atomic_json_write(self.path, self.next)
        self.assertIs(raised.exception, failure)
        self.assertNotIsInstance(raised.exception, JsonWriteCommittedError)
        self.assertIs(raised.exception.temporary_cleanup_error, cleanup)
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(len(list(self.directory.glob('*.tmp'))), 1)

    def test_secondary_cleanup_preserves_a_non_io_serialization_error(self):
        failure = ValueError('invalid fixture data')
        cleanup = OSError(errno.EIO, 'temporary cleanup failed')
        with patch('src.storage.json.dump', side_effect=failure), \
                patch('src.storage.os.unlink', side_effect=cleanup):
            with self.assertRaises(ValueError) as raised:
                atomic_json_write(self.path, self.next)
        self.assertIs(raised.exception, failure)
        self.assertIs(raised.exception.temporary_cleanup_error, cleanup)
        self.assertEqual(self.read(), self.previous)

    def test_reused_temporary_name_is_not_removed_after_publication(self):
        original_replace = os.replace
        reused = []

        def replace(source, destination):
            original_replace(source, destination)
            path = Path(source)
            path.write_bytes(b'unrelated new file')
            reused.append(path)

        with patch('src.storage.os.replace', side_effect=replace):
            atomic_json_write(self.path, self.next)
        self.assertEqual(self.read(), self.next)
        self.assertEqual(reused[0].read_bytes(), b'unrelated new file')

    def test_committed_error_message_and_args_never_contain_the_value(self):
        value = {'secret': 'PRIVATE-PUBLISHED-VALUE'}
        failure = OSError(errno.EIO, 'directory synchronization failed')
        error = JsonWriteCommittedError(value, failure)
        self.assertIs(error.value, value)
        self.assertEqual(error.errno, errno.EIO)
        self.assertNotIn('PRIVATE-PUBLISHED-VALUE', str(error))
        self.assertNotIn('PRIVATE-PUBLISHED-VALUE', repr(error.args))

    def test_sidecar_close_after_success_carries_published_transaction_result(self):
        failure = OSError(errno.EIO, 'sidecar close failed')
        with sidecar_close_failure(self.path, failure) as closed:
            with self.assertRaises(JsonWriteCommittedError) as raised:
                update_json(self.path, lambda previous: self.next, {})
        self.assert_committed(raised.exception, failure)
        self.assert_closed(closed)

    def test_sidecar_close_during_committed_error_unwind_keeps_publication_metadata(self):
        original_fsync = os.fsync
        synchronization = OSError(errno.EIO, 'directory synchronization failed')
        close = OSError(errno.EIO, 'sidecar close failed')

        def synchronize(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise synchronization
            return original_fsync(fd)

        with sidecar_close_failure(self.path, close) as closed, \
                patch('src.storage.os.fsync', side_effect=synchronize):
            with self.assertRaises(JsonWriteCommittedError) as raised:
                update_json(self.path, lambda previous: self.next, {})
        self.assert_committed(raised.exception, close)
        self.assertIsInstance(close.__context__, JsonWriteCommittedError)
        self.assertIs(close.__context__.value, self.next)
        self.assertIs(close.__context__.__cause__, synchronization)
        self.assert_closed(closed)

    def test_sidecar_close_before_publication_does_not_claim_commit(self):
        writing = OSError(errno.EACCES, 'replace denied')
        close = OSError(errno.EIO, 'sidecar close failed')
        with sidecar_close_failure(self.path, close) as closed, \
                patch('src.storage.os.replace', side_effect=writing):
            with self.assertRaises(OSError) as raised:
                update_json(self.path, lambda previous: self.next, {})
        self.assertIs(raised.exception, close)
        self.assertNotIsInstance(raised.exception, JsonWriteCommittedError)
        self.assertEqual(self.read(), self.previous)
        self.assert_closed(closed)

    def test_null_json_remains_a_published_result_on_lock_close_failure(self):
        failure = OSError(errno.EIO, 'sidecar close failed')
        with sidecar_close_failure(self.path, failure) as closed:
            with self.assertRaises(JsonWriteCommittedError) as raised:
                update_json(self.path, lambda previous: None, {})
        self.assertIsNone(raised.exception.value)
        self.assertIsNone(self.read())
        self.assertIs(raised.exception.__cause__, failure)
        self.assert_closed(closed)


if __name__ == '__main__':
    unittest.main()
