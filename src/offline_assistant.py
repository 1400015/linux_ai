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

from .knowledge_base import knowledge_content, knowledge_for, package_manager_for
from .local_knowledge import (PROCEDURE_BY_ID, PROCEDURES, localized, normalize,
                              render_procedure, search_procedures)
from .device_actions import support_package
from .diagnostics import analyze, render_findings


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
        "clean": [["xbps-remove", "-O"]],
    },
    "apt": {
        "install": ["apt-get", "install", "-y", "{pkg}"],
        "remove": ["apt-get", "remove", "-y", "{pkg}"],
        "search": ["apt-cache", "search", "{pkg}"],
        "update": [["apt-get", "update"], ["apt-get", "upgrade", "-y"]],
        "clean": [["apt-get", "clean"]],
    },
    "dnf": {
        "install": ["dnf", "install", "-y", "{pkg}"],
        "remove": ["dnf", "remove", "-y", "{pkg}"],
        "search": ["dnf", "search", "{pkg}"],
        "update": [["dnf", "upgrade", "-y"]],
        "clean": [["dnf", "clean", "all"]],
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

_PACKAGE_EXECUTABLES = {
    "xbps": "xbps-install", "apt": "apt-get", "dnf": "dnf",
    "pacman": "pacman", "zypper": "zypper", "apk": "apk",
}
_COMPONENT_TOOLS = ("ip", "nmcli", "networkctl", "resolvectl", "dhcpcd",
                    "rfkill", "sv", "rc-service", "systemctl", "findmnt", "df", "free")


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
    version_id: str = ""
    available_tools: Tuple[str, ...] = ()
    package_manager_verified: bool = False
    service_manager_verified: bool = False


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

    pkg_manager = package_manager_for(distro_id, id_like)
    tools = tuple(name for name in (*_PACKAGE_EXECUTABLES.values(), *_COMPONENT_TOOLS)
                  if which(name))
    pkg_verified = _PACKAGE_EXECUTABLES.get(pkg_manager) in tools
    if pkg_manager == "unknown":
        installed = [name for name, executable in _PACKAGE_EXECUTABLES.items()
                     if executable in tools]
        if len(installed) == 1:
            pkg_manager, pkg_verified = installed[0], True

    if is_systemd_running is None:
        is_systemd_running = os.path.isdir("/run/systemd/system")
    # Prefer what is *actually running* over what merely happens to be
    # installed (a runit system can have systemctl installed for chroots).
    if is_systemd_running:
        service_manager = "systemd"
    elif "sv" in tools:
        service_manager = "runit"
    elif "rc-service" in tools:
        service_manager = "openrc"
    elif "systemctl" in tools:
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
        version_id=release.get("VERSION_ID", ""),
        available_tools=tools,
        package_manager_verified=pkg_verified,
        service_manager_verified=bool(is_systemd_running),
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
    """Result of an offline query.

    ``commands`` are ready to confirm. ``interaction`` names a chooser
    (wifi, printer, scanner) whose options come from a local scan, not
    from the model.
    """

    text: str
    commands: List[Command] = field(default_factory=list)
    interaction: str = ""


from .i18n import OFFLINE_TEXTS as _TEXTS, OFFLINE_SERVICE_ACTIONS as _SERVICE_ACTIONS, offline_text as _t

# Order matters: check the more specific verbs first so "restart" is not
# mistaken for "start". Word boundaries keep "start" out of "restart".
_ACTION_WORDS = [
    ("disable", ("disable", "desativar", "desactivar", "désactiver", "desactiver", "deaktivieren")),
    ("restart", ("restart", "reiniciar", "redémarrer", "redemarrer", "neustarten")),
    ("stop", ("stop", "parar", "detener", "arrêter", "arreter", "stoppen")),
    ("enable", ("enable", "ativar", "activar", "activer", "aktivieren")),
    ("start", ("start", "iniciar", "démarrer", "demarrer", "starten")),
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
    "install": ["install", "instalar", "instala", "installer", "installieren"],
    "remove": ["remove", "uninstall", "remover", "desinstalar", "désinstaller", "desinstaller",
               "deinstallieren", "entfernen"],
    "search": ["search package", "find package", "procurar pacote",
               "pesquisar pacote", "search for a package"],
    "services": ["service", "services", "serviço", "servico", "servicio", "dienst", "systemd", "runit", "openrc",
                 "ativar serviço", "ativar servico", "enable service",
                 "start service", "restart service"],
    "timezone": ["timezone", "time zone", "fuso", "timedatectl", "fuso horário",
                 "fuso horario"],
    "hostname": ["hostname", "nome da máquina", "nome da maquina",
                 "nome do computador"],
    "disk": ["disk", "disco", "df -h", "espaço em disco", "espaco em disco",
             "storage"],
    "memory": ["memory", "memória", "memoria", " ram ", "free -h"],
    "network": ["network", "rede", "dns", "ip addr", "ping", "internet"],
    "firewall": ["firewall", "ufw", "nftables", "iptables", "firewalld",
                 "firewall-cmd"],
    "shell": ["shell", "chsh", "zsh", "default shell"],
    "alias": ["alias", "aliases"],
    "clean": ["clean", "limpar", "cache", "temporários", "temporarios",
              "cleanup", "libertar espaço", "free space"],
    "autostart": ["autostart", "arranque automático", "arranque automatico",
                  "startup", "iniciar com o sistema"],
    "distro": ["distro", "distribution", "distribuição", "versão", "versao",
               "kernel", "what linux", "que linux", "que distro"],
    # Knowledge-base intents: "how does THIS distro do X"
    "config": ["configuration file", "configuration files", "config file", "config files", "where is the config",
               "config location", "where is it configured", "onde fica a config",
               "ficheiro de configura", "ficheiros de configura",
               "arquivo de configura", "fichier de config",
               "konfigurationsdatei", "onde fica configurado",
               "onde ficam os ficheiros"],
    "logs": ["logs", "log files", "where are the logs", "journal", "journalctl",
             "syslog", "registos", "registro de", "journaux", "protokolle"],
    "repos": ["repository", "repositories", "repositório", "repositorio",
              "sources.list", "mirrorlist", " repos ", "repositorios"],
    "docs": [" wiki ", "documentation", "documentação", "documentacao",
             "handbook", "official docs", "guia oficial", " manuais ",
             " manual ", "dokumentation", " where do i find docs"],
    "help": ["help", "ajuda", "what can you do", "o que consegues",
             "o que podes", "comandos disponíveis"],
}

_WIFI_RE = re.compile(
    r"\b(?:wi-?fi|wireless|wlan)\b|redes?\s+sem\s+fios",
    re.IGNORECASE,
)
_PRINTER_RE = re.compile(
    r"\b(?:printers?|impressoras?|imprimantes?)\b",
    re.IGNORECASE,
)
_SCANNER_RE = re.compile(
    r"\b(?:scanners?|digitalizadores?)\b",
    re.IGNORECASE,
)
_INSTALL_RE = re.compile(
    r"(?<![\w-])(?:install|instalar|instala|installer|installieren)\s+([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(
    r"(?<![\w-])(?:remove|uninstall|remover|desinstalar|désinstaller|desinstaller|deinstallieren|entfernen)"
    r"\s+([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(
    r"\b(?:search|procurar|pesquisar)\s+(?:for\s+|por\s+)?([A-Za-z0-9][A-Za-z0-9@._+-]{0,63})",
    re.IGNORECASE,
)
_SVC_RE = re.compile(
    r"(?<![\w-])(?:" + '|'.join(re.escape(word) for _, words in _ACTION_WORDS for word in words) + r")"
    r"\s+(?:(?:(?:o|el|le|den)\s+)?(?:service|serviço|servico|servicio|dienst)\s+)?"
    r"([A-Za-z0-9][A-Za-z0-9@._:-]{0,63})",
    re.IGNORECASE,
)
_TZ_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_+-]*/[A-Za-z0-9_+/-]+)\b")
_HOST_RE = re.compile(
    r"(?:hostname|nome da máquina|nome da maquina|nome do computador)"
    r"\s*(?:to|para|como|:|=)?\s+([A-Za-z0-9][A-Za-z0-9-]{0,62})",
    re.IGNORECASE,
)
# Free-text query after a docs/wiki intent ("documentation about ufw")
_DOCS_QUERY_RE = re.compile(
    r"(?:documentation|documentação|documentacao|wiki|handbook|manual|guia|"
    r"dokumentation|docs)\s+(?:about|on|for|sobre|para|de|do|da|zu|:)?\s*(.+)",
    re.IGNORECASE,
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+:-]{0,62}$")
_TZ_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+$")
_HOST_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")

