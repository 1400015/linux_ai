"""Offline assistant: answers and local tasks without any API.

Used when no API key is configured or when the configured provider cannot be
reached. Everything here is local: distribution detection from
``/etc/os-release``, a small knowledge base, and *trusted* command templates.

Privileged changes are never executed by this module: it only builds the
commands (as argv lists, so there is no shell interpolation) and the UI runs
them through ``pkexec`` after an explicit confirmation.
"""

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# Command templates (argv lists; may contain the {pkg}/{svc}/{tz}/{host}
# placeholders). Keeping them as lists avoids any shell-injection surface.
# --------------------------------------------------------------------------

PKG_MANAGERS = {
    "xbps": {
        "install": ["xbps-install", "-Sy", "{pkg}"],
        "remove": ["xbps-remove", "-Ry", "{pkg}"],
        "search": ["xbps-query", "-Rs", "{pkg}"],
        "update": [["xbps-install", "-Su"]],
        "clean": [["xbps-remove", "-O"], ["xbps-remove", "-o"]],
    },
    "apt": {
        "install": ["apt-get", "install", "-y", "{pkg}"],
        "remove": ["apt-get", "remove", "-y", "{pkg}"],
        "search": ["apt-cache", "search", "{pkg}"],
        "update": [["apt-get", "update"], ["apt-get", "upgrade", "-y"]],
        "clean": [["apt-get", "autoremove", "-y"], ["apt-get", "clean"]],
    },
    "dnf": {
        "install": ["dnf", "install", "-y", "{pkg}"],
        "remove": ["dnf", "remove", "-y", "{pkg}"],
        "search": ["dnf", "search", "{pkg}"],
        "update": [["dnf", "upgrade", "-y"]],
        "clean": [["dnf", "autoremove", "-y"], ["dnf", "clean", "all"]],
    },
    "pacman": {
        "install": ["pacman", "-S", "--noconfirm", "{pkg}"],
        "remove": ["pacman", "-Rns", "--noconfirm", "{pkg}"],
        "search": ["pacman", "-Ss", "{pkg}"],
        "update": [["pacman", "-Syu", "--noconfirm"]],
        "clean": [["pacman", "-Sc", "--noconfirm"]],
    },
    "zypper": {
        "install": ["zypper", "install", "-y", "{pkg}"],
        "remove": ["zypper", "remove", "-y", "{pkg}"],
        "search": ["zypper", "search", "{pkg}"],
        "update": [["zypper", "update", "-y"]],
        "clean": [["zypper", "clean", "-a"]],
    },
    "apk": {
        "install": ["apk", "add", "{pkg}"],
        "remove": ["apk", "del", "{pkg}"],
        "search": ["apk", "search", "{pkg}"],
        "update": [["apk", "update"], ["apk", "upgrade"]],
        "clean": [["apk", "cache", "clean"]],
    },
}

SERVICE_MANAGERS = {
    "systemd": {
        "enable": ["systemctl", "enable", "--now", "{svc}"],
        "disable": ["systemctl", "disable", "--now", "{svc}"],
        "start": ["systemctl", "start", "{svc}"],
        "stop": ["systemctl", "stop", "{svc}"],
        "restart": ["systemctl", "restart", "{svc}"],
        "status": ["systemctl", "status", "{svc}"],
        "list": ["systemctl", "list-units", "--type=service"],
    },
    "runit": {
        "enable": ["ln", "-s", "/etc/sv/{svc}", "/var/service/"],
        "disable": ["rm", "-f", "/var/service/{svc}"],
        "start": ["sv", "up", "{svc}"],
        "stop": ["sv", "down", "{svc}"],
        "restart": ["sv", "restart", "{svc}"],
        "status": ["sv", "status", "{svc}"],
        "list": ["ls", "/var/service"],
    },
    "openrc": {
        "enable": ["rc-update", "add", "{svc}", "default"],
        "disable": ["rc-update", "del", "{svc}"],
        "start": ["rc-service", "{svc}", "start"],
        "stop": ["rc-service", "{svc}", "stop"],
        "restart": ["rc-service", "{svc}", "restart"],
        "status": ["rc-service", "{svc}", "status"],
        "list": ["rc-status"],
    },
}

