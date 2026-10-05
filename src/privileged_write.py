"""Small file-copy helper run by pkexec; only standard-library imports.

Directory descriptors anchor all destination operations. Exclusive temporary
files avoid predictable-name symlinks and work on GNU and BusyBox systems.
"""

from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys


SENSITIVE_FILES = frozenset({
    '/etc/passwd', '/etc/shadow', '/etc/group', '/etc/gshadow', '/etc/sudoers',
    '/etc/security/opasswd',
})
SENSITIVE_DIRECTORIES = ('/etc/sudoers.d', '/etc/polkit-1', '/etc/pam.d', '/etc/security',
                         '/etc/ssl/private', '/usr/libexec/linux-ai-assistant',
                         '/usr/share/polkit-1', '/usr/local/share/polkit-1')


def is_sensitive_path(path):
    """Account, authorization and system private-key files require manual administration."""
    resolved = os.path.realpath(os.path.expanduser(path))
    components = resolved.split(os.sep)
    return (resolved in SENSITIVE_FILES
            or resolved in (os.path.realpath('/usr/bin/pkexec'), os.path.realpath('/usr/bin/python3'))
            or any(resolved == directory or resolved.startswith(directory + os.sep)
                   for directory in SENSITIVE_DIRECTORIES)
            or (os.path.dirname(resolved) == '/etc/ssh'
                and os.path.basename(resolved).startswith('ssh_host_'))
            or '.ssh' in components or '.gnupg' in components
            or ('.aws' in components and os.path.basename(resolved) == 'credentials'))


def _destination(destination):
    parent, name = os.path.split(destination)
    if not os.path.isabs(destination) or os.path.normpath(destination) != destination or not name:
        raise ValueError('An absolute normalized destination is required')
    if is_sensitive_path(destination):
        raise PermissionError('Sensitive system files require manual administration: ' + destination)
    return parent, name


def planned_backup_path(destination, removal=False):
    """Reserve the adjacent name before I/O so an interrupted operation stays recoverable."""
    parent, name = os.path.split(destination)
    suffix = '.undo-' if removal else '.bak-'
    if len(os.fsencode(name + suffix)) + 32 > 255:
        name = '.linux-ai'
    return os.path.join(parent, name + suffix + secrets.token_hex(16))


class FileOperationError(PermissionError):
    """Failure with an explicit publication state and retained adjacent files."""

    def __init__(self, message, *, published=False, backup=None, cleanup=None):
        if published is not True and published is not False:
            published = None
        self.outcome = {'status': 'unchanged' if published is False else 'uncertain',
                        'published': published, 'backup': backup, 'cleanup': list(cleanup or ())}
        prefix = ('File publication or durability is uncertain; revalidate the target and recovery journal. '
                  if published is not False else 'The operation did not publish a change. ')
        super().__init__(prefix + str(message))


@contextmanager
def _operation(destination, directory_fd):
    state = {'published': False, 'backup': None, 'backup_valid': False,
             'temporary': None, 'fds': [directory_fd]}
    error = None
    try:
        yield state
    except BaseException as cause:
        error = cause
    cleanup = []
    for name in (state['temporary'], state['backup'] if not state['backup_valid'] else None):
        if name is None:
            continue
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError as cause:
            cleanup.append(os.path.join(os.path.dirname(destination), name))
            error = error or cause
    for descriptor in reversed(state['fds']):
        try:
            os.close(descriptor)
        except OSError as cause:
            error = error or cause
    if error is not None:
        if not isinstance(error, (OSError, ValueError)):
            raise error
        backup = (os.path.join(os.path.dirname(destination), state['backup'])
                  if state['backup_valid'] else None)
        raise FileOperationError(str(error), published=state['published'], backup=backup,
                                 cleanup=cleanup) from error


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


def _backup_file(directory_fd, destination, backup_path, removal=False):
    if backup_path is None:
        backup_path = planned_backup_path(destination, removal)
    parent = os.path.dirname(destination)
    if (os.path.dirname(backup_path) != parent or os.path.normpath(backup_path) != backup_path
            or backup_path == destination):
        raise ValueError('Backup must be adjacent to the destination')
    if is_sensitive_path(backup_path):
        raise PermissionError('Sensitive files cannot be used as helper backup destinations')
    name = os.path.basename(backup_path)
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    return descriptor, name