_STOPWORDS = {
    "a", "an", "the", "um", "uma", "o", "os", "as", "package", "pacote",
    "service", "serviço", "servico", "my", "meu", "minha",
    "on", "for", "with", "to", "para", "com", "no", "na", "em", "then",
    "depois", "if", "se", "please", "por", "favor",
    "and", "e", "y", "et", "und",
}


def _match_intent(low: str) -> Tuple[Optional[str], int]:
    # Destructive/install verbs need an unambiguous, complete verb match.
    for intent, pattern in (("remove", _REMOVE_RE), ("install", _INSTALL_RE)):
        if pattern.search(low):
            return intent, 1
    # Device setup is more specific than a generic network question.
    for intent, pattern in (("wifi", _WIFI_RE), ("printer", _PRINTER_RE), ("scanner", _SCANNER_RE)):
        if pattern.search(low):
            return intent, 1
    if re.search(r"\b(?:versão|versao|version)\s+(?:do|da|of|de)\s+(?:python|java|node|ruby)\b", low):
        return None, 0
    best: Optional[str] = None
    best_score = 0
    for intent, words in _KEYWORDS.items():
        score = 0
        for word in words:
            keyword = word.strip()
            if keyword in {"atualiz", "actualiz"} or keyword.endswith(' de configura') or keyword == 'fichier de config':
                matched = re.search(r"\b" + re.escape(keyword) + r"\w*", low)
            else:
                matched = re.search(r"(?<![\w-])" + re.escape(keyword) + r"(?![\w-])", low)
            if matched:
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