# distro id / id_like -> package manager
_DISTRO_PKG = {
    "void": "xbps",
    "debian": "apt", "ubuntu": "apt", "linuxmint": "apt", "pop": "apt",
    "raspbian": "apt", "kali": "apt", "elementary": "apt",
    "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "rocky": "dnf",
    "almalinux": "dnf",
    "arch": "pacman", "manjaro": "pacman", "artix": "pacman",
    "opensuse": "zypper", "opensuse-leap": "zypper",
    "opensuse-tumbleweed": "zypper", "sles": "zypper",
    "alpine": "apk",
}

# id_like values that imply a package manager
_LIKE_PKG = {
    "debian": "apt", "ubuntu": "apt", "arch": "pacman", "fedora": "dnf",
    "rhel": "dnf", "suse": "zypper", "alpine": "apk", "void": "xbps",
}


def read_os_release(path: str = "/etc/os-release") -> Dict[str, str]:
    """Parse ``/etc/os-release`` into a dict (empty when unavailable)."""
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


@dataclass
class DistroInfo:
    """Distribution facts and the commands that apply to it."""

    pretty_name: str = "Linux"
    distro_id: str = "unknown"
    id_like: Tuple[str, ...] = ()
    pkg_manager: str = "unknown"
    service_manager: str = "unknown"
    kernel: str = ""
    pkg: Dict[str, Any] = field(default_factory=dict)
    svc: Dict[str, Any] = field(default_factory=dict)
    timezone_argv: Optional[List[str]] = None
    hostname_argv: Optional[List[str]] = None


def detect_distro(os_release: Optional[Dict[str, str]] = None,
                  which=shutil.which,
                  is_systemd_running: Optional[bool] = None) -> DistroInfo:
    """Detect the distribution, package manager and service manager.

    All probes are injectable so the logic can be unit-tested off-Linux.
    """
    release = os_release if os_release is not None else read_os_release()
    distro_id = release.get("ID", "unknown").lower()
    id_like = tuple(
        part.strip().lower()
        for part in release.get("ID_LIKE", "").split()
        if part.strip()
    )
    pretty = release.get("PRETTY_NAME") or release.get("NAME") or distro_id

    pkg_manager = "unknown"
    for name in (distro_id, *id_like):
        if name in _DISTRO_PKG:
            pkg_manager = _DISTRO_PKG[name]
            break
    if pkg_manager == "unknown":
        for name in id_like:
            if name in _LIKE_PKG:
                pkg_manager = _LIKE_PKG[name]
                break

    if is_systemd_running is None:
        is_systemd_running = os.path.isdir("/run/systemd/system")
    # Prefer what is *actually running* over what merely happens to be
    # installed (a runit system can have systemctl installed for chroots).
    if is_systemd_running:
        service_manager = "systemd"
    elif which("sv"):
        service_manager = "runit"
    elif which("rc-service"):
        service_manager = "openrc"
    elif which("systemctl"):
        service_manager = "systemd"
    elif distro_id == "void" or "void" in id_like:
        service_manager = "runit"
    elif distro_id == "alpine" or "alpine" in id_like:
        service_manager = "openrc"
    elif pkg_manager in ("apt", "dnf", "pacman", "zypper"):
        service_manager = "systemd"
    else:
        service_manager = "unknown"

    pkg = PKG_MANAGERS.get(pkg_manager, {})
    svc = SERVICE_MANAGERS.get(service_manager, {})

    timezone_argv = ["timedatectl", "set-timezone", "{tz}"] if service_manager == "systemd" else None
    hostname_argv = ["hostnamectl", "set-hostname", "{host}"] if service_manager == "systemd" else None

    return DistroInfo(
        pretty_name=pretty,
        distro_id=distro_id,
        id_like=id_like,
        pkg_manager=pkg_manager,
        service_manager=service_manager,
        kernel=release.get("_KERNEL", ""),
        pkg=pkg,
        svc=svc,
        timezone_argv=timezone_argv,
        hostname_argv=hostname_argv,
    )


