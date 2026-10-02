"""Atomic JSON transactions shared by the Linux GUI and CLI."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile


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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_json(path, update, default):
    """Read/modify/replace under one inter-process lock; preserve corrupt input."""
    path = Path(path)
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
        atomic_json_write(path, result)
        return result
