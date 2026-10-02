"""Read-only system evidence, composed independently of distribution names."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import platform
import shutil
import time
from typing import Tuple

from .schema_validation import validate_schema

PACKAGE_TOOLS = {'apt': 'apt-get', 'xbps': 'xbps-install', 'pacman': 'pacman',
                 'dnf': 'dnf', 'zypper': 'zypper', 'apk': 'apk', 'portage': 'emerge'}
SERVICE_TOOLS = {'systemd': 'systemctl', 'runit': 'sv', 'openrc': 'rc-service',
                 'dinit': 'dinitctl', '66': '66'}
TOOLS = tuple(dict.fromkeys((*PACKAGE_TOOLS.values(), *SERVICE_TOOLS.values(),
                            'swaymsg', 'xrandr', 'nmcli', 'networkctl', 'dhcpcd',
                            'pipewire', 'pulseaudio', 'pactl', 'wpctl', 'lsblk', 'findmnt')))


@dataclass(frozen=True)
class Evidence:
    source: str
    value: str


@dataclass(frozen=True)
class Component:
    identifier: str = 'unknown'
    state: str = 'unknown'
    evidence: Tuple[Evidence, ...] = ()

    @property
    def active(self):
        return self.state == 'active'


@dataclass(frozen=True)
class SystemContext:
    distro_id: str
    pretty_name: str
    id_like: Tuple[str, ...]
    version: str
    kernel: str
    architecture: str
    package_manager: Component
    service_manager: Component
    session: Component
    compositor: Component
    audio: Component
    network: Component
    tools: Tuple[Tuple[str, str], ...]
    observed_at: float

    def to_dict(self):
        data = asdict(self)
        data.update(schema_version='0.1', id_like=list(self.id_like),
                    tools=[{'name': name, 'path': path} for name, path in self.tools])
        return validate_schema(data, 'system-context')

    @property
    def revision(self):
        data = self.to_dict()
        data.pop('observed_at')
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @classmethod
    def from_dict(cls, value):
        data = validate_schema(value, 'system-context')
        data.pop('schema_version')
        for key in ('package_manager', 'service_manager', 'session', 'compositor', 'audio', 'network'):
            item = data[key]
            data[key] = Component(item['identifier'], item['state'],
                                  tuple(Evidence(**evidence) for evidence in item['evidence']))
        data['tools'] = tuple((item['name'], item['path']) for item in data['tools'])
        data['id_like'] = tuple(data['id_like'])
        return cls(**data)

    def summary(self):
        return '{}; packages: {} ({}); services: {} ({}); session: {} ({}).'.format(
            self.pretty_name, self.package_manager.identifier, self.package_manager.state,
            self.service_manager.identifier, self.service_manager.state,
            self.session.identifier, self.session.state,
        )


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            return stream.read(4097)[:4096].strip()
    except OSError:
        return ''


def detect_system_context(distro=None, *, environ=None, which=shutil.which, read_text=_read,
                          exists=os.path.exists, clock=time.time, kernel=None, architecture=None):
    """No subprocesses, network calls, model calls or privileged reads."""
    if distro is None:
        from .offline_assistant import detect_distro
        distro = detect_distro(which=which)
    env = os.environ if environ is None else environ
    installed = tuple((name, str(path)) for name in TOOLS for path in (which(name),) if path)
    tool_paths = dict(installed)
    package = getattr(distro, 'pkg_manager', 'unknown')
    if PACKAGE_TOOLS.get(package) in tool_paths:
        package_component = Component(package, 'installed', (Evidence('executable', tool_paths[PACKAGE_TOOLS[package]]),))
    else:
        candidates = [name for name, tool in PACKAGE_TOOLS.items() if tool in tool_paths]
        package_component = (Component(candidates[0], 'installed',
                             (Evidence('executable', tool_paths[PACKAGE_TOOLS[candidates[0]]]),))
                             if len(candidates) == 1 else Component(evidence=tuple(Evidence('installed', item) for item in candidates)))

    pid1 = str(read_text('/proc/1/comm')).strip()[:128]
    managers = {'systemd': 'systemd', 'runit': 'runit', 'runit-init': 'runit',
                'openrc-init': 'openrc', 'dinit': 'dinit'}
    manager = managers.get(pid1, '')
    if manager:
        service_component = Component(manager, 'active', (Evidence('/proc/1/comm', pid1),))
    elif exists('/run/systemd/system'):
        service_component = Component('systemd', 'active', (Evidence('runtime marker', '/run/systemd/system'),))
    elif read_text('/run/openrc/softlevel'):
        service_component = Component('openrc', 'active', (Evidence('runtime marker', '/run/openrc/softlevel'),))
    else:
        available = [name for name, executable in SERVICE_TOOLS.items() if executable in tool_paths]
        service_component = (Component(available[0], 'installed', (Evidence('executable', tool_paths[SERVICE_TOOLS[available[0]]]),))
                             if len(available) == 1 else Component(evidence=tuple(Evidence('installed', item) for item in available)))

    wayland = env.get('XDG_SESSION_TYPE', '').lower() == 'wayland' or bool(env.get('WAYLAND_DISPLAY') or env.get('SWAYSOCK'))
    session = Component('wayland', 'observed', (Evidence('session environment', 'wayland'),)) if wayland else (
        Component('x11', 'observed', (Evidence('session environment', 'DISPLAY'),)) if env.get('DISPLAY') else Component())
    desktops = env.get('XDG_CURRENT_DESKTOP', '').lower().replace(':', ' ').split()
    compositor = Component('sway', 'inferred', (Evidence('session environment', 'SWAYSOCK'),)) if env.get('SWAYSOCK') else Component()
    if compositor.identifier == 'unknown':
        found = [name for name in ('gnome', 'kde', 'plasma', 'qtile', 'xfce') if name in desktops]
        if len(found) == 1:
            compositor = Component(found[0], 'inferred', (Evidence('XDG_CURRENT_DESKTOP', found[0]),))

    def installed_component(options):
        found = [name for name in options if name in tool_paths]
        return (Component(found[0], 'installed', (Evidence('executable', tool_paths[found[0]]),))
                if len(found) == 1 else Component(evidence=tuple(Evidence('installed', item) for item in found)))

    context = SystemContext(
        getattr(distro, 'distro_id', 'unknown'), getattr(distro, 'pretty_name', 'Linux'),
        tuple(getattr(distro, 'id_like', ())), getattr(distro, 'version_id', ''),
        kernel if kernel is not None else platform.release(),
        architecture if architecture is not None else platform.machine(),
        package_component, service_component, session, compositor,
        installed_component(('pipewire', 'pulseaudio')),
        installed_component(('nmcli', 'networkctl', 'dhcpcd')), installed, float(clock()),
    )
    context.to_dict()
    return context


def compatible_distro(distro, context):
    """Update a legacy snapshot only from positively observed runtime evidence."""
    from dataclasses import replace
    from .offline_assistant import SERVICE_MANAGERS
    if not context.service_manager.active:
        return distro
    manager = context.service_manager.identifier
    return replace(distro, service_manager=manager, service_manager_verified=True,
                   svc=SERVICE_MANAGERS.get(manager, {}))