@dataclass
class Command:
    """A single trusted command, represented as an argv list."""

    argv: List[str]
    privileged: bool = False
    description: str = ""

    def display(self) -> str:
        return shlex.join(self.argv)


@dataclass
class Reply:
    """Result of an offline query."""

    text: str
    commands: List[Command] = field(default_factory=list)


from .i18n import OFFLINE_TEXTS as _TEXTS, OFFLINE_SERVICE_ACTIONS as _SERVICE_ACTIONS, offline_text as _t

# Order matters: check the more specific verbs first so "restart" is not
# mistaken for "start". Word boundaries keep "start" out of "restart".
_ACTION_WORDS = [
    ("disable", ("disable", "desativar")),
    ("restart", ("restart", "reiniciar")),
    ("stop", ("stop", "parar")),
    ("enable", ("enable", "ativar")),
    ("start", ("start", "iniciar")),
]

def _service_action_word(lang: str, action: str) -> str:
    catalog = _SERVICE_ACTIONS.get(lang) or _SERVICE_ACTIONS["en"]
    return catalog[action]


def _fill(argv: List[str], **replacements: str) -> List[str]:
    """Replace `{placeholder}` inside every argv element."""
    filled = []
    for part in argv:
        for key, value in replacements.items():
            part = part.replace("{" + key + "}", value)
        filled.append(part)
    return filled


# --------------------------------------------------------------------------
# Intent matching and argument extraction
# --------------------------------------------------------------------------

_KEYWORDS: Dict[str, List[str]] = {
    "update": ["update", "upgrade", "atualiz", "actualiz", "upgrade the system",
               "atualizar o sistema"],
    "install": ["install", "instalar", "instala"],
    "remove": ["remove", "uninstall", "remover", "desinstalar"],
    "search": ["search package", "find package", "procurar pacote",
               "pesquisar pacote", "search for a package"],
    "services": ["service", "serviço", "servico", "systemd", "runit", "openrc",
                 "ativar serviço", "ativar servico", "enable service",
                 "start service", "restart service"],
    "timezone": ["timezone", "time zone", "fuso", "timedatectl", "fuso horário",
                 "fuso horario"],
    "hostname": ["hostname", "nome da máquina", "nome da maquina",
                 "nome do computador"],
    "disk": ["disk", "disco", "df -h", "espaço em disco", "espaco em disco",
             "storage"],
    "memory": ["memory", "memória", "memoria", " ram ", "free -h"],
    "network": ["network", "rede", "wifi", "dns", "ip addr", "ping", "internet"],
    "firewall": ["firewall", "ufw", "nftables", "iptables", "firewalld"],
    "shell": ["shell", "chsh", "zsh", "default shell"],
    "alias": ["alias", "aliases"],
    "clean": ["clean", "limpar", "cache", "temporários", "temporarios",
              "cleanup", "libertar espaço", "free space"],
    "autostart": ["autostart", "arranque automático", "arranque automatico",
                  "startup", "iniciar com o sistema"],
    "distro": ["distro", "distribution", "distribuição", "versão", "versao",
               "kernel", "what linux", "que linux", "que distro"],
    "help": ["help", "ajuda", "what can you do", "o que consegues",
             "o que podes", "comandos disponíveis"],
}

