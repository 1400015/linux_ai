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


def write_file(source, destination, expected_digest, parent_identity, source_digest=None):
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
        if source_digest is not None and digest_fd(source_fd) != source_digest:
            raise PermissionError('Source changed since the preview')
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

        if source_digest is not None and digest_fd(source_fd) != source_digest:
            raise PermissionError('Source changed during the write')
        if source_digest is not None:
            copied_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                if digest_fd(copied_fd) != source_digest:
                    raise PermissionError('Copied contents differ from the approved source')
            finally:
                os.close(copied_fd)

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


def remove_file(destination, expected_digest, parent_identity):
    """Undo creation of an unchanged regular file, retaining a recovery copy."""
    parent, name = os.path.split(destination)
    if not os.path.isabs(destination) or not name or expected_digest is None:
        raise ValueError('An absolute destination and digest are required')
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_fd = None
    try:
        info = os.fstat(directory_fd)
        if (info.st_dev, info.st_ino) != tuple(parent_identity):
            raise PermissionError('Destination directory changed since the preview')
        original_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        original = os.fstat(original_fd)
        if not stat.S_ISREG(original.st_mode) or digest_fd(original_fd) != expected_digest:
            raise PermissionError('File changed since the preview')
        backup_fd, backup = exclusive_file(directory_fd, name + '.undo-')
        with os.fdopen(backup_fd, 'wb') as out, os.fdopen(os.dup(original_fd), 'rb') as inp:
            shutil.copyfileobj(inp, out, 65536)
            out.flush()
            os.fchown(out.fileno(), original.st_uid, original.st_gid)
            os.fchmod(out.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(out.fileno())
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
                or digest_fd(original_fd) != expected_digest):
            raise PermissionError('File changed during recovery')
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return os.path.join(parent, backup)
    finally:
        if original_fd is not None:
            os.close(original_fd)
        os.close(directory_fd)


def inspect_file(destination, expected_digest, parent_identity, backup=None, backup_digest=None):
    """Authenticated read preview for a private file previously written by us."""
    parent, name = os.path.split(destination)
    if not os.path.isabs(destination) or not name:
        raise ValueError('An absolute destination is required')
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptors = []
    try:
        info = os.fstat(directory_fd)
        if (info.st_dev, info.st_ino) != tuple(parent_identity):
            raise PermissionError('Destination directory changed since the preview')
        current = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        descriptors.append(current)
        if not stat.S_ISREG(os.fstat(current).st_mode) or digest_fd(current) != expected_digest:
            raise PermissionError('File changed after this operation; recovery refused')
        source = current
        if backup is not None:
            if os.path.dirname(backup) != parent or backup == destination:
                raise PermissionError('Backup must be adjacent to the destination')
            source = os.open(os.path.basename(backup), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            descriptors.append(source)
            if not stat.S_ISREG(os.fstat(source).st_mode) or digest_fd(source) != backup_digest:
                raise PermissionError('Original backup missing or changed; recovery refused')
        data = bytearray()
        while len(data) <= 1024 * 1024:
            chunk = os.read(source, min(65536, 1024 * 1024 + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return {'content': bytes(data[:1024 * 1024]).decode('utf-8', errors='replace'),
                'truncated': len(data) > 1024 * 1024}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        os.close(directory_fd)


def main():
    try:
        args = sys.argv[1:]
        if args and args[0] == '--inspect':
            _, destination, digest, device, inode, backup, original_digest = args
            result = inspect_file(destination, digest, (int(device), int(inode)),
                                  None if backup == '-' else backup, None if original_digest == '-' else original_digest)
            print(json.dumps(result))
            return 0
        if args and args[0] == '--remove':
            _, destination, digest, device, inode = args
            backup = remove_file(destination, digest, (int(device), int(inode)))
        else:
            if len(args) not in (5, 6):
                raise ValueError('Unexpected write arguments')
            source, destination, digest, device, inode = args[:5]
            backup = write_file(source, destination, None if digest == '-' else digest,
                                (int(device), int(inode)), args[5] if len(args) == 6 else None)
        print(json.dumps({'backup': backup}))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
