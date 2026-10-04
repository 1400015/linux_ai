"""Atomic JSON transactions shared by the Linux GUI and CLI."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile


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


def atomic_json_write(path, value):
    path = Path(path)
    published = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
        write_error = None
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
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


def update_json(path, update, default):
    """Read/modify/replace under one inter-process lock; preserve corrupt input."""
    path = Path(path)
    published = False
    published_value = None
    try:
        with json_lock(path):
            try:
                with path.open(encoding='utf-8') as stream:
                    previous = json.load(stream)
            except FileNotFoundError:
                previous = default
            except (json.JSONDecodeError, UnicodeError):
                fd, backup = tempfile.mkstemp(prefix=path.name + '.corrupt-', dir=str(path.parent))
                with os.fdopen(fd, 'wb') as target, path.open('rb') as source:
                    shutil.copyfileobj(source, target)
                previous = default
            result = update(previous)
            try:
                atomic_json_write(path, result)
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