_INSTALL_RE = re.compile(
    r"(?:install|instalar|instala)\s+([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(
    r"(?:remove|uninstall|remover|desinstalar)\s+([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(
    r"(?:search|procurar|pesquisar)\s+(?:for\s+|por\s+)?([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_SVC_RE = re.compile(
    r"(?:enable|start|restart|stop|disable|ativar|iniciar|reiniciar|parar|desativar)"
    r"\s+(?:service\s+|serviço\s+|servico\s+|o\s+serviço\s+|o\s+servico\s+)?"
    r"([A-Za-z0-9][A-Za-z0-9@._:-]{0,63})",
    re.IGNORECASE,
)
_TZ_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_+-]*/[A-Za-z0-9_+/-]+)\b")
_HOST_RE = re.compile(
    r"(?:hostname|nome da máquina|nome da maquina|nome do computador)"
    r"\s*(?:to|para|como|:|=)?\s+([A-Za-z0-9][A-Za-z0-9-]{0,62})",
    re.IGNORECASE,
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+:-]{0,62}$")
_TZ_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+$")
_HOST_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")

_STOPWORDS = {
    "a", "an", "the", "um", "uma", "o", "os", "as", "package", "pacote",
    "service", "serviço", "servico", "my", "meu", "minha",
}


def _match_intent(low: str) -> Tuple[Optional[str], int]:
    best: Optional[str] = None
    best_score = 0
    for intent, words in _KEYWORDS.items():
        score = 0
        for word in words:
            if word in low:
                score += 1
        if score > best_score:
            best = intent
            best_score = score
    return best, best_score


