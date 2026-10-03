"""Local trial sessions. Recorded data never authorizes or replays an action.

Only context and selected bounded sources are read. Originals stay untouched;
exports contain a reviewed snapshot, not a recursive copy of a directory.
"""

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import struct
import time
import uuid
import zipfile
import zlib

from ._version import __version__
from .action_audit import MAX_EVENTS
from .build_info import BuildObservation, observe_checkout_build
from .diagnostics import redact
from .log_privacy import redact_argv, secret_option
from .schema_validation import validate_schema
from .system_context import detect_system_context

RESULTS = ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'N/A')
TEST_TYPES = ('unknown', 'physical', 'vm', 'wsl', 'fixture')
MODES = ('unknown', 'auto', 'offline', 'local', 'remote')
MAX_RUNS = 100
MAX_CASES = 100
MAX_RUN_BYTES = 8 * 1024 * 1024
MAX_CASE_BYTES = 320 * 1024
MAX_EVENT_BYTES = 128 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
MAX_ATTACHMENTS = 30
MAX_IMAGE_PIXELS = 20_000_000
_IDENTIFIER = re.compile(r'^(?:run|case|att)-[a-f0-9]{32}$')
_CASE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
_SECRET_KEY = re.compile(r'(?i)(?:password|passwd|pwd|passphrase|token|api[_-]?key|secret|authorization|cookie|encryption[_-]?key)')
_TEXT_EXTENSIONS = {'.txt', '.log', '.md', '.csv', '.json'}
_NOTICE = ('Automatic redaction is partial. Review text, image pixels and image metadata before sharing. '
           'Logs are global to the application, not isolated to a conversation. Missing events do not prove '
           'that no effect occurred. Operator results are not automatic verification. No data is uploaded.')


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _clean(text, limit=4096):
    if not isinstance(text, str) or len(text) > limit:
        raise ValueError('Text exceeds its limit or is not a string')
    value = redact(text)
    home = str(Path.home())
    return value.replace(home, '[home]') if len(home) > 1 else value