_NEGATION_RE = re.compile(
    r"\b(?:nao|não|nunca|jamais|not|never|without|sem|kein|keine|nicht|pas|sin|no)\b|\bdon['’]t\b",
    re.IGNORECASE,
)
_MUTATION_RE = re.compile(
    r"(?<![\w-])(?:install|instalar|instala|installer|installieren|remove|uninstall|remover|desinstalar|désinstaller|desinstaller|deinstallieren|entfernen|update|upgrade|atualiz\w*|actualiz\w*|clean|limpar|enable|disable|start|stop|restart|ativar|desativar|iniciar|parar|reiniciar|hostname|timezone|fuso)(?![\w-])",
    re.IGNORECASE,
)
_PACKAGE_TAIL_RE = re.compile(
    r"(?<![\w-])(?:install|instalar|instala|installer|installieren|remove|uninstall|remover|desinstalar|désinstaller|desinstaller|deinstallieren|entfernen)\s+(.+)$",
    re.IGNORECASE,
)
_NEXT_WORDS = {"next", "continue", "continuar", "continua", "seguinte", "proximo", "proximo passo", "e depois", "what next", "then"}

_EXECUTABLE_INTENTS = frozenset({
    "update", "install", "remove", "services", "timezone", "hostname", "clean",
    "wifi", "printer", "scanner",
})
_PROBLEM_RE = re.compile(
    r"\b(?:diagnostic\w*|diagnostico|diagnóstico|failed|fails|broken|problema|erro|falha|"
    r"nao funciona|não funciona|not working|nao arranca|não arranca|sem rede|sem internet)\b",
    re.IGNORECASE,
)


def _is_refusal(text: str) -> bool:
    """True when the sentence refuses an action.

    ``sem`` is a negation (``sem instalar``). ``rede sem fios`` is only the
    Portuguese name of Wi-Fi, so that phrase is removed before the check.
    """
    cleaned = _WIFI_RE.sub(" ", text or "")
    return _NEGATION_RE.search(cleaned) is not None


def _packages(text: str) -> List[str]:
    """Parse the entire argument list; never silently discard another request."""
    match = _PACKAGE_TAIL_RE.search(text.strip())
    if not match:
        return []
    tail = re.sub(r"\s+(?:please|por favor)\s*[.!?]*$", "", match.group(1), flags=re.I)
    tail = tail.rstrip("!?")
    tail = re.sub(r"^(?:packages?|pacotes?)\s+", "", tail, flags=re.I)
    if re.search(r"\b(?:and|e|y|et|und|then|depois)\s+(?:install|instalar|remove|remover|update|upgrade|atualizar|clean|limpar|restart|reiniciar|enable|ativar)\b", tail, re.I):
        return []
    tokens = re.split(r"\s*(?:,|\s+(?:and|e|y|et|und)\s+)\s*|\s+", tail)
    if not tokens or len(tokens) > 20:
        return []
    # A leading article is not a package name. A stopword later in the
    # sentence still rejects the whole request, so "install nano on Ubuntu"
    # is not turned into an install of nano.
    while tokens and tokens[0].lower() in _STOPWORDS:
        tokens.pop(0)
    if not tokens:
        return []
    packages = []
    for token in tokens:
        package = _valid(token)
        # apt interprets a trailing +/- as a different requested action.
        if not package or package.endswith(("+", "-")):
            return []
        if package not in packages:
            packages.append(package)
    return packages