def _valid(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token = token.strip()
    if token.lower() in _STOPWORDS:
        return None
    return token if _SAFE_TOKEN.match(token) else None


class OfflineAssistant:
    """Local, API-free assistant for the running distribution."""

    def __init__(self, system_utils=None, config=None,
                 os_release: Optional[Dict[str, str]] = None):
        self.system_utils = system_utils
        self.config = config
        self._distro = detect_distro(os_release)

    @property
    def distro(self) -> DistroInfo:
        return self._distro

    # -- public API --------------------------------------------------------

    def handle(self, message: str, lang: str = "en") -> Reply:
        """Answer `message` using only local knowledge."""
        if lang not in _TEXTS:
            lang = "en"
        text = (message or "").strip()
        low = " " + text.lower() + " "
        distro = self._distro

        if not text:
            return Reply(self._help(distro, lang))

        intent, score = _match_intent(low)
        if not intent or score == 0:
            return Reply(self._help(distro, lang))

        dispatch = {
            "help": lambda: Reply(self._help(distro, lang)),
            "distro": lambda: Reply(self._distro_reply(distro, lang)),
            "update": lambda: self._update_reply(distro, lang),
            "install": lambda: self._install_reply(distro, lang, text),
            "remove": lambda: self._remove_reply(distro, lang, text),
            "search": lambda: self._search_reply(distro, lang, text),
            "services": lambda: self._services_reply(distro, lang, text),
            "timezone": lambda: self._timezone_reply(distro, lang, text),
            "hostname": lambda: self._hostname_reply(distro, lang, text),
            "disk": lambda: self._disk_reply(distro, lang),
            "memory": lambda: self._memory_reply(distro, lang),
            "network": lambda: Reply(_t(lang, "network")),
            "firewall": lambda: Reply(self._firewall_text(distro, lang)),
            "shell": lambda: Reply(_t(lang, "shell")),
            "alias": lambda: Reply(_t(lang, "alias")),
            "clean": lambda: self._clean_reply(distro, lang),
            "autostart": lambda: Reply(_t(lang, "autostart")),
        }
        return dispatch[intent]()

    @staticmethod
    def run_privileged(command: Command, timeout: int = 120):
        """Run a privileged command via pkexec. Returns (ok, output)."""
        if not command.privileged:
            raise ValueError("run_privileged() expects a privileged command")
        try:
            proc = subprocess.run(
                ["pkexec", *command.argv],
                capture_output=True, text=True, timeout=timeout,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode == 0, output.strip()
        except FileNotFoundError:
            return False, "pkexec is not installed (install polkit)."
        except subprocess.TimeoutExpired:
            return False, "The command timed out."
        except OSError as exc:
            return False, str(exc)

    # -- internals ---------------------------------------------------------

    def _run(self, command: str) -> Optional[str]:
        """Run a safe diagnostic via the sandboxed SystemUtils, if available."""
        if self.system_utils is None:
            return None
        try:
            ok, output = self.system_utils.execute_command(command)
        except Exception:
            return None
        return output.strip() if ok else None

    def _help(self, distro: DistroInfo, lang: str) -> str:
        return _t(lang, "help", pretty=distro.pretty_name,
                  pkg=distro.pkg_manager, svc=distro.service_manager)

    def _distro_reply(self, distro: DistroInfo, lang: str) -> str:
        like = f", like: {'/'.join(distro.id_like)}" if distro.id_like else ""
        kernel = distro.kernel or self._run("uname -r") or "unknown"
        return _t(lang, "distro", pretty=distro.pretty_name,
                  distro_id=distro.distro_id, like=like,
                  pkg=distro.pkg_manager, svc=distro.service_manager,
                  kernel=kernel)

    def _no_pkg(self, lang: str) -> Reply:
        return Reply(self._help(self._distro, lang))

    def _update_reply(self, distro: DistroInfo, lang: str) -> Reply:
        jobs = distro.pkg.get("update") or []
        if not jobs:
            return self._no_pkg(lang)
        cmds = "\n".join(shlex.join(job) for job in jobs)
        commands = [Command(argv=list(job), privileged=True,
                            description="Update the system")
                    for job in jobs]
        return Reply(_t(lang, "update", pretty=distro.pretty_name, cmds=cmds),
                     commands)

    def _install_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        template = distro.pkg.get("install")
        if not template:
            return self._no_pkg(lang)
        match = _INSTALL_RE.search(text)
        package = _valid(match.group(1)) if match else None
        if package:
            argv = _fill(template, pkg=package)
            cmd = shlex.join(argv)
            return Reply(
                _t(lang, "install_named", pkg=package, cmd=cmd),
                [Command(argv=argv, privileged=True,
                         description=f"Install {package}")],
            )
        shown = shlex.join(template)
        return Reply(_t(lang, "install_generic", pretty=distro.pretty_name,
                          cmd=shown, example="htop"))

    def _remove_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        template = distro.pkg.get("remove")
        if not template:
            return self._no_pkg(lang)
        match = _REMOVE_RE.search(text)
        package = _valid(match.group(1)) if match else None
        if package:
            argv = _fill(template, pkg=package)
            return Reply(
                _t(lang, "remove_named", pkg=package, cmd=shlex.join(argv)),
                [Command(argv=argv, privileged=True,
                         description=f"Remove {package}")],
            )
        return Reply(_t(lang, "remove_generic", pretty=distro.pretty_name,
                          cmd=shlex.join(template)))

    def _search_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        template = distro.pkg.get("search")
        if not template:
            return self._no_pkg(lang)
        match = _SEARCH_RE.search(text)
        package = _valid(match.group(1)) if match else None
        if package:
            argv = _fill(template, pkg=package)
            return Reply(_t(lang, "search_named", pkg=package,
                              cmd=shlex.join(argv)))
        return Reply(_t(lang, "search_generic", pretty=distro.pretty_name,
                          cmd=shlex.join(template)))

    def _services_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        svc = distro.svc
        if not svc:
            return Reply(_t(lang, "services", pretty=distro.pretty_name,
                            svc=distro.service_manager,
                            list="N/A", status="N/A", enable="N/A",
                            start="N/A", restart="N/A", stop="N/A",
                            disable="N/A"))
        match = _SVC_RE.search(text)
        name = _valid(match.group(1)) if match else None
        match_low = (match.group(0).lower() if match else "")
        action = None
        for key, words in _ACTION_WORDS:
            if any(re.search(rf"\b{re.escape(word)}\b", match_low) for word in words):
                action = key
                break
        if name and action:
            argv = _fill(svc[action], svc=name)
            verb = _service_action_word(lang, action)
            return Reply(
                _t(lang, "service_action", action=verb,
                   svc=name, cmd=shlex.join(argv)),
                [Command(argv=argv, privileged=True,
                         description=f"{action} {name}")],
            )
        if match and not name:
            return Reply(_t(lang, "service_unknown"))
        fmt = {k: shlex.join(v) for k, v in svc.items()}
        return Reply(_t(lang, "services", pretty=distro.pretty_name,
                          svc=distro.service_manager, **fmt))

    def _timezone_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        match = _TZ_RE.search(text)
        tz = match.group(1) if match and _TZ_TOKEN.match(match.group(1)) else None
        if tz and distro.timezone_argv:
            argv = _fill(distro.timezone_argv, tz=tz)
            return Reply(
                _t(lang, "set_timezone", tz=tz, cmd=shlex.join(argv)),
                [Command(argv=argv, privileged=True,
                         description=f"Set timezone to {tz}")],
            )
        if not distro.timezone_argv:
            return Reply(_t(lang, "timezone_manual", svc=distro.service_manager))
        cmds = shlex.join(distro.timezone_argv)
        return Reply(_t(lang, "timezone", pretty=distro.pretty_name,
                          svc=distro.service_manager, cmds=cmds))

    def _hostname_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        match = _HOST_RE.search(text)
        host = match.group(1) if match and _HOST_TOKEN.match(match.group(1)) else None
        if host and distro.hostname_argv:
            argv = _fill(distro.hostname_argv, host=host)
            return Reply(
                _t(lang, "set_hostname", host=host, cmd=shlex.join(argv)),
                [Command(argv=argv, privileged=True,
                         description=f"Set hostname to {host}")],
            )
        if not distro.hostname_argv:
            return Reply(_t(lang, "hostname_manual", svc=distro.service_manager))
        cmds = shlex.join(distro.hostname_argv)
        return Reply(_t(lang, "hostname", pretty=distro.pretty_name,
                          svc=distro.service_manager, cmds=cmds))

    def _disk_reply(self, distro: DistroInfo, lang: str) -> Reply:
        out = self._run("df -h")
        extra = "\n".join([
            "  df -h",
            "  du -sh ~/.cache /var/cache 2>/dev/null",
            "  du -xh --max-depth=1 ~ 2>/dev/null | sort -h",
        ])
        if out:
            return Reply(_t(lang, "disk", out=out, cmds=extra))
        return Reply(_t(lang, "disk_none", cmds=extra))

    def _memory_reply(self, distro: DistroInfo, lang: str) -> Reply:
        out = self._run("free -h")
        if out:
            return Reply(_t(lang, "memory", out=out))
        return Reply(_t(lang, "memory_none", cmds="  free -h"))

    def _firewall_text(self, distro: DistroInfo, lang: str) -> str:
        if distro.service_manager == "systemd":
            cmds = ("  firewall-cmd --state        # firewalld\n"
                    "  ufw status verbose          # ufw (Debian/Ubuntu)\n"
                    "  nft list ruleset            # nftables")
        else:
            cmds = ("  iptables -S                  # classic iptables\n"
                    "  nft list ruleset             # nftables")
        return _t(lang, "firewall", pretty=distro.pretty_name,
                  svc=distro.service_manager, cmds=cmds)

    def _clean_reply(self, distro: DistroInfo, lang: str) -> Reply:
        jobs = distro.pkg.get("clean") or []
        cmds = "\n".join(shlex.join(job) for job in jobs) if jobs else "  (no known package cache command)"
        return Reply(_t(lang, "clean", pretty=distro.pretty_name, cmds=cmds),
                     [Command(argv=list(job), privileged=True,
                              description="Clean package cache")
                      for job in jobs])