def _sanitized(value, depth=0):
    if depth > 20:
        raise ValueError('Evidence is nested too deeply')
    if isinstance(value, str):
        return _clean(value, MAX_TEXT_BYTES)
    if value is None or type(value) in (bool, int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError('Invalid evidence timestamp')
        return value
    if type(value) is list:
        if len(value) > 2048:
            raise ValueError('Too many evidence entries')
        if all(isinstance(item, str) for item in value):
            if any(len(item) > MAX_TEXT_BYTES for item in value):
                raise ValueError('Text exceeds its limit')
            return [_clean(item, MAX_TEXT_BYTES) for item in redact_argv(value)]
        items, hide_next = [], False
        for item in value:
            items.append('[redacted]' if hide_next else _sanitized(item, depth + 1))
            hide_next = secret_option(item)
        return items
    if type(value) is dict:
        if len(value) > 2048 or any(not isinstance(key, str) for key in value):
            raise ValueError('Invalid evidence object')
        return {_clean(key, 256): '[redacted]' if _SECRET_KEY.search(key)
                else _sanitized(item, depth + 1) for key, item in value.items()}
    raise ValueError('Unsupported evidence value')


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _markdown(value):
    text = html.escape(str(value), quote=False)
    return re.sub(r'([\\`*{}\[\]()#+.!_|>~-])', r'\\\1', text).replace('\n', ' / ')


def _csv_cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def _text_artifact(data, suffix):
    try:
        text = data.decode('utf-8')
        if suffix == '.json':
            return _json_bytes(_sanitized(json.loads(text)))
        if suffix == '.csv':
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            for index, row in enumerate(csv.reader(io.StringIO(text, newline=''))):
                if index >= 10000 or len(row) > 256:
                    raise ValueError('CSV attachment exceeds row or column limits')
                writer.writerow([_csv_cell(_clean(cell, MAX_TEXT_BYTES)) for cell in row])
            return stream.getvalue().encode('utf-8')
        return _clean(text, MAX_TEXT_BYTES).encode('utf-8')
    except (UnicodeError, RecursionError, csv.Error) as error:
        raise ValueError('Attachment must be bounded UTF-8 text with valid JSON/CSV when selected') from error


def _parents(path):
    """Reject directory symlinks as well as a symlink at the leaf."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for parent in reversed(path.parents):
        metadata = parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError('A real directory is required; symlink parents are not accepted')
    return path


def _directory_fd(path):
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_directory(path):
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        metadata = os.fstat(fd)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError('Trial directory must be owned by this user and private (0700)')
    finally:
        os.close(fd)
    return path


def _open_regular(path, private=False):
    path = _parents(path)
    parent_fd = _directory_fd(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    metadata = os.fstat(fd)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1 or (private and metadata.st_mode & 0o077)):
        os.close(fd)
        raise ValueError('Evidence must be a regular owned file, without links; stored data must be private')
    return fd, metadata


def _read_bytes(path, limit, private=False):
    fd, before = _open_regular(path, private)
    with os.fdopen(fd, 'rb') as stream:
        if before.st_size > limit:
            raise ValueError('Evidence exceeds its size limit')
        value = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(value) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError('Evidence changed while it was read')
    current = Path(path).lstat()
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        raise ValueError('Evidence identity changed')
    return value


def _write_new(path, data):
    path = _parents(path)
    parent_fd = _directory_fd(path.parent)
    created = None
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        created = os.fstat(fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent_fd)
    except BaseException:
        if created is not None:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                os.unlink(path.name, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)
    return path


def _atomic_store(path, value):
    """Private metadata replacement, anchored to the checked directory descriptor."""
    parent_fd = _directory_fd(_private_directory(path.parent))
    temporary = '.trial-tmp-' + uuid.uuid4().hex
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _image_dimensions(data, extension):
    if extension == '.png':
        if (len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n'
                or data[8:16] != b'\0\0\0\rIHDR'
                or zlib.crc32(data[12:29]) & 0xffffffff != struct.unpack('>I', data[29:33])[0]):
            raise ValueError('Invalid PNG header')
        width, height = struct.unpack('>II', data[16:24])
    else:
        if not data.startswith(b'\xff\xd8'):
            raise ValueError('Invalid JPEG header')
        cursor, width, height = 2, 0, 0
        while cursor + 3 < len(data):
            if data[cursor] != 255:
                raise ValueError('Invalid JPEG marker')
            while cursor < len(data) and data[cursor] == 255:
                cursor += 1
            if cursor >= len(data):
                break
            marker = data[cursor]
            cursor += 1
            if marker in (0xda, 0xd9):
                break
            if marker == 0x01 or 0xd0 <= marker <= 0xd8:
                continue
            if cursor + 2 > len(data):
                break
            size = struct.unpack('>H', data[cursor:cursor + 2])[0]
            if size < 2 or cursor + size > len(data):
                raise ValueError('Invalid JPEG segment')
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                if size < 8:
                    raise ValueError('Invalid JPEG dimensions')
                height, width = struct.unpack('>HH', data[cursor + 3:cursor + 7])
                break
            cursor += size
    if not 0 < width <= 12000 or not 0 < height <= 12000 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError('Image exceeds dimension limits (12000 per side, 20 million pixels)')
    return width, height


class TrialRecorder:
    def __init__(self, root=None, *, history_path=None, log_path=None,
                 context_factory=None, clock=time.time, defaults=None, settings_factory=None,
                 build_observer=None):
        self.root = _private_directory(root or Path.home() / '.local/share/linux_ai_assistant/trials')
        self.index = self.root / 'index.json'
        self.history_path = Path(history_path or Path.home() / '.config/linux_ai_assistant/history.json')
        self.audit_path = self.history_path.with_name('actions.json')
        self.journal_path = self.history_path.with_name('changes.json')
        self.log_path = Path(log_path or Path.home() / '.cache/linux_ai_assistant/app.log')
        self.context_factory = context_factory or (lambda: detect_system_context().to_dict())
        self.clock = clock
        self.defaults = defaults or {}
        self.settings_factory = settings_factory
        self.build_observer = build_observer or observe_checkout_build

    @contextmanager
    def _locked(self):
        import fcntl
        _private_directory(self.root)
        parent_fd = _directory_fd(self.root)
        try:
            fd = os.open('index.json.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077 or metadata.st_nlink != 1):
                raise ValueError('Invalid private trial lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _now(self):
        value = float(self.clock())
        if not math.isfinite(value) or not 0 <= value < 253402300800:
            raise ValueError('Invalid clock')
        return value

    def _index(self):
        try:
            value = json.loads(_read_bytes(self.index, 4096, private=True))
        except FileNotFoundError:
            return {'version': 1, 'selected': None}
        if (type(value) is not dict or set(value) != {'version', 'selected'} or value['version'] != 1
                or (value['selected'] is not None and not self._valid_id(value['selected'], 'run'))):
            raise ValueError('Invalid trial index; original preserved')
        return value

    @staticmethod
    def _valid_id(value, prefix):
        return isinstance(value, str) and value.startswith(prefix + '-') and bool(_IDENTIFIER.fullmatch(value))

    def _run_dir(self, identifier):
        if not self._valid_id(identifier, 'run'):
            raise ValueError('Invalid trial identity')
        return _private_directory(self.root / identifier)

    def _load(self, identifier):
        # Do not create a missing run directory while reading a user-supplied ID.
        if not self._valid_id(identifier, 'run') or not (self.root / identifier).exists():
            raise ValueError('Unknown trial')
        path = self._run_dir(identifier) / 'run.json'
        try:
            value = json.loads(_read_bytes(path, MAX_RUN_BYTES, private=True))
        except (RecursionError, UnicodeError) as error:
            raise ValueError('Invalid trial data; original preserved') from error
        if (type(value) is not dict or value.get('format') != 'linux-ai-trial'
                or value.get('version') != 1 or value.get('id') != identifier
                or type(value.get('cases')) is not list or len(value['cases']) > MAX_CASES
                or type(value.get('attachments')) is not list or len(value['attachments']) > MAX_ATTACHMENTS):
            raise ValueError('Invalid trial data; original preserved')
        required = {'format', 'version', 'id', 'title', 'environment', 'build', 'mode', 'provider', 'model',
                    'test_type', 'started_at', 'finished_at', 'context', 'warnings', 'cases', 'attachments', 'notice'}
        if set(value) - {'_review_digest', '_retained_bytes'} != required or value['mode'] not in MODES or value['test_type'] not in TEST_TYPES:
            raise ValueError('Invalid trial fields; original preserved')
        if '_retained_bytes' in value and (type(value['_retained_bytes']) is not int
                                          or not 0 <= value['_retained_bytes'] <= MAX_ATTACHMENT_BYTES):
            raise ValueError('Invalid attachment storage accounting')
        build = value['build']
        build_fields = {'version', 'reference', 'python'}
        if (type(build) is not dict or set(build) not in (build_fields, build_fields | {'source', 'commit', 'worktree'})
                or any(not isinstance(item, str) for item in value['build'].values())
                or any(not isinstance(value[key], str) for key in ('title', 'environment', 'provider', 'model', 'notice'))):
            raise ValueError('Invalid trial metadata')
        if 'source' in build:
            if (build['source'] not in ('operator', 'checkout', 'unknown')
                    or build['worktree'] not in ('clean', 'dirty', 'unknown')
                    or (build['source'] == 'checkout' and not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', build['commit']))
                    or (build['source'] != 'checkout' and (build['commit'] or build['worktree'] != 'unknown'))):
                raise ValueError('Invalid build provenance')
            if build['source'] == 'checkout':
                suffix = {'clean': '', 'dirty': '-dirty', 'unknown': '-worktree-unknown'}[build['worktree']]
                if build['reference'] != build['commit'] + suffix:
                    raise ValueError('Build reference does not match observed provenance')
            elif build['source'] == 'unknown' and build['reference'] != 'unknown':
                raise ValueError('Unknown build provenance has an unexpected reference')
        for item in value['cases']:
            if (type(item) is not dict or not self._valid_id(item.get('id'), 'case')
                    or not isinstance(item.get('case_id'), str) or not _CASE_ID.fullmatch(item['case_id'])
                    or item.get('result') not in (*RESULTS, None)
                    or type(item.get('warnings')) is not list
                    or any(not isinstance(item.get(key), str) for key in ('session_id', 'variant', 'interface', 'notes', 'observations'))
                    or any(type(item.get(key)) is not list for key in ('events', 'changes'))
                    or (item.get('result') is None and type(item.get('_baseline')) is not dict)):
                raise ValueError('Invalid case metadata; original preserved')
            for timestamp in (item.get('started_at'), item.get('finished_at')):
                if timestamp is not None and (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                                               or not 0 <= timestamp < 253402300800):
                    raise ValueError('Invalid case timestamp')
        for item in value['attachments']:
            if (type(item) is not dict or not self._valid_id(item.get('id'), 'att')
                    or item.get('suffix') not in _TEXT_EXTENSIONS | {'.png', '.jpg'}
                    or type(item.get('bytes')) is not int or not 0 <= item['bytes'] <= MAX_IMAGE_BYTES
                    or not isinstance(item.get('name'), str)
                    or not isinstance(item.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
                raise ValueError('Invalid attachment metadata')
        return value

    def _selected(self):
        identifier = self._index()['selected']
        if identifier is None:
            raise ValueError('Start or select a trial first')
        return self._load(identifier)

    def _save(self, run, invalidate=True):
        if invalidate:
            run.pop('_review_digest', None)
        if len(_json_bytes(run)) > MAX_RUN_BYTES:
            raise ValueError('Trial is full; finish it and start another')
        _atomic_store(self._run_dir(run['id']) / 'run.json', run)

    @staticmethod
    def _public(value):
        if isinstance(value, dict):
            return {key: TrialRecorder._public(item) for key, item in value.items() if not key.startswith('_')}
        if isinstance(value, list):
            return [TrialRecorder._public(item) for item in value]
        return value

    @staticmethod
    def _open_case(run):
        cases = [case for case in run['cases'] if case.get('result') is None]
        if len(cases) > 1:
            raise ValueError('Invalid trial: multiple open cases')
        return cases[0] if cases else None

    def _context(self, warnings):
        try:
            value = _sanitized(self.context_factory())
            if type(value) is not dict or len(_json_bytes(value)) > 32 * 1024:
                raise ValueError('Context exceeds size limit')
            return value
        except Exception as error:
            warnings.append('Context unavailable: ' + _clean(str(error)[:1000]))
            return None

    def _settings(self, run, warnings):
        try:
            settings = (self.settings_factory() if self.settings_factory else
                        {key: run[key] for key in ('mode', 'provider', 'model')})
            if type(settings) is not dict or settings.get('mode') not in MODES:
                raise ValueError('Invalid assistance selection')
            return {'mode': settings['mode'], 'provider': _clean(settings.get('provider', ''), 128),
                    'model': _clean(settings.get('model', ''), 256)}
        except Exception as error:
            warnings.append('Assistance selection unavailable: ' + _clean(str(error)[:1000]))
            return {'mode': 'unknown', 'provider': '', 'model': ''}

    def _audit(self, session_id, warnings):
        try:
            # Atomic source writes allow a consistent read without creating a source lock.
            value = json.loads(_read_bytes(self.audit_path, 4 * 1024 * 1024, private=True))
            if type(value) is not list or len(value) > MAX_EVENTS:
                raise ValueError('Invalid audit collection')
            events = [validate_schema(event, 'audit-event') for event in value]
            if len(events) >= MAX_EVENTS:
                warnings.append('Audit reached its global 512-event limit; earlier events may be missing')
            return [event for event in events if event['session_id'] == session_id]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, RecursionError) as error:
            warnings.append('Audit unavailable: ' + _clean(str(error)[:1000]))
            return []

    def _journal(self, session_id, warnings):
        try:
            value = json.loads(_read_bytes(self.journal_path, 2 * 1024 * 1024, private=True))
            if (type(value) is not dict or value.get('version') != 1
                    or type(value.get('changes')) is not list or len(value['changes']) > 500):
                raise ValueError('Invalid change journal')
            records = []
            fields = ('id', 'session_id', 'path', 'created_at', 'before', 'after', 'backup', 'status')
            for item in value['changes']:
                if type(item) is not dict:
                    raise ValueError('Invalid change record')
                if item.get('session_id') == session_id:
                    records.append({key: item.get(key) for key in fields})
            return records
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, RecursionError) as error:
            warnings.append('Change journal unavailable: ' + _clean(str(error)[:1000]))
            return []

    def _log_files(self):
        return [Path(str(self.log_path) + '.' + str(number)) for number in (3, 2, 1)] + [self.log_path]

    def _log_baseline(self):
        markers = []
        for path in self._log_files():
            try:
                fd, metadata = _open_regular(path, private=True)
                with os.fdopen(fd, 'rb') as stream:
                    stream.seek(max(0, metadata.st_size - 128))
                    anchor = stream.read(128)
                markers.append({'dev': metadata.st_dev, 'ino': metadata.st_ino,
                                'size': metadata.st_size, 'anchor': _digest(anchor)})
            except (OSError, ValueError):
                # A missing log is normal; it can first appear during this case.
                continue
        return markers

    def _logs(self, baseline, warnings):
        parts, available = [], False
        markers = {(item['dev'], item['ino']): item for item in baseline}
        for path in self._log_files():
            try:
                fd, metadata = _open_regular(path, private=True)
                with os.fdopen(fd, 'rb') as stream:
                    available = True
                    previous = markers.get((metadata.st_dev, metadata.st_ino))
                    offset = previous['size'] if previous else 0
                    if previous:
                        stream.seek(max(0, offset - 128))
                        anchor = stream.read(min(offset, 128))
                        if metadata.st_size < offset or _digest(anchor) != previous['anchor']:
                            warnings.append('Log truncated or rewritten; interval coverage is incomplete')
                            offset = 0
                    if offset >= metadata.st_size:
                        continue
                    if path != self.log_path:
                        warnings.append('Log rotation observed; collected retained segments only')
                    if metadata.st_size - offset > MAX_LOG_BYTES:
                        warnings.append('Log excerpt truncated to the latest 64 KiB')
                        offset = metadata.st_size - MAX_LOG_BYTES
                    stream.seek(offset)
                    data = stream.read(MAX_LOG_BYTES)
                    after = os.fstat(stream.fileno())
                    if after.st_size < metadata.st_size:
                        warnings.append('Log changed during collection; coverage may be incomplete')
                    parts.append(data.decode('utf-8', errors='replace'))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as error:
                warnings.append('Log segment unavailable: ' + _clean(str(error)[:1000]))
        if not available:
            warnings.append('No readable private app log was available')
        text = '\n'.join(parts)
        if len(text.encode('utf-8')) > MAX_LOG_BYTES:
            text = text.encode('utf-8')[-MAX_LOG_BYTES:].decode('utf-8', errors='replace')
            warnings.append('Combined log excerpt truncated to the latest 64 KiB')
        return _clean(text, MAX_TEXT_BYTES)

    @staticmethod
    def _bounded_records(records, warnings, name):
        clean, used = [], 0
        for record in records:
            item = _sanitized(record)
            size = len(_json_bytes(item))
            if used + size > MAX_EVENT_BYTES:
                warnings.append(name + ' truncated to 128 KiB')
                break
            clean.append(item)
            used += size
        return clean

    def current(self):
        with self._locked():
            identifier = self._index()['selected']
            return self._public(self._load(identifier)) if identifier else None

    def list_runs(self):
        with self._locked():
            runs = []
            identifiers = sorted(path.name for path in self.root.iterdir() if self._valid_id(path.name, 'run'))
            if len(identifiers) > MAX_RUNS:
                raise ValueError('Too many trial directories')
            for identifier in identifiers:
                run = self._load(identifier)
                runs.append({key: run[key] for key in ('id', 'title', 'environment', 'started_at', 'finished_at')})
            return sorted(runs, key=lambda item: item['started_at'], reverse=True)

    def select(self, run_id):
        with self._locked():
            selected = self._index()['selected']
            if selected and selected != run_id and self._open_case(self._load(selected)):
                raise ValueError('Record the result of the open case before selecting another trial')
            run = self._load(run_id)
            _atomic_store(self.index, {'version': 1, 'selected': run_id})
            return self._public(run)

    def start(self, title, environment, build_ref='', mode='unknown', provider='', model='', test_type='unknown'):
        if not title.strip() or not environment.strip():
            raise ValueError('Title and environment are required')
        title, environment, build_ref = _clean(title, 200), _clean(environment, 128), _clean(build_ref, 200)
        if mode == 'unknown':
            mode = self.defaults.get('mode', 'unknown')
        provider = provider or self.defaults.get('provider', '')
        model = model or self.defaults.get('model', '')
        if mode not in MODES or test_type not in TEST_TYPES:
            raise ValueError('Invalid assistance mode or test environment')
        with self._locked():
            selected = self._index()['selected']
            if selected and self._load(selected)['finished_at'] is None:
                raise ValueError('Finish the selected trial before starting another')
            if sum(self._valid_id(path.name, 'run') for path in self.root.iterdir()) >= MAX_RUNS:
                raise ValueError('Trial storage reached 100 runs; archive them manually before continuing')
            identifier = 'run-' + uuid.uuid4().hex
            warnings = []
            if build_ref.strip():
                observation = BuildObservation(reference=build_ref, source='operator')
            else:
                observation = self.build_observer(Path(__file__))
            if observation.warning:
                warnings.append(_clean(observation.warning))
            build = {'version': __version__, 'python': platform.python_version(), **observation.metadata()}
            run = {'format': 'linux-ai-trial', 'version': 1, 'id': identifier,
                   'title': title, 'environment': environment,
                   'build': build,
                   'mode': mode, 'provider': _clean(provider, 128), 'model': _clean(model, 256),
                   'test_type': test_type, 'started_at': self._now(), 'finished_at': None,
                   'context': self._context(warnings), 'warnings': warnings,
                   'cases': [], 'attachments': [], 'notice': _NOTICE}
            self._save(run)
            _atomic_store(self.index, {'version': 1, 'selected': identifier})
            return self._public(run)

    def begin_case(self, case_id, session_id, variant='', interface='gui', notes=''):
        if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
            raise ValueError('Case ID must contain 1–64 letters, digits, dots, underscores or hyphens')
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            raise ValueError('A conversation ID is required')
        if interface not in ('gui', 'cli', 'manual'):
            raise ValueError('Invalid interface')
        with self._locked():
            run = self._selected()
            if run['finished_at'] is not None or self._open_case(run):
                raise ValueError('Finish the open case, or select an unfinished trial')
            if len(run['cases']) >= MAX_CASES or len(_json_bytes(run)) > MAX_RUN_BYTES - MAX_CASE_BYTES:
                raise ValueError('Trial case limit reached; finish it and start another')
            warnings = []
            baseline_events = self._audit(session_id, warnings)
            baseline_changes = self._journal(session_id, warnings)
            case = {'id': 'case-' + uuid.uuid4().hex, 'case_id': case_id,
                    'session_id': session_id, 'variant': _clean(variant, 200), 'interface': interface,
                    'notes': _clean(notes), 'observations': '', 'result': None,
                    'started_at': self._now(), 'finished_at': None,
                    'before': self._context(warnings), 'after': None, 'warnings': warnings,
                    'events': [], 'changes': [], 'log_excerpt': None, 'operation_id': None,
                    'settings_before': self._settings(run, warnings), 'settings_after': None,
                    '_baseline': {'events': [_digest(_json_bytes(item)) for item in baseline_events],
                                  'changes': [_digest(_json_bytes(item)) for item in baseline_changes],
                                  'logs': self._log_baseline()}}
            run['cases'].append(case)
            self._save(run)
            return self._public(case)

    def end_case(self, result, notes='', operation_id=None, collect_logs=False):
        if result not in RESULTS or type(collect_logs) is not bool:
            raise ValueError('Invalid result or log selection')
        if operation_id is not None and (not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128):
            raise ValueError('Invalid operation ID')
        with self._locked():
            run = self._selected()
            case = self._open_case(run)
            if case is None:
                raise ValueError('Begin a case first')
            baseline = case['_baseline']
            warnings = case['warnings']
            now = self._now()
            if now < case['started_at']:
                warnings.append('Clock moved backwards; UTC interval is unreliable')
            events = [item for item in self._audit(case['session_id'], warnings)
                      if _digest(_json_bytes(item)) not in baseline['events']
                      and (operation_id is None or item['operation_id'] == operation_id)]
            changes = [item for item in self._journal(case['session_id'], warnings)
                       if _digest(_json_bytes(item)) not in baseline['changes']]
            case.update(result=result, observations=_clean(notes), finished_at=now,
                        after=self._context(warnings), operation_id=operation_id,
                        settings_after=self._settings(run, warnings),
                        events=self._bounded_records(events, warnings, 'Audit evidence'),
                        changes=self._bounded_records(changes, warnings, 'Change evidence'),
                        log_excerpt=self._logs(baseline['logs'], warnings) if collect_logs else None)
            if case['settings_before'] != case['settings_after']:
                warnings.append('Assistance selection changed during the case; both selections are recorded')
            case.pop('_baseline')
            if len(_json_bytes(case)) > MAX_CASE_BYTES:
                raise ValueError('Case evidence exceeds its limit; shorten notes or collect without logs')
            case['warnings'] = list(dict.fromkeys(warnings))
            self._save(run)
            return self._public(case)

    def finish(self):
        with self._locked():
            run = self._selected()
            if self._open_case(run):
                raise ValueError('Record the result of the open case first')
            if run['finished_at'] is None:
                run['finished_at'] = self._now()
                self._save(run)
            return self._public(run)

    def _attachment_data(self, run, attachment):
        identifier = attachment.get('id')
        suffix = attachment.get('suffix')
        if (not self._valid_id(identifier, 'att')
                or suffix not in _TEXT_EXTENSIONS | {'.png', '.jpg'}):
            raise ValueError('Invalid attachment record')
        path = self._run_dir(run['id']) / 'attachments' / (identifier + suffix)
        limit = MAX_IMAGE_BYTES if suffix in ('.png', '.jpg') else MAX_TEXT_BYTES
        data = _read_bytes(path, limit, private=True)
        if attachment.get('sha256') != _digest(data) or attachment.get('bytes') != len(data):
            raise ValueError('Attachment changed after collection; remove it and attach again')
        if suffix in ('.png', '.jpg'):
            _image_dimensions(data, suffix)
        return path, data

    def attach(self, path):
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix == '.jpeg':
            suffix = '.jpg'
        if suffix not in _TEXT_EXTENSIONS | {'.png', '.jpg'}:
            raise ValueError('Attach UTF-8 TXT/LOG/MD/CSV/JSON or PNG/JPEG only')
        image = suffix in ('.png', '.jpg')
        data = _read_bytes(source, MAX_IMAGE_BYTES if image else MAX_TEXT_BYTES)
        dimensions = _image_dimensions(data, suffix) if image else None
        if not image:
            data = _text_artifact(data, suffix)
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError('Redacted attachment exceeds size limit')
        with self._locked():
            run = self._selected()
            if len(run['attachments']) >= MAX_ATTACHMENTS:
                raise ValueError('Attachment limit reached')
            retained = run.get('_retained_bytes', sum(item['bytes'] for item in run['attachments']))
            if retained + len(data) > MAX_ATTACHMENT_BYTES:
                raise ValueError('Attachments exceed 16 MiB in total')
            directory = _private_directory(self._run_dir(run['id']) / 'attachments')
            identifier = 'att-' + uuid.uuid4().hex
            metadata = {'id': identifier, 'name': _clean(source.name, 255), 'suffix': suffix,
                        'media_type': 'image/png' if suffix == '.png' else 'image/jpeg' if image else 'text/plain',
                        'bytes': len(data), 'sha256': _digest(data),
                        'case_entry_id': self._open_case(run)['id'] if self._open_case(run) else None,
                        'dimensions': list(dimensions) if dimensions else None,
                        'notice': 'Image pixels and metadata are not redacted' if image else 'Redaction is partial'}
            target = directory / (identifier + suffix)
            _write_new(target, data)
            try:
                run['attachments'].append(metadata)
                run['_retained_bytes'] = retained + len(data)
                self._save(run)
            except BaseException:
                target.unlink()
                raise
            return self._public(metadata)

    def exclude_attachment(self, identifier):
        with self._locked():
            run = self._selected()
            attachment = next((item for item in run['attachments'] if item['id'] == identifier), None)
            if attachment is None:
                raise ValueError('Unknown attachment')
            # Remove from the export allowlist. Retain the private copy for analysis;
            # unregistered files are never picked up by directory traversal.
            run['attachments'].remove(attachment)
            self._save(run)

    def attachment_preview(self, identifier):
        with self._locked():
            run = self._selected()
            attachment = next((item for item in run['attachments'] if item['id'] == identifier), None)
            if attachment is None:
                raise ValueError('Unknown attachment')
            path, data = self._attachment_data(run, attachment)
            if attachment['suffix'] in ('.png', '.jpg'):
                return {'kind': 'image', 'path': str(path)}
            return {'kind': 'text', 'text': data.decode('utf-8')}

    def _report(self, run):
        lines = ['# Linux AI — real-world trial', '', 'Trial: ' + _markdown(run['id']),
                 'Title: ' + _markdown(run['title']), 'Environment: ' + _markdown(run['environment']),
                 'Build reference: ' + _markdown(run['build']['reference']),
                 'Build reference source: ' + _markdown(run['build'].get('source', 'unrecorded')),
                 'Observed checkout commit: ' + _markdown(run['build'].get('commit') or 'unknown'),
                 'Observed checkout changes: ' + _markdown(run['build'].get('worktree', 'unknown')),
                 'App / Python: ' + _markdown(run['build']['version'] + ' / ' + run['build']['python']),
                 'Type / mode: ' + _markdown(run['test_type'] + ' / ' + run['mode']),
                 'Started UTC: ' + _utc(run['started_at']),
                 'Finished UTC: ' + (_utc(run['finished_at']) if run['finished_at'] is not None else 'open'),
                 '', _NOTICE, '', '## Cases', '']
        for case in run['cases']:
            lines.extend(['### ' + _markdown(case['case_id'] + ' / ' + case['variant']), '',
                          'Entry: ' + case['id'], 'Conversation: ' + _markdown(case['session_id']),
                          'Interface: ' + case['interface'], 'Result reported by operator: ' + case['result'],
                          'Assistance before / after: ' + _markdown(str(case['settings_before']) + ' / ' + str(case['settings_after'])),
                          'Started UTC: ' + _utc(case['started_at']),
                          'Ended UTC: ' + _utc(case['finished_at']),
                          'Operation filter: ' + _markdown(case['operation_id'] or 'none'), '',
                          'Preparation notes: ' + _markdown(case['notes']),
                          'Observed result / independent verification notes: ' + _markdown(case['observations']),
                          'Collected events / changes: {} / {}'.format(len(case['events']), len(case['changes'])),
                          'App log: ' + ('not selected' if case['log_excerpt'] is None else 'selected; global'), ''])
            for warning in case['warnings']:
                lines.append('- ' + _markdown(warning))
            lines.append('')
        lines.extend(['## Coverage', '', 'Each row is one attempt; repetitions do not increase coverage.',
                      'Planned but unrecorded cases are not represented. Use the protocol CSV to track them.', ''])
        for outcome in RESULTS:
            lines.append('- {}: {}'.format(outcome, sum(case['result'] == outcome for case in run['cases'])))
        lines.extend(['', '## Collection warnings', ''])
        lines.extend('- ' + _markdown(warning) for warning in run['warnings'])
        lines.extend(['', '## Attachments', ''])
        for attachment in run['attachments']:
            lines.append('- {} / {} / {} bytes / {}'.format(
                attachment['id'], _markdown(attachment['name']), attachment['bytes'], attachment['notice']))
        lines.extend(['', 'Structured context, before/after snapshots, events and journal metadata are in trial.json.', ''])
        return '\n'.join(lines).encode('utf-8')

    @staticmethod
    def _results_csv(run):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(('campanha_id', 'ambiente_id', 'execucao_id', 'build_sha', 'caso_id', 'variante',
                         'interface', 'modo', 'tipo_ensaio', 'tentativa', 'inicio_utc', 'fim_utc',
                         'resultado', 'resumo_observado', 'verificacao_independente', 'evidencia', 'issue', 'observador'))
        attempts = {}
        for case in run['cases']:
            key = (case['case_id'], case['variant'], case['interface'])
            attempts[key] = attempts.get(key, 0) + 1
            values = (run['id'], run['environment'], case['id'], run['build']['reference'], case['case_id'],
                      case['variant'], case['interface'], case['settings_before']['mode'], run['test_type'], attempts[key],
                      _utc(case['started_at']), _utc(case['finished_at']), case['result'],
                      case['observations'], '', 'trial.json#' + case['id'], '', '')
            writer.writerow([_csv_cell(item) for item in values])
        return stream.getvalue().encode('utf-8')

    def _files(self, run):
        if self._open_case(run):
            raise ValueError('Record the result of the open case before reviewing or exporting')
        # Re-sanitize metadata loaded from disk, but never interpret it as a command.
        public = _sanitized(self._public(run))
        files = {'trial.json': _json_bytes(public), 'report.md': self._report(public),
                 'results.csv': self._results_csv(public)}
        for attachment in run['attachments']:
            _, data = self._attachment_data(run, attachment)
            if attachment['suffix'] in _TEXT_EXTENSIONS:
                data = _text_artifact(data, attachment['suffix'])
            files['attachments/' + attachment['id'] + attachment['suffix']] = data
        manifest = {'format': 'linux-ai-trial-export', 'version': 1, 'run_id': run['id'],
                    'notice': _NOTICE, 'files': [{'path': name, 'bytes': len(data), 'sha256': _digest(data)}
                                               for name, data in sorted(files.items())]}
        files['manifest.json'] = _json_bytes(manifest)
        return files

    @staticmethod
    def _fingerprint(files):
        return _digest(_json_bytes({name: _digest(data) for name, data in sorted(files.items())}))

    def preview(self):
        with self._locked():
            run = self._selected()
            files = self._files(run)
            lines = [_NOTICE, '']
            for name, data in sorted(files.items()):
                lines.extend(['FILE: ' + name, 'SHA256: ' + _digest(data)])
                if name.endswith(('.png', '.jpg')):
                    lines.append('IMAGE: view every selected image and its metadata before confirming export.')
                else:
                    lines.append(data.decode('utf-8'))
                lines.append('')
            run['_review_digest'] = self._fingerprint(files)
            self._save(run, invalidate=False)
            return '\n'.join(lines)

    def export(self, path, reviewed=False):
        if reviewed is not True:
            raise ValueError('Preview the package and explicitly confirm review before export')
        with self._locked():
            run = self._selected()
            files = self._files(run)
            if run.get('_review_digest') != self._fingerprint(files):
                raise ValueError('Preview the current content again; review is missing or outdated')
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(files.items()):
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    archive.writestr(info, data)
            return _write_new(path, output.getvalue())


def make_recorder(history_store, config, distro):
    """Use only non-secret selection names, never the effective provider config."""
    def selection():
        mode = config.get_assistance_mode()
        provider = ('' if mode == 'offline' else 'local_llm' if mode == 'local'
                    else config.get('api.default_provider', ''))
        model = config.get('api.providers.' + provider + '.model', '') if provider else ''
        return {'mode': mode, 'provider': provider, 'model': model}
    return TrialRecorder(history_path=history_store.path,
                         context_factory=lambda: detect_system_context(distro).to_dict(),
                         defaults=selection(), settings_factory=selection)
