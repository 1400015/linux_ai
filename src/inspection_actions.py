"""Read-only capabilities exercise the contract without privileged execution."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .action_audit import record_command
from .action_contract import ActionDefinition, ActionResult
from .package_actions import _run


class ChecksumAdapter:
    definition = ActionDefinition('files.checksum', False, 'read', 'user', 'Read only; SHA-256 verifies bytes, not their trustworthiness.')

    def __init__(self, allowed_dirs=None, clock=time.monotonic):
        self.allowed_dirs = tuple(Path(item).resolve() for item in (allowed_dirs or (Path.home(),)))
        self.clock = clock

    def prepare(self, parameters):
        if set(parameters) != {'path'} or type(parameters['path']) is not str or not 0 < len(parameters['path']) <= 4096:
            raise ValueError('Invalid checksum path')
        if any(ord(char) < 32 for char in parameters['path']):
            raise ValueError('Invalid checksum path')
        path = Path(parameters['path']).expanduser()
        if not path.is_absolute():
            raise ValueError('Use an absolute file path')
        path = path.resolve(strict=True)
        if not any(path == allowed or allowed in path.parents for allowed in self.allowed_dirs):
            raise ValueError('Checksum path is outside the permitted directories')
        metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8 * 1024**3:
            raise ValueError('Checksum requires a bounded regular file')
        identity = [metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns]
        return {'path': str(path), 'identity': identity}, str(path), 'read'

    def execute(self, parameters, is_current):
        path = Path(parameters['path'])
        # Revalidate containment after preparation; no root privileges are used.
        if str(path.resolve(strict=True)) != str(path):
            return ActionResult('failed', 'The checksum path changed after preparation.')
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns] != parameters['identity']:
                return ActionResult('failed', 'The checksum file changed after preparation.')
            if not stat.S_ISREG(before.st_mode) or before.st_size > 8 * 1024**3:
                return ActionResult('failed', 'Checksum requires a regular file up to 8 GiB.')
            deadline, count, digest = self.clock() + 60, 0, hashlib.sha256()
            while True:
                if not is_current() or self.clock() > deadline:
                    return ActionResult('failed', 'Checksum cancelled or time limit exceeded.')
                block = stream.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > before.st_size:
                    return ActionResult('failed', 'File grew while calculating its checksum.')
                digest.update(block)
            after = os.fstat(stream.fileno())
            current = os.stat(path, follow_symlinks=False)
        def identity(item):
            return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if count != before.st_size or identity(before) != identity(after) or identity(before) != identity(current):
            return ActionResult('failed', 'File changed while calculating its checksum.')
        result = digest.hexdigest()
        return ActionResult('done', 'SHA-256: {}\n{}'.format(result, path), data={'sha256': result, 'bytes': count}, verified=True)


class StorageAdapter:
    definition = ActionDefinition('storage.list', False, 'read', 'user', 'Read-only inventory. ISO writing remains unavailable pending VM validation.')

    def __init__(self, runner=None):
        self.runner = runner or _run

    def prepare(self, parameters):
        if parameters:
            raise ValueError('Storage inventory accepts no arbitrary options')
        return {}, 'local block-device inventory', 'read'

    def execute(self, parameters, is_current):
        if not is_current():
            return ActionResult('failed', 'Storage query cancelled.')
        argv = ['lsblk', '--json', '--bytes', '--output', 'NAME,PATH,TYPE,SIZE,RO,RM,TRAN,MODEL,MAJ:MIN,MOUNTPOINTS']
        ok, output = self.runner(argv, timeout=10)
        record_command(argv, ok)
        if not ok or type(output) is not str or len(output.encode()) > 1024 * 1024:
            return ActionResult('failed', 'Cannot read the block-device inventory.')
        try:
            value = json.loads(output)
            if type(value) is not dict or type(value.get('blockdevices')) is not list:
                raise ValueError('Invalid topology')
            total = [0]

            def parse(node, depth=0):
                total[0] += 1
                if total[0] > 512 or depth > 16 or type(node) is not dict:
                    raise ValueError('Storage topology exceeds the limit')
                path = node.get('path')
                size = node.get('size')
                if (not {'path', 'type', 'size', 'ro', 'rm', 'mountpoints', 'maj:min'}.issubset(node)
                        or type(node['ro']) not in (bool, int) or node['ro'] not in (0, 1)
                        or type(node['rm']) not in (bool, int) or node['rm'] not in (0, 1)
                        or type(node['maj:min']) is not str or not re.fullmatch(r'\d{1,6}:\d{1,6}', node['maj:min'])):
                    raise ValueError('Missing or invalid storage identity evidence')
                if (type(path) is not str or not re.fullmatch(r'/dev/[A-Za-z0-9_./-]{1,128}', path)
                        or '..' in path.split('/') or type(size) is not int or not 0 <= size < 2**64):
                    raise ValueError('Invalid block-device identity')
                mounts = node.get('mountpoints', [])
                children = node.get('children', [])
                if (type(mounts) is not list or len(mounts) > 128 or type(children) is not list
                        or any(item is not None and (type(item) is not str or len(item) > 4096) for item in mounts)):
                    raise ValueError('Invalid mount information')
                parsed = [parse(child, depth + 1) for child in children]
                return {'path': path, 'size': size, 'type': str(node.get('type', 'unknown'))[:32],
                        'read_only': node.get('ro') in (True, 1), 'removable': node.get('rm') in (True, 1),
                        'mounted': any(mounts) or any(child['mounted'] for child in parsed), 'children': parsed}

            devices = [parse(node) for node in value['blockdevices']]
        except (ValueError, TypeError, RecursionError):
            return ActionResult('failed', 'Storage topology is incomplete or invalid.')
        lines = ['{} | {} bytes | {} | mounted={} | removable={} | read_only={}'.format(
            node['path'], node['size'], node['type'], node['mounted'], node['removable'], node['read_only']) for node in devices]
        lines.append('A gravação de ISOs ainda não está disponível. / ISO writing is not available yet.')
        return ActionResult('done', '\n'.join(lines), data=devices, verified=True)
