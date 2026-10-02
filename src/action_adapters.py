"""Compatibility adapters retain backend discovery and verification invariants."""

from .action_contract import ActionDefinition, ActionResult
from .display_actions import DisplayMode
from .package_actions import PackageCandidate, _query


class PackageAdapter:
    def __init__(self, service, install=False):
        self.service, self.install = service, install
        self.definition = ActionDefinition('packages.install' if install else 'packages.search', install,
            'change' if install else 'read', 'pkexec' if install else 'user',
            'No automatic package rollback; package-manager state is verified after the transaction.')

    def prepare(self, parameters):
        if self.install:
            candidate = PackageCandidate.from_dict(parameters)
            if not candidate.version or not candidate.source:
                raise ValueError('An exact repository candidate is required')
            return candidate.to_dict(), candidate.name + '=' + candidate.version, 'change'
        if set(parameters) != {'query'} or _query(parameters['query']) is None:
            raise ValueError('Invalid package query')
        return parameters, 'configured repositories', 'read'

    def execute(self, parameters, is_current):
        if self.install:
            ok, text = self.service.install(PackageCandidate.from_dict(parameters), is_current=is_current)
            return ActionResult('done' if ok else 'failed', text, verified=ok)
        data, error = self.service.search(parameters['query'])
        return ActionResult('failed' if error else 'done', error, data=data, verified=not error)


class DisplayAdapter:
    def __init__(self, service, apply=False):
        self.service, self.apply = service, apply
        self.definition = ActionDefinition('display.apply_mode' if apply else 'display.list_modes', apply,
            'change' if apply else 'read', 'user', 'Independent 15-second display recovery; keep requires visual confirmation.')

    def prepare(self, parameters):
        if self.apply:
            mode = DisplayMode.from_dict(parameters)
            return mode.to_dict(), '{} {}x{}@{:g}'.format(mode.output, mode.width, mode.height, mode.refresh), 'change'
        if parameters:
            raise ValueError('Monitor discovery has no arbitrary arguments')
        return {}, 'current display session', 'read'

    def execute(self, parameters, is_current):
        if self.apply:
            change, error = self.service.apply_mode(DisplayMode.from_dict(parameters), timeout=15, is_current=is_current)
            return ActionResult('pending' if change else 'failed', error, change=change)
        data, error = self.service.list_modes()
        return ActionResult('failed' if error else 'done', error, data=data, verified=not error)
