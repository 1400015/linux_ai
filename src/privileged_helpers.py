"""Only installed root-owned launchers may be passed to the dedicated polkit actions."""

import hashlib
import json
import os
from pathlib import Path
import stat
from xml.etree import ElementTree


HELPER_DIRECTORY = Path('/usr/libexec/linux-ai-assistant')
POLICY_PATH = Path('/usr/share/polkit-1/actions/org.linux_ai_assistant.policy')
PKEXEC_PATH = Path('/usr/bin/pkexec')
PYTHON_PATH = Path('/usr/bin/python3')
MAX_HELPER_BYTES = 1024 * 1024
PROTOCOL = 2


def _trusted(path, executable=False, interpreter_link=False, _seen=None):
    """Reject writable ancestors and links, except a root-owned interpreter alias."""
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise PermissionError('A fixed absolute helper path is required')
    seen = set() if _seen is None else set(_seen)
    if path in seen or len(seen) >= 16:
        raise PermissionError('Invalid interpreter link chain')
    seen.add(path)
    current = Path(path.anchor)
    root = current.lstat()
    if root.st_uid != 0 or not stat.S_ISDIR(root.st_mode) or root.st_mode & 0o022:
        raise PermissionError('The filesystem root is not protected')
    if path == current:
        raise PermissionError('A privileged helper must be a regular file')
    for component in path.parts[1:]:
        current /= component
        info = current.lstat()
        if info.st_uid != 0:
            raise PermissionError('Privileged helper paths must be owned by root: ' + str(current))
        if stat.S_ISLNK(info.st_mode):
            if current != path or not interpreter_link:
                raise PermissionError('Privileged helper paths must not contain links: ' + str(current))
            # realpath() would hide unsafe intermediate aliases. Validate each
            # link and the ancestors of its immediate target before proceeding.
            destination = os.readlink(str(path))
            if not os.path.isabs(destination):
                destination = os.path.join(str(path.parent), destination)
            destination = Path(os.path.normpath(destination))
            return _trusted(destination, executable=executable, interpreter_link=True, _seen=seen)
        if info.st_mode & 0o022:
            raise PermissionError('Privileged helper paths must not be writable by other users: ' + str(current))
        expected = stat.S_ISREG if current == path else stat.S_ISDIR
        if not expected(info.st_mode):
            raise PermissionError('Invalid privileged helper path: ' + str(current))
    if executable and not info.st_mode & 0o111:
        raise PermissionError('Privileged helper is not executable: ' + str(path))
    return path


def _read_trusted(path, limit=MAX_HELPER_BYTES):
    _trusted(path)
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError('Unsafe privileged helper file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise PermissionError('Privileged helper file exceeds its size limit')
    return data


def _command(kind):
    try:
        _trusted(PKEXEC_PATH, executable=True, interpreter_link=True)
        _trusted(PYTHON_PATH, executable=True, interpreter_link=True)
        policy = _read_trusted(POLICY_PATH, 64 * 1024)
        launcher = HELPER_DIRECTORY / kind
        _trusted(launcher, executable=True)
        data = _read_trusted(launcher)
        if not data.startswith(b'#!/usr/bin/python3 -IS\n'):
            raise PermissionError('The privileged launcher must use the fixed isolated interpreter without site code')
        manifest = json.loads(_read_trusted(HELPER_DIRECTORY / 'manifest.json', 64 * 1024))
        names = ('files', 'services', 'privileged_write.py', 'privileged_service.py')
        if (not isinstance(manifest, dict) or manifest.get('protocol') != PROTOCOL
                or not isinstance(manifest.get('sha256'), dict)
                or set(manifest['sha256']) != set(names)):
            raise PermissionError('Incompatible privileged helper installation; reinstall it')
        for name in names:
            digest = hashlib.sha256(_read_trusted(HELPER_DIRECTORY / name)).hexdigest()
            if manifest['sha256'][name] != digest:
                raise PermissionError('Privileged helper installation is incomplete; reinstall it')
        # The annotation, rather than an unsupported pkexec command-line flag,
        # chooses the dedicated action for this fixed installed executable.
        configuration = ElementTree.fromstring(policy)
        if configuration.tag != 'policyconfig':
            raise PermissionError('The dedicated polkit configuration is invalid')
        matches = [action for action in configuration.findall('action')
                   if any(annotation.get('key') == 'org.freedesktop.policykit.exec.path'
                          and (annotation.text or '').strip() == str(launcher)
                          for annotation in action.findall('annotate'))]
        if len(matches) != 1 or matches[0].get('id') != 'org.linux_ai_assistant.' + kind:
            raise PermissionError('The dedicated polkit action is not installed')
        action = matches[0]
        if (action.findtext('defaults/allow_active') != 'auth_admin'
                or action.findtext('defaults/allow_inactive') != 'no'
                or action.findtext('defaults/allow_any') != 'no'):
            raise PermissionError('The dedicated polkit action must require fresh administrator authentication')
        selectors = {annotation.get('key'): (annotation.text or '').strip()
                     for annotation in action.findall('annotate')
                     if annotation.get('key', '').startswith('org.freedesktop.policykit.exec.')}
        if (selectors != {'org.freedesktop.policykit.exec.path': str(launcher)}
                or len([annotation for annotation in action.findall('annotate')
                        if annotation.get('key', '').startswith('org.freedesktop.policykit.exec.')]) != 1):
            raise PermissionError('The dedicated polkit action has incompatible executable selectors')
    except (OSError, ValueError, TypeError, ElementTree.ParseError) as error:
        raise PermissionError('Dedicated privileged helpers are unavailable or unsafe. '
                              'Install them with sudo bash scripts/install-privileged-helpers.sh. '
                              + str(error)) from error
    return [str(PKEXEC_PATH), str(launcher)]


def file_helper_command():
    return _command('files')


def service_helper_command():
    return _command('services')
