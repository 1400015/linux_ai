"""Bounded conversation file input and private, non-overwriting exports."""

import os
from pathlib import Path

MAX_IMPORT_BYTES = 2 * 1024 * 1024


def read_conversation(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Conversation file exceeds 2 MiB")
    return data.decode("utf-8")


def write_conversation(path, content):
    """Do not replace an existing file without a separate explicit action."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd is not None:
            os.close(fd)