def write_file(source, destination, expected_digest, parent_identity, source_digest=None,
               backup_path=None):
    """Write exactly the file previewed, preserving metadata or using 0600."""
    parent, name = _destination(destination)
    if is_sensitive_path(source):
        raise PermissionError('Sensitive files cannot be copied by the application helper')
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_fd = source_fd = None
    with _operation(destination, directory_fd) as state:
        directory_stat = os.fstat(directory_fd)
        if (directory_stat.st_dev, directory_stat.st_ino) != tuple(parent_identity):
            raise PermissionError('Destination directory changed since the preview')
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        state['fds'].append(source_fd)
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError('Input must be a regular file')
        if source_digest is not None and digest_fd(source_fd) != source_digest:
            raise PermissionError('Source changed since the preview')
        try:
            original_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        if original_fd is not None:
            state['fds'].append(original_fd)
        original = os.fstat(original_fd) if original_fd is not None else None
        if original is not None and not stat.S_ISREG(original.st_mode):
            raise ValueError('Destination must be a regular file')
        actual_digest = digest_fd(original_fd) if original_fd is not None else None
        if actual_digest != expected_digest:
            raise PermissionError('File changed since the preview; review it again')

        if original is not None:
            backup_fd, backup = _backup_file(directory_fd, destination, backup_path)
            state['backup'] = backup
            with os.fdopen(backup_fd, 'wb') as out, os.fdopen(os.dup(original_fd), 'rb') as inp:
                shutil.copyfileobj(inp, out, 65536)
                out.flush()
                os.fchown(out.fileno(), original.st_uid, original.st_gid)
                os.fchmod(out.fileno(), stat.S_IMODE(original.st_mode))
                os.fsync(out.fileno())
            state['backup_valid'] = True
            # Persist the recovery name before publishing the destination.
            os.fsync(directory_fd)
            os.lseek(original_fd, 0, os.SEEK_SET)

        temporary_fd, temporary = exclusive_file(directory_fd, '.linux-ai-')
        state['temporary'] = temporary
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
        state['published'] = True
        state['temporary'] = None
        os.fsync(directory_fd)
        return os.path.join(parent, state['backup']) if state['backup_valid'] else None


def remove_file(destination, expected_digest, parent_identity, backup_path=None):
    """Undo creation of an unchanged regular file, retaining a recovery copy."""
    parent, name = _destination(destination)
    if expected_digest is None:
        raise ValueError('An absolute destination and digest are required')
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_fd = None
    with _operation(destination, directory_fd) as state:
        info = os.fstat(directory_fd)
        if (info.st_dev, info.st_ino) != tuple(parent_identity):
            raise PermissionError('Destination directory changed since the preview')
        original_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        state['fds'].append(original_fd)
        original = os.fstat(original_fd)
        if not stat.S_ISREG(original.st_mode) or digest_fd(original_fd) != expected_digest:
            raise PermissionError('File changed since the preview')
        backup_fd, backup = _backup_file(directory_fd, destination, backup_path, removal=True)
        state['backup'] = backup
        with os.fdopen(backup_fd, 'wb') as out, os.fdopen(os.dup(original_fd), 'rb') as inp:
            shutil.copyfileobj(inp, out, 65536)
            out.flush()
            os.fchown(out.fileno(), original.st_uid, original.st_gid)
            os.fchmod(out.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(out.fileno())
        state['backup_valid'] = True
        os.fsync(directory_fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
                or digest_fd(original_fd) != expected_digest):
            raise PermissionError('File changed during recovery')
        os.unlink(name, dir_fd=directory_fd)
        state['published'] = True
        os.fsync(directory_fd)
        return os.path.join(parent, backup)


def inspect_file(destination, expected_digest, parent_identity, backup=None, backup_digest=None):
    """Authenticated read preview for a private file previously written by us."""
    parent, name = _destination(destination)
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
            if is_sensitive_path(backup):
                raise PermissionError('Sensitive files cannot be read by the application helper')
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
            if len(args) not in (5, 6):
                raise ValueError('Unexpected remove arguments')
            _, destination, digest, device, inode = args[:5]
            backup = remove_file(destination, digest, (int(device), int(inode)),
                                 args[5] if len(args) == 6 else None)
        else:
            if len(args) not in (6, 7):
                raise ValueError('Unexpected write arguments')
            source, destination, digest, device, inode = args[:5]
            if len(args[5]) != 64 or any(char not in '0123456789abcdef' for char in args[5]):
                raise ValueError('The SHA-256 of the approved source is required')
            backup = write_file(source, destination, None if digest == '-' else digest,
                                (int(device), int(inode)), args[5],
                                args[6] if len(args) == 7 else None)
        print(json.dumps({'backup': backup, 'published': True, 'status': 'completed'}))
        return 0
    except (OSError, ValueError) as error:
        outcome = getattr(error, 'outcome', {'status': 'unchanged', 'published': False,
                                           'backup': None, 'cleanup': []})
        print(json.dumps({'outcome': outcome}))
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
