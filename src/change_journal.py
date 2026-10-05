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

from .privileged_write import digest_fd, planned_backup_path, write_file, remove_file
from .storage import atomic_json_write, json_lock, read_json

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

    def _read(self, path=None):
        path = self.path if path is None else path
        try:
            doc = read_json(path, MAX_JOURNAL_BYTES)
        except FileNotFoundError:
            return {'version': 1, 'changes': []}
        if (type(doc) is not dict or doc.get('version') != 1 or type(doc.get('changes')) is not list
                or len(doc['changes']) > MAX_RECORDS):
            raise ValueError('Invalid change journal; original file preserved')
        seen = set()
        for record in doc['changes']:
            if (type(record) is not dict or not isinstance(record.get('id'), str)
                    or record['id'] in seen or not isinstance(record.get('path'), str)
                    or not os.path.isabs(record['path']) or os.path.normpath(record['path']) != record['path']
                    or record.get('status') not in ('pending', 'applied', 'restored', 'failed',
                                                    'uncertain', 'recovery_pending', 'recovery_uncertain')
                    or not valid_digest(record.get('after'))
                    or (record.get('before') is not None and not valid_digest(record['before']))
                    or not isinstance(record.get('created_at'), str) or len(record['created_at']) > 64
                    or not isinstance(record.get('session_id'), str)
                    or (record.get('backup') is not None and not isinstance(record['backup'], str))):
                raise ValueError('Invalid change record; journal preserved')
            seen.add(record['id'])
        return doc

    @staticmethod
    def _check_size(doc, reserve=0):
        # Match atomic_json_write's encoding before touching the target. The
        # reservation covers bounded error metadata and the longer final status.
        size = len(json.dumps(doc, indent=2, ensure_ascii=False).encode('utf-8'))
        if size + reserve > MAX_JOURNAL_BYTES:
            raise ValueError('Change journal exceeds 2 MiB; archive it before further writes')

    def _persist(self, path, doc):
        self._check_size(doc)
        atomic_json_write(path, doc)

    @property
    def archive_directory(self):
        return self.path.with_name(self.path.name + '.archives')

    def _archive_paths(self):
        directory = self.archive_directory
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return []
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('Archive directory must be private (0700), owned and not a link')
        paths = []
        for path in sorted(directory.glob('*.json')):
            identifier = path.stem
            if len(identifier) != 32 or any(char not in '0123456789abcdef' for char in identifier):
                raise ValueError('Invalid change archive name')
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PermissionError('Change archive must be an owned regular file')
            paths.append(path)
        return paths

    def archive(self):
        """Move terminal records into a private, ID-addressable journal archive.

        A rename under the ordinary journal lock leaves every record in exactly
        one file. Backups remain adjacent to their targets. A missing active
        journal means an empty journal and is recreated by the next write.
        """
        with json_lock(self.path):
            doc = self._read()
            if not doc['changes']:
                raise ValueError('No changes to archive')
            if any(item['status'] in ('pending', 'recovery_pending') for item in doc['changes']):
                raise ValueError('Unfinished operations must be reviewed before archiving')
            self.archive_directory.mkdir(mode=0o700, exist_ok=True)
            self._archive_paths()
            identifier = uuid.uuid4().hex
            destination = self.archive_directory / (identifier + '.json')
            if destination.exists():
                raise FileExistsError('Change archive already exists')
            os.replace(str(self.path), str(destination))
            try:
                for directory in (self.archive_directory, self.path.parent):
                    descriptor = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            except OSError as error:
                raise OSError('Changes were archived at {}, but durability could not be confirmed: {}'.format(
                    destination, error)) from error
            return {'archive_id': identifier, 'path': str(destination), 'count': len(doc['changes'])}

    def list_changes(self, session_id=None, include_archived=False):
        with json_lock(self.path):
            records = []
            if include_archived:
                for path in self._archive_paths():
                    records.extend(dict(item, archive_id=path.stem) for item in self._read(path)['changes'])
            records.extend(self._read()['changes'])
            if include_archived:
                records.sort(key=lambda item: item['created_at'])
            if len({item['id'] for item in records}) != len(records):
                raise ValueError('Duplicate change IDs; journals preserved')
        return [dict(item) for item in reversed(records)
                if session_id is None or item.get('session_id') == session_id]

    def _find(self, identifier):
        matches = []
        for path in [self.path] + self._archive_paths():
            doc = self._read(path)
            for record in doc['changes']:
                if record['id'] == identifier:
                    matches.append((path, doc, record))
        if not matches:
            raise KeyError('Unknown change')
        if len(matches) != 1:
            raise ValueError('Duplicate change IDs; journals preserved')
        return matches[0]

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
    def _write(source, path, before, identity, source_hash, backup_path=None):
        from .file_actions import _write_privileged, is_privileged_path
        if is_privileged_path(path):
            return _write_privileged(source, path, before, identity, source_hash, backup_path)
        return write_file(source, path, before, identity, source_hash, backup_path)

    def _failed_operation(self, journal_path, doc, record, error, recovery=False):
        outcome = getattr(error, 'outcome', None)
        published = outcome.get('published') if outcome else False
        if recovery:
            record['status'] = 'applied' if published is False else 'recovery_uncertain'
            if outcome and outcome.get('backup'):
                record['recovery_backup'] = outcome['backup']
        else:
            record['status'] = 'failed' if published is False else 'uncertain'
            record['published'] = published
            if outcome and outcome.get('backup'):
                record['backup'] = outcome['backup']
        if outcome and outcome.get('cleanup'):
            record['cleanup'] = outcome['cleanup']
        try:
            self._persist(journal_path, doc)
        except OSError as journal_error:
            raise OSError('File operation failed and journal finalization failed; revalidate the target and '
                          'keep the reserved adjacent backup: ' + str(journal_error)) from error

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
                      'after': after,
                      'backup': planned_backup_path(path) if expected_digest is not None else None,
                      'published': None, 'status': 'pending', 'session_id': session_id,
                      'created_at': datetime.now(timezone.utc).isoformat()}
            doc['changes'].append(record)
            self._check_size(doc, reserve=2 * len(json.dumps(path, ensure_ascii=False).encode('utf-8')) + 256)
            self._persist(self.path, doc)
            try:
                self._policy(path, allowed_dirs)
                record['backup'] = self._write(source, path, expected_digest, identity, after, record['backup'])
            except Exception as error:
                self._failed_operation(self.path, doc, record, error)
                raise
            record['status'] = 'applied'
            record['published'] = True
            try:
                self._persist(self.path, doc)
            except OSError as error:
                raise OSError('File written, but journal finalization failed; keep the adjacent backup and review the pending record: ' + str(error)) from error
            return dict(record)

    def _restorable(self, record, allowed_dirs):
        from .file_actions import is_privileged_path
        if record['status'] not in ('applied', 'pending', 'uncertain'):
            raise ValueError('Only unrecovered writes with verified target and backup can be restored')
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
        with json_lock(self.path):
            _, _, stored = self._find(identifier)
            record = dict(stored)
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
            journal_path, doc, record = self._find(identifier)
            self._restorable(record, allowed_dirs)
            path = record['path']
            identity = parent_identity or self._identity(path)
            recovery_backup = planned_backup_path(path, removal=record['before'] is None)
            record.update(status='recovery_pending', recovery_backup=recovery_backup,
                          restored_at=datetime.now(timezone.utc).isoformat())
            self._check_size(doc, reserve=2 * len(json.dumps(path, ensure_ascii=False).encode('utf-8')) + 256)
            self._persist(journal_path, doc)
            try:
                if record['before'] is None:
                    from .file_actions import _remove_privileged, is_privileged_path
                    backup = (_remove_privileged(path, record['after'], identity, recovery_backup)
                              if is_privileged_path(path)
                              else remove_file(path, record['after'], identity, recovery_backup))
                else:
                    backup = self._write(record['backup'], path, record['after'], identity,
                                         record['before'], recovery_backup)
            except Exception as error:
                self._failed_operation(journal_path, doc, record, error, recovery=True)
                raise
            record.update(status='restored', recovery_backup=backup,
                          restored_at=datetime.now(timezone.utc).isoformat())
            try:
                self._persist(journal_path, doc)
            except OSError as error:
                raise OSError('Recovery completed, but journal update failed; retain the recovery backup: ' + str(error)) from error
            return dict(record)


def content_digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
