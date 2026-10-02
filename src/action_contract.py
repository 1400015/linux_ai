"""Registered adapters, typed preparation and session-bound one-use authorization."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import secrets
import threading
import uuid

from .action_audit import _sink
from .schema_validation import validate_schema


@dataclass(frozen=True)
class ActionDefinition:
    identifier: str
    changes_system: bool
    risk: str
    privilege: str
    recovery: str


@dataclass(frozen=True)
class PreparedAction:
    operation_id: str
    session_id: str
    definition: ActionDefinition
    resource: str
    risk: str
    parameters_json: str

    @property
    def parameters(self):
        return json.loads(self.parameters_json)

    @property
    def fingerprint(self):
        data = [self.operation_id, self.session_id, asdict(self.definition),
                self.resource, self.risk, self.parameters_json]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()


@dataclass
class ActionResult:
    status: str
    text: str = ''
    data: object = None
    change: object = None
    verified: bool = False
    operation_id: str = ''
    trace: list = field(default_factory=list)

    @property
    def ok(self):
        return self.status in {'done', 'pending'}


class ActionExecutor:
    """Only code-registered adapters execute. Serialized plans are never grants."""

    def __init__(self, audit):
        self.audit = audit
        self._adapters = {}
        self._grants = {}
        self._prepared = {}
        self._lock = threading.Lock()
        self._closed = False

    def register(self, adapter):
        identifier = adapter.definition.identifier
        if identifier in self._adapters:
            raise ValueError('Duplicate action capability')
        self._adapters[identifier] = adapter

    @property
    def definitions(self):
        return {key: adapter.definition for key, adapter in self._adapters.items()}

    def prepare(self, action_id, parameters, session_id):
        request = validate_schema(dict(schema_version='0.1', action_id=action_id,
            parameters=parameters, session_id=session_id), 'action-request')
        if self._closed or action_id not in self._adapters:
            raise ValueError('Action is not available')
        adapter = self._adapters[action_id]
        parameters, resource, risk = adapter.prepare(request['parameters'])
        if risk not in {'read', 'change', 'high', 'destructive'}:
            raise ValueError('Invalid contextual risk')
        levels = {'read': 0, 'change': 1, 'high': 2, 'destructive': 3}
        if levels[risk] < levels[adapter.definition.risk]:
            raise ValueError('Adapter cannot lower the registered minimum risk')
        prepared = PreparedAction(uuid.uuid4().hex, session_id, adapter.definition, resource,
                                  risk, json.dumps(parameters, sort_keys=True, allow_nan=False))
        self.audit.append(prepared.operation_id, session_id, action_id, resource, risk,
                          'prepared', adapter.definition.recovery)
        with self._lock:
            if self._closed:
                raise ValueError('Action executor is closed')
            if len(self._prepared) >= 128:
                raise ValueError('Too many pending action preparations')
            self._prepared[prepared.operation_id] = prepared.fingerprint
        return prepared

    def authorize(self, prepared, session_id, *, explicit_request=False, confirmed_resource=None):
        if not isinstance(prepared, PreparedAction):
            raise ValueError('Serialized data cannot authorize execution')
        with self._lock:
            if (self._closed or explicit_request is not True or session_id != prepared.session_id
                    or self._prepared.get(prepared.operation_id) != prepared.fingerprint):
                raise ValueError('Authorization does not match the prepared human request')
            if prepared.risk in {'high', 'destructive'} and confirmed_resource != prepared.resource:
                raise ValueError('Explicit target confirmation is required for this risk')
            token = secrets.token_hex(32)
            self._grants = {key: value for key, value in self._grants.items() if value != prepared.fingerprint}
            self._grants[token] = prepared.fingerprint
            return token

    def execute(self, prepared, *, grant=None, is_current=lambda: True):
        if not isinstance(prepared, PreparedAction):
            return ActionResult('failed', 'Serialized data cannot execute an action.')
        with self._lock:
            valid = self._prepared.pop(prepared.operation_id, None) == prepared.fingerprint
            adapter = self._adapters.get(prepared.definition.identifier)
            valid = valid and adapter is not None and adapter.definition == prepared.definition
            authorized = not prepared.definition.changes_system or self._grants.pop(grant, None) == prepared.fingerprint
            available = not self._closed
        if not valid or not authorized or not available or not is_current():
            return ActionResult('failed', 'Action cancelled or authorization unavailable.', operation_id=prepared.operation_id)
        trace = []

        def sink(phase, detail=''):
            self.audit.append(prepared.operation_id, prepared.session_id, prepared.definition.identifier,
                              prepared.resource, prepared.risk, phase, detail)
            trace.append({'phase': phase, 'detail': detail})

        # Failure to persist this event prevents every backend mutation.
        try:
            sink('running')
        except (OSError, ValueError):
            return ActionResult('failed', 'Cannot persist the audit; action was not executed.', operation_id=prepared.operation_id)
        context_token = _sink.set(sink)
        try:
            result = self._adapters[prepared.definition.identifier].execute(prepared.parameters, is_current)
        except Exception:
            # Never interpolate arbitrary exceptions into persistent logs.
            result = ActionResult('failed', 'Backend failed; check the actual state before retrying.')
        finally:
            _sink.reset(context_token)
        if not isinstance(result, ActionResult) or result.status not in {'done', 'pending', 'failed'}:
            result = ActionResult('failed', 'Backend returned an invalid structured result.')
        elif result.status == 'done' and not result.verified:
            result.status = 'failed'
            result.text += '\nThe requested effect was not verified.'
        elif result.status == 'pending' and (not prepared.definition.changes_system or result.change is None):
            result = ActionResult('failed', 'Backend returned an invalid provisional change.')
        result.operation_id = prepared.operation_id
        result.trace = trace
        try:
            sink('pending' if result.status == 'pending' else 'verified' if result.verified else 'failed')
        except (OSError, ValueError):
            result.text += '\nAudit completion could not be saved; inspect the actual state.'
        if result.change is not None:
            result.change.audit_sink = sink
        return result

    def close(self):
        with self._lock:
            self._closed = True
            self._grants.clear()
            self._prepared.clear()

    def discard(self, prepared):
        with self._lock:
            self._prepared.pop(prepared.operation_id, None)
            self._grants = {key: value for key, value in self._grants.items() if value != prepared.fingerprint}
