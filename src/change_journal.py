"""Private journal for approved file writes, with guarded, backed-up recovery.

The journal contains metadata and hashes, never file contents. It is not an
authorization boundary: permissions, file identity and digests are rechecked
for every write/recovery. Backups are retained alongside the affected file.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .privileged_write import digest_fd, write_file, remove_file
from .storage import atomic_json_write, json_lock

MAX_RECORDS = 500
MAX_JOURNAL_BYTES = 2 * 1024 * 1024


def valid_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def file_digest(path):
    """Reject links/non-files; distinguish absence from unreadable contents."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('A regular file is required')
        return digest_fd(fd)
    finally:
        os.close(fd)


class ChangeJournal:
    def __init__(self, path=None):
        self.path = Path(path or Path.home() / '.config/linux_ai_assistant/changes.json')

    def _read(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_JOURNAL_BYTES + 1)
        except FileNotFoundError:
            return {'version': 1, 'changes': []}
        if len(raw) > MAX_JOURNAL_BYTES:
            raise ValueError('Change journal exceeds 2 MiB')
        doc = json.loads(raw)
        if (type(doc) is not dict or doc.get('version') != 1 or type(doc.get('changes')) is not list
                or len(doc['changes']) > MAX_RECORDS):
            raise ValueError('Invalid change journal; original file preserved')
        seen = set()
        for record in doc['changes']:
            if (type(record) is not dict or not isinstance(record.get('id'), str)
                    or record['id'] in seen or not isinstance(record.get('path'), str)
                    or not os.path.isabs(record['path']) or os.path.normpath(record['path']) != record['path']
                    or record.get('status') not in ('pending', 'applied', 'restored', 'failed')
                    or not valid_digest(record.get('after'))
                    or (record.get('before') is not None and not valid_digest(record['before']))
                    or not isinstance(record.get('created_at'), str) or len(record['created_at']) > 64
                    or not isinstance(record.get('session_id'), str)
                    or (record.get('backup') is not None and not isinstance(record['backup'], str))):
                raise ValueError('Invalid change record; journal preserved')
            seen.add(record['id'])
        return doc

    def list_changes(self, session_id=None):
        with json_lock(self.path):
            records = self._read()['changes']
        return [dict(item) for item in reversed(records)
                if session_id is None or item.get('session_id') == session_id]

    @staticmethod
    def _policy(path, allowed_dirs):
        from .file_actions import is_allowed_path
        allowed_dirs = allowed_dirs() if callable(allowed_dirs) else allowed_dirs
        if not os.path.isabs(path) or os.path.realpath(path) != path or not is_allowed_path(path, allowed_dirs):
            raise PermissionError('Path not allowed or changed: ' + path)

    @staticmethod
    def _identity(path):
        info = os.stat(os.path.dirname(path), follow_symlinks=False)
        return info.st_dev, info.st_ino

    @staticmethod
    def _write(source, path, before, identity, source_hash):
        from .file_actions import _write_privileged, is_privileged_path
        if is_privileged_path(path):
            return _write_privileged(source, path, before, identity, source_hash)
        return write_file(source, path, before, identity, source_hash)

    def apply(self, source, path, expected_digest, allowed_dirs=None, session_id='', parent_identity=None):
        """Caller must confirm the preview first. Reserve a journal record before I/O."""
        self._policy(path, allowed_dirs)
        after = file_digest(source)
        if after is None:
            raise ValueError('Missing input file')
        identity = parent_identity or self._identity(path)
        with json_lock(self.path):
            doc = self._read()
            if len(doc['changes']) >= MAX_RECORDS:
                # Never silently discard recovery records.
                raise ValueError('Change journal is full; archive it before further writes')
            if file_digest(path) != expected_digest:
                raise PermissionError('File changed since the preview')
            record = {'id': uuid.uuid4().hex, 'path': path, 'before': expected_digest,
                      'after': after, 'backup': None, 'status': 'pending', 'session_id': session_id,
                      'created_at': datetime.now(timezone.utc).isoformat()}
            doc['changes'].append(record)
            atomic_json_write(self.path, doc)
            try:
                self._policy(path, allowed_dirs)
                record['backup'] = self._write(source, path, expected_digest, identity, after)
            except Exception:
                record['status'] = 'failed'
                atomic_json_write(self.path, doc)
                raise
            record['status'] = 'applied'
            try:
                atomic_json_write(self.path, doc)
            except OSError as error:
                raise OSError('File written, but journal finalization failed; keep the adjacent backup and review the pending record: ' + str(error)) from error
            return dict(record)

    def _restorable(self, record, allowed_dirs):
        from .file_actions import is_privileged_path
        if record['status'] != 'applied':
            raise ValueError('Only completed, unrecovered changes can be restored')
        path = record['path']
        self._policy(path, allowed_dirs)
        verified = True
        try:
            current_digest = file_digest(path)
        except PermissionError:
            if not is_privileged_path(path):
                raise
            verified = False
        else:
            if current_digest != record['after']:
                raise PermissionError('File changed after this operation; recovery refused')
        if record['before'] is not None:
            backup = record.get('backup')
            if (not isinstance(backup, str) or os.path.dirname(backup) != os.path.dirname(path) or backup == path):
                raise PermissionError('Original backup missing or changed; recovery refused')
            try:
                original_digest = file_digest(backup)
            except PermissionError:
                if not is_privileged_path(path):
                    raise
                verified = False
            else:
                if original_digest != record['before']:
                    raise PermissionError('Original backup missing or changed; recovery refused')
        return verified

    def preview_restore(self, identifier, allowed_dirs=None):
        record = next((item for item in self.list_changes() if item['id'] == identifier), None)
        if record is None:
            raise KeyError('Unknown change')
        record['parent_identity'] = self._identity(record['path'])
        if not self._restorable(record, allowed_dirs):
            from .file_actions import _inspect_privileged
            # Preview requires authentication for unreadable private system files.
            # Recovery rechecks both hashes in the authenticated write helper.
            preview = _inspect_privileged(record['path'], record['after'], record['parent_identity'],
                                          record['backup'], record['before'])
            record.update(preview_content=preview['content'], preview_truncated=preview['truncated'])
        return record

    def restore(self, identifier, allowed_dirs=None, parent_identity=None):
        """Caller must approve recovery; revalidate everything under the journal lock."""
        with json_lock(self.path):
            doc = self._read()
            record = next((item for item in doc['changes'] if item['id'] == identifier), None)
            if record is None:
                raise KeyError('Unknown change')
            self._restorable(record, allowed_dirs)
            path = record['path']
            identity = parent_identity or self._identity(path)
            if record['before'] is None:
                from .file_actions import _remove_privileged, is_privileged_path
                backup = (_remove_privileged(path, record['after'], identity) if is_privileged_path(path)
                          else remove_file(path, record['after'], identity))
            else:
                backup = self._write(record['backup'], path, record['after'], identity, record['before'])
            record.update(status='restored', recovery_backup=backup,
                          restored_at=datetime.now(timezone.utc).isoformat())
            try:
                atomic_json_write(self.path, doc)
            except OSError as error:
                raise OSError('Recovery completed, but journal update failed; retain the recovery backup: ' + str(error)) from error
            return dict(record)


def content_digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
