"""Small file-copy helper run by pkexec; only standard-library imports.

Directory descriptors anchor all destination operations. Exclusive temporary
files avoid predictable-name symlinks and work on GNU and BusyBox systems.
"""

import hashlib
import json
import os
import secrets
import shutil
import stat
import sys


def digest_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def exclusive_file(directory_fd, prefix):
    name = prefix + secrets.token_hex(16)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory_fd)
    return fd, name


def write_file(source, destination, expected_digest, parent_identity):
    """Write exactly the file previewed, preserving metadata or using 0600."""
    parent, name = os.path.split(destination)
    if not os.path.isabs(destination) or not name:
        raise ValueError('An absolute destination is required')
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_fd = source_fd = None
    temporary = None
    try:
        directory_stat = os.fstat(directory_fd)
        if (directory_stat.st_dev, directory_stat.st_ino) != tuple(parent_identity):
            raise PermissionError('Destination directory changed since the preview')
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError('Input must be a regular file')
        try:
            original_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        original = os.fstat(original_fd) if original_fd is not None else None
        if original is not None and not stat.S_ISREG(original.st_mode):
            raise ValueError('Destination must be a regular file')
        actual_digest = digest_fd(original_fd) if original_fd is not None else None
        if actual_digest != expected_digest:
            raise PermissionError('File changed since the preview; review it again')

        backup = None
        if original is not None:
            backup_fd, backup = exclusive_file(directory_fd, name + '.bak-')
            with os.fdopen(backup_fd, 'wb') as out, os.fdopen(os.dup(original_fd), 'rb') as inp:
                shutil.copyfileobj(inp, out, 65536)
                out.flush()
                os.fchown(out.fileno(), original.st_uid, original.st_gid)
                os.fchmod(out.fileno(), stat.S_IMODE(original.st_mode))
                os.fsync(out.fileno())
            os.lseek(original_fd, 0, os.SEEK_SET)

        temporary_fd, temporary = exclusive_file(directory_fd, '.linux-ai-')
        with os.fdopen(temporary_fd, 'wb') as out, os.fdopen(os.dup(source_fd), 'rb') as inp:
            shutil.copyfileobj(inp, out, 65536)
            out.flush()
            if original is not None:
                os.fchown(out.fileno(), original.st_uid, original.st_gid)
                os.fchmod(out.fileno(), stat.S_IMODE(original.st_mode))
            else:
                # A new system configuration is private and owned by the
                # elevated helper. The user may explicitly change its mode later.
                os.fchmod(out.fileno(), 0o600)
            os.fsync(out.fileno())

        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if original is None:
            if current is not None:
                raise PermissionError('Destination appeared since the preview')
        elif (current is None or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
              or digest_fd(original_fd) != expected_digest):
            raise PermissionError('File changed during the write; review it again')
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
        return os.path.join(parent, backup) if backup else None
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory_fd)
        for fd in (original_fd, source_fd, directory_fd):
            if fd is not None:
                os.close(fd)


def main():
    try:
        source, destination, digest, device, inode = sys.argv[1:]
        backup = write_file(source, destination, None if digest == '-' else digest,
                            (int(device), int(inode)))
        print(json.dumps({'backup': backup}))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