class OfflineAssistant:
    """Local, API-free assistant for the running distribution."""

    def __init__(self, system_utils=None, config=None,
                 os_release: Optional[Dict[str, str]] = None,
                 which=shutil.which,
                 is_systemd_running: Optional[bool] = None):
        self.system_utils = system_utils
        self.config = config
        # Sondas injectáveis (mesmo contrato de detect_distro): sem isto, um
        # host com systemd a correr detetava uma distro "void" como systemd
        # e os testes dependiam do ambiente onde correm.
        self._which = which
        self._distro = detect_distro(
            os_release, which=which,
            is_systemd_running=is_systemd_running,
        )
        # Base de conhecimento por distribuição (wikis/manuais oficiais,
        # locais de configuração, logs, firewall). Uma distro conhecida mas
        # sem perfil próprio cai no perfil genérico - as respostas continuam
        # a ter factos e apontadores válidos.
        self._kb = (knowledge_for(self._distro.distro_id, self._distro.id_like)
                    or knowledge_for("unknown"))
        self._diagnostic = None

    @property
    def distro(self) -> DistroInfo:
        return self._distro

    # -- public API --------------------------------------------------------

    def reset_conversation(self):
        """Drop observations and diagnostic continuation when switching sessions."""
        self._diagnostic = None

    def diagnostic_state(self) -> Optional[Dict[str, Any]]:
        """Return a minimal independent snapshot, never command output or prose."""
        return dict(self._diagnostic) if self._diagnostic is not None else None

    def restore_diagnostic(self, state) -> bool:
        """Restore validated continuation metadata without executing any probe.

        Session files are untrusted input. Accept only an exact data-only shape,
        a procedure applicable to this machine and an in-range integer step.
        Invalid state clears any previous continuation rather than retaining it.
        """
        self.reset_conversation()
        if type(state) is not dict or set(state) != {"id", "step"}:
            return False
        procedure_id = state["id"]
        index = state["step"]
        if type(procedure_id) is not str or type(index) is not int:
            return False
        procedure = PROCEDURE_BY_ID.get(procedure_id)
        if procedure is None or not procedure.applies_to(self._distro):
            return False
        if not 0 <= index < len(procedure.steps):
            return False
        self._diagnostic = {"id": procedure_id, "step": index}
        return True

    def handle(self, message: str, lang: str = "en") -> Reply:
        """Answer `message` using only local knowledge."""
        if lang not in _TEXTS:
            lang = "en"
        text = (message or "").strip()
        low = " " + text.lower() + " "
        distro = self._distro

        if not text:
            return Reply(self._help(distro, lang))

        mutating = bool(_MUTATION_RE.search(text) or _SVC_RE.search(text))
        if mutating and _NEGATION_RE.search(text):
            return Reply(_t(lang, "negated_action"))
        if _INSTALL_RE.search(text) and _REMOVE_RE.search(text):
            return Reply(_t(lang, "ambiguous_action"))
        if mutating and re.search(
                r"\b(?:if i|se eu|what happens|o que acontece|whether|talvez|maybe)\b", low):
            return Reply(_t(lang, "ambiguous_action"))
        if mutating and re.search(r"\b(?:erro|error|failed|falha|problema|problem|unable|cannot)\b", low):
            matches = search_procedures(text, distro, limit=1)
            return Reply(render_procedure(matches[0], lang, distro) if matches else
                         _t(lang, "ambiguous_action"))

        normalized = normalize(text).strip(" .?!")
        if normalized in {"cancel", "cancelar", "parar diagnostico", "stop diagnosis"}:
            self.reset_conversation()
            return Reply(_t(lang, "diagnostic_cancelled"))
        if self._diagnostic and normalized in _NEXT_WORDS:
            return self._continue_diagnostic(lang)
        if self._diagnostic and self._looks_like_observation(text):
            return self._continue_diagnostic(lang, text)

        findings = analyze(text, self.distro, lang)
        if findings:
            return Reply(render_findings(findings, lang) + ("\n\nUsa «guia <id>» para continuar ou o menu Relatório de diagnóstico para exportar." if lang == "pt" else
                         "\n\nUse 'guide <id>' to continue or the Diagnostic report menu to export."))
        if re.match(r"^(?:guia(?: local)?|local guide|guide|pesquisar conhecimento|procurar conhecimento|search knowledge)\b", normalized):
            return self._local_guides(text, lang)

        intent, score = _match_intent(low)
        if not intent or score == 0:
            matches = search_procedures(text, distro, limit=1)
            if matches:
                return Reply(render_procedure(matches[0], lang, distro))
            return Reply(self._help(distro, lang))

        if _PROBLEM_RE.search(low) and intent in {"services", "network", "disk", "memory", "repos", "logs"}:
            matches = search_procedures(text, distro, limit=1)
            if matches:
                return self._start_diagnostic(matches[0].id, lang)
        # A problem or a refusal names the device but must not connect,
        # add a queue, or install its support package.
        if intent in {"wifi", "printer", "scanner"} and (
                _PROBLEM_RE.search(low) or _is_refusal(text)):
            if _PROBLEM_RE.search(low):
                matches = search_procedures(text, distro, limit=1)
                if matches:
                    return self._start_diagnostic(matches[0].id, lang)
                return Reply(_t(lang, "ambiguous_action"))
            return Reply(_t(lang, "negated_action"))

        self.reset_conversation()
        return self._dispatch(intent, text, lang)

    def propose(self, message: str, lang: str = "en") -> Reply:
        """Offer a confirmed action for ``message`` without changing diagnostics.

        Used while a model answer is on screen. The offer is built from the
        user sentence and the local catalog. It is empty when the sentence
        is a question about a failure, a negation, or has nothing to run.
        """
        if lang not in _TEXTS:
            lang = "en"
        text = (message or "").strip()
        if not text:
            return Reply("")
        low = " " + text.lower() + " "
        mutating = bool(
            _MUTATION_RE.search(text) or _SVC_RE.search(text)
            or _WIFI_RE.search(text) or _PRINTER_RE.search(text) or _SCANNER_RE.search(text)
        )
        if mutating and _is_refusal(text):
            return Reply("")
        if _INSTALL_RE.search(text) and _REMOVE_RE.search(text):
            return Reply("")
        if mutating and re.search(
                r"\b(?:if i|se eu|what happens|o que acontece|whether|talvez|maybe)\b", low):
            return Reply("")
        if _PROBLEM_RE.search(low):
            return Reply("")
        intent, score = _match_intent(low)
        if not intent or score == 0 or intent not in _EXECUTABLE_INTENTS:
            return Reply("")
        reply = self._dispatch(intent, text, lang)
        if not reply.commands and not reply.interaction:
            return Reply("")
        return reply

    def _dispatch(self, intent: str, text: str, lang: str) -> Reply:
        distro = self._distro
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
            "network": lambda: self._start_diagnostic("network-interface", lang),
            "firewall": lambda: Reply(self._firewall_text(distro, lang)),
            "shell": lambda: Reply(_t(lang, "shell")),
            "alias": lambda: Reply(_t(lang, "alias")),
            "clean": lambda: self._clean_reply(distro, lang),
            "autostart": lambda: Reply(_t(lang, "autostart")),
            "wifi": lambda: self._wifi_reply(distro, lang),
            "printer": lambda: self._printer_reply(distro, lang),
            "scanner": lambda: self._scanner_reply(distro, lang),
            # Knowledge-base intents
            "config": lambda: Reply(self._config_reply(distro, lang)),
            "logs": lambda: Reply(self._logs_reply(distro, lang)),
            "repos": lambda: Reply(self._repos_reply(distro, lang)),
            "docs": lambda: Reply(self._docs_reply(distro, lang, text)),
        }
        return dispatch[intent]()

    def _has_tool(self, name: str) -> bool:
        """Use the injected ``which`` so tests do not see the host's tools."""
        return self._which(name) is not None

    def _install_named(self, package: str, description: str) -> Optional[Command]:
        template = self._distro.pkg.get("install")
        if not template or not _valid(package):
            return None
        argv = [package if part == "{pkg}" else part for part in template]
        return Command(argv=argv, privileged=True, description=description)

    def _wifi_reply(self, distro: DistroInfo, lang: str) -> Reply:
        if self._has_tool("nmcli"):
            return Reply(_t(lang, "wifi_offer"), interaction="wifi")
        package = support_package("wifi", distro.pkg_manager)
        command = self._install_named(package, f"Install {package}") if package else None
        return Reply(
            _t(lang, "wifi_unavailable", pkg=package or "NetworkManager"),
            [command] if command else [],
        )

    def _printer_reply(self, distro: DistroInfo, lang: str) -> Reply:
        if self._has_tool("lpinfo"):
            return Reply(_t(lang, "printer_offer"), interaction="printer")
        package = support_package("printer", distro.pkg_manager)
        command = self._install_named(package, f"Install {package}") if package else None
        return Reply(
            _t(lang, "printer_unavailable", pkg=package or "cups"),
            [command] if command else [],
        )

    def _scanner_reply(self, distro: DistroInfo, lang: str) -> Reply:
        package = support_package("scanner", distro.pkg_manager)
        command = self._install_named(package, f"Install {package}") if package else None
        if self._has_tool("scanimage"):
            return Reply(
                _t(lang, "scanner_offer"),
                [command] if command else [],
                interaction="scanner",
            )
        return Reply(
            _t(lang, "scanner_unavailable", pkg=package or "sane-airscan"),
            [command] if command else [],
        )

    def _local_guides(self, text: str, lang: str) -> Reply:
        search_only = bool(re.match(r"^(?:pesquisar conhecimento|procurar conhecimento|search knowledge)\b", text, re.I))
        query = re.sub(r"^(?:guia(?:\s+local)?|local guide|guide|pesquisar conhecimento|procurar conhecimento|search knowledge)\s*", "", text, flags=re.I).strip()
        exact = PROCEDURE_BY_ID.get(query.lower())
        if exact and exact.applies_to(self._distro):
            if search_only:
                return Reply(render_procedure(exact, lang, self._distro))
            return self._start_diagnostic(exact.id, lang)
        matches = search_procedures(query, self._distro)
        if not query:
            matches = tuple(item for item in PROCEDURES if item.applies_to(self._distro))
        if not matches:
            return Reply(_t(lang, "knowledge_not_found"))
        if len(matches) == 1:
            if search_only:
                return Reply(render_procedure(matches[0], lang, self._distro))
            return self._start_diagnostic(matches[0].id, lang)
        lines = [f"- {item.id}: {localized(item.title, lang)} — {localized(item.summary, lang)}" for item in matches]
        return Reply(_t(lang, "knowledge_results", results="\n".join(lines)))

    @staticmethod
    def _looks_like_observation(text: str) -> bool:
        return "\n" in text or bool(re.search(r"\b(?:UP|DOWN|UNKNOWN|default via|Mem:|Swap:|Filesystem|Sist\.\s*Fich|failed|not-found|active \(running\))|[0-9]{1,3}%", text))

    def _start_diagnostic(self, procedure_id: str, lang: str) -> Reply:
        self._diagnostic = {"id": procedure_id, "step": 0}
        return self._diagnostic_step(lang)

    def _continue_diagnostic(self, lang: str, observation: str = "") -> Reply:
        procedure = PROCEDURE_BY_ID[self._diagnostic["id"]]
        index = self._diagnostic["step"]
        interpretation = self._interpret_probe(procedure.steps[index].probe_key,
                                               observation[:12000], lang) if observation else ""
        if index + 1 >= len(procedure.steps):
            self.reset_conversation()
            return Reply((interpretation + "\n\n" if interpretation else "") +
                         _t(lang, "diagnostic_complete") + "\n" + localized(procedure.recovery, lang))
        self._diagnostic["step"] += 1
        reply = self._diagnostic_step(lang)
        if interpretation:
            reply.text = interpretation + "\n\n" + reply.text
        return reply

    def _diagnostic_step(self, lang: str) -> Reply:
        procedure = PROCEDURE_BY_ID[self._diagnostic["id"]]
        index = self._diagnostic["step"]
        step = procedure.steps[index]
        text = _t(lang, "diagnostic_step", title=localized(procedure.title, lang),
                  number=index + 1, total=len(procedure.steps),
                  instruction=localized(step.instruction, lang))
        if step.command:
            text += "\n" + step.command
        output = self._probe(step.probe_key) if step.probe_key else None
        if output:
            text += "\n\n" + _t(lang, "diagnostic_observation", output=output)
            text += "\n" + self._interpret_probe(step.probe_key, output, lang)
        else:
            text += "\n\n" + _t(lang, "diagnostic_manual")
        text += "\n" + localized(step.interpretation, lang)
        text += "\n\n" + _t(lang, "diagnostic_next")
        text += "\n" + _t(lang, "diagnostic_sources", date=procedure.reviewed_at,
                               sources=" ".join(procedure.sources))
        return Reply(text)

    def _probe(self, key: str) -> Optional[str]:
        # This registry is code, not parsed from user messages or documents.
        commands = {"links": "ip link show", "addresses": "ip addr show",
                    "routes": "ip route", "disk": "df -h", "inodes": "df -i",
                    "memory": "free -h"}
        command = commands.get(key)
        if command and command.split()[0] not in self._distro.available_tools:
            return None
        return self._run(command) if command else None

    @staticmethod
    def _interpret_probe(key: str, output: str, lang: str) -> str:
        findings = analyze(output, lang=lang, probe_key=key)
        if findings:
            return render_findings(findings, lang)
        if not output:
            return ""
        if key == "links" and re.search(r"\bDOWN\b", output):
            return _t(lang, "observed_link_down")
        if key == "routes" and not re.search(r"^default\s", output, re.M):
            return _t(lang, "observed_no_default")
        if key in {"disk", "inodes"}:
            percentages = [int(value) for value in re.findall(r"\b(\d{1,3})%", output)]
            if percentages and max(percentages) >= 90:
                return _t(lang, "observed_high_disk" if key == "disk" else "observed_high_inodes")
        if key == "addresses" and "169.254." in output:
            return _t(lang, "observed_link_local")
        return _t(lang, "observed_inconclusive")

    @staticmethod
    def run_command(command: Command, timeout: int = 120):
        """Run a confirmed catalog command. Privileged argv goes through pkexec."""
        if command.privileged:
            return OfflineAssistant.run_privileged(command, timeout=timeout)
        from .device_actions import run_argv
        return run_argv(list(command.argv), timeout=timeout)

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
            ok, output = self.system_utils.execute_command(command, timeout=8)
        except Exception:
            return None
        return str(output).strip()[:12000] if ok else None

    def _help(self, distro: DistroInfo, lang: str) -> str:
        return _t(lang, "help", pretty=distro.pretty_name,
                  pkg=distro.pkg_manager, svc=distro.service_manager) + "\n" + _t(lang, "knowledge_help")

    def _distro_reply(self, distro: DistroInfo, lang: str) -> str:
        like = f", like: {'/'.join(distro.id_like)}" if distro.id_like else ""
        kernel = distro.kernel or self._run("uname -r") or "unknown"
        text = _t(lang, "distro", pretty=distro.pretty_name,
                  distro_id=distro.distro_id, like=like,
                  pkg=distro.pkg_manager, svc=distro.service_manager,
                  kernel=kernel)
        text += "\n" + _t(lang, "detected_components", version=distro.version_id or "?",
                            tools=", ".join(distro.available_tools) or "?",
                            pkg_verified=str(distro.package_manager_verified),
                            svc_verified=str(distro.service_manager_verified))
        # Base de conhecimento: o que torna ESTA distribuição diferente
        kb = self._kb
        if kb is not None and kb.distinct:
            bullets = "\n".join("  - " + item for item in knowledge_content(kb, "distinct", lang))
            text += _t(lang, "distro_notes", pretty=distro.pretty_name,
                       bullets=bullets)
        text += self._kb_reference(lang)
        return text

    # -- knowledge base (wikis/manuais oficiais por distribuição) ----------

    def _kb_reference(self, lang: str) -> str:
        """Rodapé "Reference" com a documentação oficial da distro."""
        kb = self._kb
        if kb is None or not kb.wiki_url:
            return ""
        return _t(lang, "reference", wiki_name=kb.wiki_name, wiki_url=kb.wiki_url)

    def _config_reply(self, distro: DistroInfo, lang: str) -> str:
        """Onde ficam os ficheiros de configuração NESTA distribuição."""
        kb = self._kb
        if kb is None:
            return self._help(distro, lang)
        rows = [
            (_t(lang, "Repositories"), knowledge_content(kb, "repositories", lang)),
            (_t(lang, "Network"), knowledge_content(kb, "network", lang)),
            (_t(lang, "Logs"), knowledge_content(kb, "logs", lang)),
            (_t(lang, "Hostname"), knowledge_content(kb, "hostname", lang)),
            (_t(lang, "Locale"), knowledge_content(kb, "locale", lang)),
        ]
        if kb.services_note:
            rows.append((_t(lang, "Services"), knowledge_content(kb, "services_note", lang)))
        body = "\n".join(f"  {label}: {content}" for label, content in rows)
        return _t(lang, "config_files", pretty=distro.pretty_name,
                  body=body) + self._kb_reference(lang)

    def _logs_reply(self, distro: DistroInfo, lang: str) -> str:
        kb = self._kb
        if kb is None or not kb.logs:
            return self._help(distro, lang)
        if distro.service_manager == "systemd":
            cmds = "  journalctl -xe\n  journalctl -u <service>"
        else:
            cmds = "  dmesg | tail -50"
        return (_t(lang, "logs", pretty=distro.pretty_name,
                   logs=knowledge_content(kb, "logs", lang), cmds=cmds) + self._kb_reference(lang))

    def _repos_reply(self, distro: DistroInfo, lang: str) -> str:
        kb = self._kb
        if kb is None or not kb.repositories:
            return self._help(distro, lang)
        return (_t(lang, "repos", pretty=distro.pretty_name,
                   repos=knowledge_content(kb, "repositories", lang)) + self._kb_reference(lang))

    def _docs_reply(self, distro: DistroInfo, lang: str, text: str) -> str:
        """Documentação oficial da distribuição, com pesquisa por query."""
        kb = self._kb
        if kb is None:
            return self._help(distro, lang)
        lines = []
        if kb.wiki_url:
            lines.append(f"  {kb.wiki_name}: {kb.wiki_url}")
        for url in kb.docs_urls:
            lines.append(f"  - {url}")
        if not lines:
            lines.append("  - " + kb.wiki_name)
        result = _t(lang, "docs", pretty=distro.pretty_name,
                    body="\n".join(lines))
        # Query livre: "documentation about ufw" -> link de pesquisa na wiki
        match = _DOCS_QUERY_RE.search(text)
        tokens = []
        if match:
            for raw in re.split(r"\s+", match.group(1).strip()):
                token = _valid(raw)
                if token:
                    tokens.append(token)
            tokens = tokens[:6]
        if tokens and kb.wiki_search_url:
            from urllib.parse import quote_plus
            query = " ".join(tokens)
            url = kb.wiki_search_url.replace("{query}", quote_plus(query))
            result += _t(lang, "docs_search", wiki_name=kb.wiki_name,
                         query=query, url=url)
        matches = search_procedures(text, distro, limit=1)
        if matches:
            result = render_procedure(matches[0], lang, distro) + "\n\n" + result
        else:
            result += "\n\n" + _t(lang, "knowledge_help")
        return result

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
        packages = _packages(text)
        if packages:
            argv = [value for part in template for value in
                    (packages if part == "{pkg}" else [part])]
            package = ", ".join(packages)
            cmd = shlex.join(argv)
            return Reply(
                _t(lang, "install_named", pkg=package, cmd=cmd),
                [Command(argv=argv, privileged=True,
                         description=f"Install {package}")],
            )
        shown = shlex.join(template)
        return Reply(_t(lang, "install_generic", pretty=distro.pretty_name,
                          cmd=shown, example="htop") + "\n" + _t(lang, "package_arguments"))

    def _remove_reply(self, distro: DistroInfo, lang: str, text: str) -> Reply:
        template = distro.pkg.get("remove")
        if not template:
            return self._no_pkg(lang)
        packages = _packages(text)
        if packages:
            argv = [value for part in template for value in
                    (packages if part == "{pkg}" else [part])]
            package = ", ".join(packages)
            return Reply(
                _t(lang, "remove_named", pkg=package, cmd=shlex.join(argv)),
                [Command(argv=argv, privileged=True,
                         description=f"Remove {package}")],
            )
        return Reply(_t(lang, "remove_generic", pretty=distro.pretty_name,
                          cmd=shlex.join(template)) + "\n" + _t(lang, "package_arguments"))

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
            remainder = text[match.end():].strip().rstrip(".!?")
            if remainder and remainder.lower() not in {"please", "por favor"}:
                return Reply(_t(lang, "ambiguous_action"))
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
        kb = self._kb
        if kb is not None and kb.firewall_tool:
            status_cmds = "\n".join(
                "  " + shlex.join(list(cmd)) for cmd in kb.firewall_status
            )
            text = _t(lang, "firewall_kb", pretty=distro.pretty_name,
                      tool=kb.firewall_tool, status_cmds=status_cmds,
                      allow_cmd=kb.firewall_allow or "n/a")
            return text + self._kb_reference(lang)
        # Fallback (sem KB): conselhos genéricos por gestor de serviços
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
