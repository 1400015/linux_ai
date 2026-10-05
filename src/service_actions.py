"""Discovered systemd/runit services; installed clients alone never authorize control.

References: https://www.freedesktop.org/software/systemd/man/systemctl.html
https://smarden.org/runit/sv.8 and https://docs.voidlinux.org/config/services/index.html
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from .action_audit import record_command
from .action_contract import ActionDefinition, ActionResult
from .package_actions import _run
from .system_context import detect_system_context
from .privileged_service import directory_identity

_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@:-]{0,126}\Z')
VERBS = {'start', 'stop', 'restart', 'enable', 'disable'}
PROTECTED = {'dbus', 'systemd-logind', 'systemd-udevd', 'systemd-journald', 'runit', 'udevd', 'polkit'}


def _service_run(argv, timeout=15):
    env = os.environ.copy()
    for key in ('DBUS_SYSTEM_BUS_ADDRESS', 'SYSTEMD_BUS_ADDRESS', 'SYSTEMD_UNIT_PATH', 'SYSTEMD_HOST', 'SYSTEMD_MACHINE'):
        env.pop(key, None)
    return _run(argv, timeout, environment=env)


def valid_name(value):
    return type(value) is str and bool(_NAME.fullmatch(value)) and '..' not in value


@dataclass(frozen=True)
class ServiceCandidate:
    name: str
    manager: str
    identity: str
    active: str
    enabled: str
    pid: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {'name', 'manager', 'identity', 'active', 'enabled', 'pid'}:
            raise ValueError('Invalid discovered service')
        if (not valid_name(value['name']) or value['manager'] not in {'runit', 'systemd'}
                or type(value['identity']) is not str or not re.fullmatch('[a-f0-9]{64}', value['identity'])
                or value['active'] not in {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'}
                or value['enabled'] not in {'enabled', 'disabled', 'static', 'indirect', 'masked', 'generated', 'alias', 'enabled-runtime', 'linked', 'linked-runtime', 'transient'}
                or type(value['pid']) is not int or not 0 <= value['pid'] < 2**31):
            raise ValueError('Invalid discovered service facts')
        return cls(**value)


def _file_identity(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024:
            raise ValueError('Service definition is not a bounded regular file')
        if before.st_uid != 0 or before.st_mode & 0o022:
            raise ValueError('Service definition must be owned and protected by root')
        data = stream.read(128 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_dev, after.st_mtime_ns, after.st_size):
            raise ValueError('Service definition changed during observation')
        return [path, before.st_dev, before.st_ino, hashlib.sha256(data).hexdigest()]


class ServiceService:
    def __init__(self, context_supplier=detect_system_context, runner=None, which=shutil.which,
                 service_dir='/var/service', file_identity=_file_identity):
        self.context_supplier, self.runner, self.which = context_supplier, runner or _service_run, which
        self.service_dir, self.file_identity = Path(service_dir), file_identity

    def _manager(self):
        component = self.context_supplier().service_manager
        if not component.active or component.identifier not in {'systemd', 'runit'}:
            raise ValueError('An active systemd or runit manager must be observed; an installed client is insufficient.')
        return component.identifier

    def _call(self, argv, timeout=15):
        ok, text = self.runner(argv, timeout=timeout)
        record_command(argv, ok)
        if type(text) is not str or len(text.encode()) > 1024 * 1024:
            raise ValueError('Invalid service query result')
        return ok, text

    def list_names(self):
        manager = self._manager()
        if manager == 'systemd':
            ok, text = self._call(['systemctl', '--system', 'list-unit-files', '--type=service', '--no-legend', '--no-pager', '--plain'])
            if not ok:
                raise ValueError('Cannot discover systemd service units')
            names = [line.split()[0] for line in text.splitlines() if line.split() and valid_name(line.split()[0]) and line.split()[0].endswith('.service')]
        else:
            names = [path.name for directory in (self.service_dir, Path('/etc/sv')) for path in directory.iterdir()
                     if valid_name(path.name) and path.is_dir()]
        if len(names) > 1024:
            raise ValueError('Service collection exceeds the limit')
        return sorted(set(names))

    def status(self, name):
        manager = self._manager()
        names = self.list_names()
        matches = [item for item in names if item.casefold() == name.casefold() or (manager == 'systemd' and item.casefold() == name.casefold() + '.service')]
        if len(matches) != 1:
            raise ValueError('Select one currently discovered service by its exact name')
        name = matches[0]
        if manager == 'systemd':
            ok, text = self._call(['systemctl', '--system', 'show', '--no-pager',
                '--property=Id,LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths,Transient,MainPID', '--', name])
            fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
            if not ok or fields.get('LoadState') != 'loaded' or fields.get('Id') != name or fields.get('Transient') != 'no':
                raise ValueError('The service is missing, aliased or transient')
            # Escaped/ambiguous paths are refused rather than guessed.
            paths = [fields.get('FragmentPath', '')] + fields.get('DropInPaths', '').split()
            if any(not path.startswith('/') or '\\' in path for path in paths):
                raise ValueError('Cannot establish the service definition identity')
            identity = [self.file_identity(path) for path in paths]
            active, enabled = fields.get('ActiveState', ''), fields.get('UnitFileState', '')
            pid = int(fields.get('MainPID', '0'))
        else:
            path = self.service_dir / name
            definition = (Path('/etc/sv') / name).resolve(strict=True)
            if definition.parent != Path('/etc/sv'):
                raise ValueError('Only observed runit definitions directly under /etc/sv are supported')
            directory_identity(str(definition))
            identity = [str(definition), self.file_identity(str(definition / 'run')), directory_identity(str(self.service_dir))]
            if not path.exists() and not path.is_symlink():
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                return ServiceCandidate(name, manager, digest, 'inactive', 'disabled')
            if not path.is_symlink() or path.resolve(strict=True) != definition:
                raise ValueError('The runtime entry is not the selected runit definition')
            ok, text = self._call(['sv', 'status', str(path)])
            match = re.match(r'^(run|down): .*?(?:\(pid (\d+)\))?', text)
            if not ok or match is None:
                raise ValueError('This runit service is not currently supervised')
            active, enabled = ('active' if match[1] == 'run' else 'inactive'), 'enabled'
            found_pid = re.search(r'\(pid (\d+)\)', text.splitlines()[0])
            pid = int(found_pid[1]) if found_pid else 0
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return ServiceCandidate.from_dict(dict(name=name, manager=manager, identity=digest, active=active, enabled=enabled, pid=pid))

    def control(self, candidate, verb, is_current):
        if verb not in VERBS:
            raise ValueError('Unsupported service operation')
        base_name = candidate.name[:-8] if candidate.name.endswith('.service') else candidate.name
        if base_name in PROTECTED:
            raise ValueError('Control of core session/init services is unavailable')
        fresh = self.status(candidate.name)
        if fresh.manager != candidate.manager or fresh.identity != candidate.identity:
            raise ValueError('Service identity changed; inspect it again')
        if not is_current():
            raise ValueError('Service request cancelled')
        tool = self.which('systemctl' if fresh.manager == 'systemd' else 'sv')
        pkexec = self.which('pkexec')
        if not tool or not pkexec or not os.path.isabs(tool) or not os.path.isabs(pkexec):
            raise ValueError('Absolute service client and pkexec paths are required')
        if fresh.manager == 'runit' and verb in {'enable', 'disable'}:
            if self.service_dir != Path('/var/service'):
                raise ValueError('Runit activation only supports the system runtime directory')
            from .privileged_helpers import service_helper_command
            helper_command = service_helper_command()
            if verb == 'disable' and fresh.active == 'active':
                # The helper installation has already validated this fixed
                # elevation binary; do not switch back to a PATH lookup here.
                stopped, _ = self._call([helper_command[0], tool, '-w', '10', 'stop', str(self.service_dir / fresh.name)], timeout=30)
                if not stopped or self.status(fresh.name).active != 'inactive':
                    return ActionResult('failed', 'The service did not stop; its runtime link was preserved.')
            argv = helper_command + [verb, fresh.name, fresh.identity]
        else:
            argv = [pkexec, tool, '--system', verb, '--', fresh.name] if fresh.manager == 'systemd' else [pkexec, tool, '-w', '10', verb, str(self.service_dir / fresh.name)]
        ok, _ = self._call(argv, timeout=90)
        # Verify after execution even if the request was revoked in the meantime.
        if fresh.manager == 'runit' and verb == 'enable':
            deadline = time.monotonic() + 5
            while True:
                try:
                    after = self.status(fresh.name)
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise ValueError('The enabled service did not enter supervision')
                    time.sleep(0.2)
        else:
            after = self.status(fresh.name)
        expected = after.active == ('inactive' if verb == 'stop' else 'active') if verb in {'start', 'stop', 'restart'} else (
            after.enabled == 'enabled' if verb == 'enable' else after.enabled == 'disabled')
        if verb == 'restart' and fresh.pid and after.pid == fresh.pid:
            expected = False
        if after.identity != fresh.identity or after.manager != fresh.manager or after.name != fresh.name:
            expected = False
        if not ok or not expected:
            return ActionResult('failed', 'O efeito pedido não foi confirmado. / The requested effect was not verified.', data=after)
        return ActionResult('done', '{}: {} / {}; {}'.format(after.name, after.active, after.enabled, verb), data=after, verified=True)


class ServiceAdapter:
    def __init__(self, service, operation):
        self.service, self.operation = service, operation
        mutate = operation == 'control'
        self.definition = ActionDefinition('services.' + operation, mutate, 'high' if mutate else 'read',
            'pkexec' if mutate else 'user', 'No universal rollback; inspect service state. Reversing a restart does not restore process state.')

    def prepare(self, parameters):
        if self.operation == 'list':
            if parameters:
                raise ValueError('Service listing accepts no arguments')
            return {}, 'observed active service manager', 'read'
        if self.operation == 'status':
            if set(parameters) != {'name'} or not valid_name(parameters['name']):
                raise ValueError('Invalid service name')
            return parameters, parameters['name'], 'read'
        if set(parameters) != {'candidate', 'verb'} or parameters['verb'] not in VERBS:
            raise ValueError('Invalid service control parameters')
        candidate = ServiceCandidate.from_dict(parameters['candidate'])
        return parameters, candidate.manager + ':' + candidate.name + ':' + parameters['verb'], 'high'

    def execute(self, parameters, is_current):
        if not is_current():
            return ActionResult('failed', 'Service request cancelled.')
        try:
            if self.operation == 'list':
                return ActionResult('done', data=self.service.list_names(), verified=True)
            if self.operation == 'status':
                candidate = self.service.status(parameters['name'])
                return ActionResult('done', '{}: {}; {}'.format(candidate.name, candidate.active, candidate.enabled), data=candidate, verified=True)
            return self.service.control(ServiceCandidate.from_dict(parameters['candidate']), parameters['verb'], is_current)
        except (OSError, ValueError):
            return ActionResult('failed', 'Não foi possível observar ou verificar este serviço. / Cannot observe or verify this service.')
