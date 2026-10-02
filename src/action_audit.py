"""Private, bounded operation events. Outputs and human messages are not logged."""

from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import stat
import time

from .schema_validation import validate_schema
from .storage import atomic_json_write, json_lock

_sink = ContextVar('action_event_sink', default=None)
MAX_EVENTS = 512
MAX_BYTES = 4 * 1024 * 1024


def command_detail(argv, ok):
    parts = []
    redact_next = False
    for item in argv[:32]:
        value = str(item)
        if redact_next:
            parts.append('[redacted]')
            redact_next = False
            continue
        redact_next = value.lower().lstrip('-') in {'password', 'passwd', 'token', 'secret', 'api-key', 'api_key'}
        value = re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[redacted]@', value)
        value = re.sub(r'(?i)(password|passwd|token|secret|api[_-]?key)([=:]).*', r'\1\2[redacted]', value)
        value = ''.join(char for char in value if ord(char) >= 32 and ord(char) != 127)
        parts.append(value[:256])
    return ('ok: ' if ok else 'failed: ') + ' '.join(parts)


def record_command(argv, ok):
    sink = _sink.get()
    if sink is not None:
        try:
            sink('command', command_detail(argv, ok))
        except (OSError, ValueError):
            # An audit fault must never prevent rollback or effect verification.
            pass


class ActionAudit:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    def _read(self):
        try:
            fd = os.open(str(self.path), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return []
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES
                    or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
                raise ValueError('Audit must be a regular private file')
            value = json.loads(stream.read(MAX_BYTES + 1))
        if type(value) is not list or len(value) > MAX_EVENTS:
            raise ValueError('Invalid audit collection')
        return [validate_schema(event, 'audit-event') for event in value]

    def append(self, operation_id, session_id, action_id, resource, risk, phase, detail=''):
        event = validate_schema(dict(schema_version='0.1', operation_id=operation_id,
            session_id=session_id, action_id=action_id, resource=resource, risk=risk,
            phase=phase, detail=detail[:4096], timestamp=float(self.clock())), 'audit-event')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with json_lock(self.path):
            events = self._read()
            events.append(event)
            atomic_json_write(self.path, events[-MAX_EVENTS:])

    def events(self, session_id, operation_id=None):
        with json_lock(self.path):
            return [event for event in self._read() if event['session_id'] == session_id
                    and (operation_id is None or event['operation_id'] == operation_id)]

    def render(self, session_id, operation_id=None):
        events = self.events(session_id, operation_id)
        if not events:
            return 'Sem operações registadas nesta conversa. / No operations in this conversation.'
        if operation_id:
            return '\n'.join('{}: {}'.format(event['phase'], event['detail'] or event['resource']) for event in events)
        latest = {event['operation_id']: event for event in events}
        lines = []
        for identifier, event in list(latest.items())[-20:]:
            phase = event['phase']
            if phase in {'running', 'command'}:
                phase = 'unfinished / verificar estado'
            elif phase == 'pending':
                phase = 'unconfirmed / verificar estado'
            lines.append('{} | {} | {} | {}'.format(identifier, event['action_id'], event['resource'], phase))
        return '\n'.join(lines)
