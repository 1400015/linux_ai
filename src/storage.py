"""Atomic JSON transactions shared by the Linux GUI and CLI."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class JsonLimitError(ValueError):
    """A bounded JSON operation was refused without replacing its input."""


class _NestingGuard:
    """Bound structural nesting independently of the interpreter's recursion limit."""

    def __init__(self):
        self.depth = 0
        self.in_string = False
        self.escaped = False

    def feed(self, raw):
        for character in raw:
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif character == 92:  # Backslash, including escaped quotes.
                    self.escaped = True
                elif character == 34:
                    self.in_string = False
            elif character == 34:
                self.in_string = True
            elif character in (91, 123):
                self.depth += 1
                if self.depth > 128:
                    raise JsonLimitError('JSON nesting limit exceeded; original file preserved')
            elif character in (93, 125):
                self.depth -= 1


def read_json(path, max_bytes=None):
    if max_bytes is None:
        with Path(path).open(encoding='utf-8') as stream:
            return json.load(stream)
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Bounded JSON input must be a regular file')
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise JsonLimitError('JSON exceeds the {} byte limit; original file preserved'.format(max_bytes))
    _NestingGuard().feed(raw)
    try:
        return json.loads(raw.decode('utf-8'))
    except RecursionError:
        raise JsonLimitError('JSON nesting limit exceeded; original file preserved') from None


class _LimitedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.written = stream, limit, 0
        self.nesting = _NestingGuard()

    def write(self, value):
        encoded = value.encode('utf-8')
        self.written += len(encoded)
        if self.written > self.limit:
            raise JsonLimitError('JSON exceeds the {} byte limit; original file preserved'.format(self.limit))
        self.nesting.feed(encoded)
        return self.stream.write(value)


class JsonWriteCommittedError(OSError):
    """JSON was published before completion or durability became uncertain."""

    def __init__(self, value, cause):
        message = 'JSON was replaced, but write completion or durability could not be confirmed.'
        if cause.errno is None:
            super().__init__(message)
        else:
            super().__init__(cause.errno, message)
        self.value = value


@contextmanager
def json_lock(path):
    # Lock a stable sidecar, not the inode replaced by each transaction.
    import fcntl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def atomic_json_write(path, value, max_bytes=None):
    path = Path(path)
    published = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
        write_error = None
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                target = _LimitedWriter(stream, max_bytes) if max_bytes is not None else stream
                json.dump(value, target, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            published = True
            # The name no longer belongs to this write. Cleanup must not remove
            # an unrelated file created at the old temporary path after rename.
            temporary = None
            directory_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException as error:
            write_error = error
            raise
        finally:
            if temporary is not None:
                try:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                except OSError as cleanup_error:
                    if write_error is None:
                        raise
                    write_error.temporary_cleanup_error = cleanup_error
    except OSError as error:
        if published:
            raise JsonWriteCommittedError(value, error) from error
        raise


def update_json(path, update, default, max_bytes=None):
    """Read/modify/replace under one inter-process lock; preserve corrupt input."""
    path = Path(path)
    published = False
    published_value = None
    try:
        with json_lock(path):
            try:
                previous = read_json(path, max_bytes)
            except FileNotFoundError:
                previous = default
            except (json.JSONDecodeError, UnicodeError):
                fd, backup = tempfile.mkstemp(prefix=path.name + '.corrupt-', dir=str(path.parent))
                try:
                    with os.fdopen(fd, 'wb') as target, path.open('rb') as source:
                        if max_bytes is None:
                            shutil.copyfileobj(source, target)
                        else:
                            original = source.read(max_bytes + 1)
                            if len(original) > max_bytes:
                                raise JsonLimitError('JSON grew beyond its byte limit; original file preserved')
                            target.write(original)
                        target.flush()
                        os.fsync(target.fileno())
                    directory_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except BaseException:
                    try:
                        os.unlink(backup)
                    except OSError:
                        pass
                    raise
                previous = default
            result = update(previous)
            try:
                if max_bytes is None:
                    atomic_json_write(path, result)
                else:
                    atomic_json_write(path, result, max_bytes=max_bytes)
            except JsonWriteCommittedError as error:
                # Record publication before the lock's finally block: a close
                # failure there can replace this exception during unwinding.
                published, published_value = True, error.value
                raise
            published, published_value = True, result
            return result
    except OSError as error:
        if published and not isinstance(error, JsonWriteCommittedError):
            raise JsonWriteCommittedError(published_value, error) from error
        raise
