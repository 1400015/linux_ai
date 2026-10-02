"""Isolated stdlib helper: only enable/disable one existing runit definition.

Invoked by pkexec with Python -I. No arbitrary paths, shell, recursive removal,
configuration text or scripts are accepted. This does not control service processes.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import sys

PROTECTED = {'dbus', 'systemd-logind', 'systemd-udevd', 'systemd-journald', 'runit', 'udevd', 'polkit'}


def directory_identity(path):
    resolved = os.path.realpath(path)
    metadata = os.stat(resolved)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ValueError('Unprotected service directory')
    parent = os.path.dirname(resolved)
    while parent != resolved:
        ancestor = os.stat(parent)
        if ancestor.st_uid != 0 or (ancestor.st_mode & 0o022 and not ancestor.st_mode & stat.S_ISVTX):
            raise ValueError('Unprotected service directory ancestor')
        resolved_parent = os.path.dirname(parent)
        if resolved_parent == parent:
            break
        parent = resolved_parent
    return [resolved, metadata.st_dev, metadata.st_ino]


def definition_identity(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o022
                or before.st_size > 128 * 1024):
            raise ValueError('Unprotected service definition')
        data = stream.read(128 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('Service definition changed')
    return [path, before.st_dev, before.st_ino, hashlib.sha256(data).hexdigest()]


def runit_identity(name, runtime='/var/service', definitions='/etc/sv'):
    definition = os.path.realpath(os.path.join(definitions, name))
    if os.path.dirname(definition) != definitions:
        raise ValueError('Definition is outside /etc/sv')
    directory_identity(definition)
    evidence = [definition, definition_identity(os.path.join(definition, 'run')), directory_identity(runtime)]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def change(verb, name, expected):
    # CLI entry point has fixed scopes. Private core supports disposable fixtures.
    with open('/proc/1/comm', encoding='utf-8') as stream:
        if stream.read(129).strip() not in {'runit', 'runit-init'}:
            raise ValueError('The active init is not runit')
    directory_identity('/var')
    directory_identity('/etc/sv')
    return _change(verb, name, expected, '/var/service', '/etc/sv')


def _change(verb, name, expected, runtime_path, definitions):
    if os.geteuid() != 0:
        raise ValueError('This helper must be authorized through pkexec')
    if (verb not in {'enable', 'disable'} or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@:-]{0,126}', name)
            or '..' in name or name in PROTECTED or not re.fullmatch(r'[a-f0-9]{64}', expected)):
        raise ValueError('Invalid restricted service request')
    runtime = directory_identity(runtime_path)
    descriptor = os.open(runtime[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        metadata = os.fstat(descriptor)
        if [metadata.st_dev, metadata.st_ino] != runtime[1:]:
            raise ValueError('Runtime directory changed')
        lock = os.open('.linux-ai-actions.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        lock_metadata = os.fstat(lock)
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != 0 or lock_metadata.st_mode & 0o077:
            raise ValueError('Invalid service lock')
        fcntl.flock(lock, fcntl.LOCK_EX)
        if runit_identity(name, runtime_path, definitions) != expected or directory_identity(runtime_path) != runtime:
            raise ValueError('Prepared service identity changed')
        definition = os.path.join(definitions, name)
        try:
            entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            entry = None
        if entry is not None:
            if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != 0:
                raise ValueError('Existing runtime entry is not a symlink')
            destination = os.readlink(name, dir_fd=descriptor)
            if os.path.realpath(os.path.join(runtime[0], destination)) != definition:
                raise ValueError('Runtime symlink targets another definition')
        if verb == 'enable' and entry is None:
            os.symlink(definition, name, dir_fd=descriptor)
        elif verb == 'disable' and entry is not None:
            os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        if lock is not None:
            os.close(lock)
        os.close(descriptor)


def main(arguments):
    if len(arguments) != 3:
        return 2
    try:
        change(*arguments)
    except (OSError, ValueError):
        print('Restricted runit activation failed; inspect the current service state.', file=sys.stderr)
        return 1
    print('Restricted runit activation completed.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
