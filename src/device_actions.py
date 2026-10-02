"""Closed device actions: Wi-Fi, printers and scanners.

The model never supplies these commands. Discovery output is parsed here,
identifiers are checked, and each change is an argv list. Secrets passed to
NetworkManager are removed from any text that goes back to the chat.
"""

import hashlib
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    active: bool


@dataclass(frozen=True)
class PrinterDevice:
    uri: str
    driverless: bool


@dataclass(frozen=True)
class ScannerDevice:
    device: str
    description: str


_DRIVERLESS_SCHEMES = {"ipp", "ipps", "dnssd", "ippusb"}
_URI_BODY = r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+"
_PRINTER_URI = re.compile(
    r"^(?:ipp|ipps|dnssd|ippusb|usb|socket|lpd):" + _URI_BODY + r"$"
)
_SCAN_LINE = re.compile(r"device `([^`\r\n]+)' is a (.+)$")
_QUEUE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

# Package names are fixed per family. A missing tool can be offered through
# the same confirmed install path as any other package; names are not guessed
# from the user's sentence.
SUPPORT_PACKAGES = {
    "wifi": {
        "apt": "network-manager",
        "xbps": "NetworkManager",
        "dnf": "NetworkManager",
        "pacman": "networkmanager",
        "zypper": "NetworkManager",
        "apk": "networkmanager",
    },
    "printer": {
        "apt": "cups",
        "xbps": "cups",
        "dnf": "cups",
        "pacman": "cups",
        "zypper": "cups",
        "apk": "cups",
    },
    "scanner": {
        "apt": "sane-airscan",
        "xbps": "sane-airscan",
        "dnf": "sane-airscan",
        "pacman": "sane-airscan",
        "zypper": "sane-airscan",
        "apk": "sane-airscan",
    },
}

_YES = {"y", "yes", "s", "sim"}


def support_package(kind: str, pkg_manager: str) -> Optional[str]:
    """Return the distro package that provides this device tool, if known."""
    return SUPPORT_PACKAGES.get(kind, {}).get(pkg_manager)


def valid_ssid(ssid: str) -> bool:
    """Accept one Wi-Fi name and reject control characters or an empty value."""
    if not isinstance(ssid, str):
        return False
    if not ssid or len(ssid) > 32:
        return False
    return all(ord(char) >= 32 and char not in "\r\n" for char in ssid)


def split_nmcli_fields(line: str) -> List[str]:
    """Split one ``nmcli -t`` line. Colons inside a field are escaped as \\:."""
    fields: List[str] = []
    current: List[str] = []
    escaped = False
    for char in line.rstrip("\r\n"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def parse_wifi_list(text: str) -> List[WifiNetwork]:
    """Parse ``nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY device wifi list``.

    Duplicate names keep the strongest signal. Empty and hidden names are
    dropped. The result is the only set of names the connect step may use.
    """
    best = {}
    order: List[str] = []
    for line in (text or "").splitlines():
        fields = split_nmcli_fields(line.strip())
        if len(fields) < 4:
            continue
        active = fields[0].strip().lower() == "yes"
        ssid = fields[1]
        if not valid_ssid(ssid):
            continue
        try:
            signal = int(fields[2].strip() or "0")
        except ValueError:
            signal = 0
        signal = max(0, min(signal, 100))
        security = fields[3].strip()
        current = best.get(ssid)
        if current is None:
            order.append(ssid)
            best[ssid] = WifiNetwork(ssid, signal, security, active)
        else:
            best[ssid] = WifiNetwork(
                ssid,
                max(current.signal, signal),
                security or current.security,
                current.active or active,
            )
    networks = [best[ssid] for ssid in order]
    networks.sort(key=lambda item: item.signal, reverse=True)
    return networks


def wifi_needs_password(network: WifiNetwork) -> bool:
    """Open and OWE networks connect without a passphrase."""
    security = (network.security or "").strip().lower()
    return bool(security) and security not in {"--", "open", "none", "owe"}


def wifi_connect_argv(ssid: str, password: Optional[str] = None) -> List[str]:
    """Build the NetworkManager connect command. The password is one argv."""
    if not valid_ssid(ssid):
        raise ValueError("Invalid Wi-Fi name")
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        if any(ord(char) < 32 for char in password):
            raise ValueError("Invalid Wi-Fi password")
        argv.extend(["password", password])
    return argv


def valid_printer_uri(uri: str) -> bool:
    """Allow the device URIs CUPS prints, and nothing that reaches a shell."""
    if not isinstance(uri, str) or len(uri) > 512:
        return False
    return _PRINTER_URI.fullmatch(uri) is not None


def printer_is_driverless(uri: str) -> bool:
    scheme = uri.split(":", 1)[0].lower()
    return scheme in _DRIVERLESS_SCHEMES


def parse_printers(text: str) -> List[PrinterDevice]:
    """Parse ``lpinfo -v``. Only validated URIs are returned."""
    found = []
    seen = set()
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        uri = parts[1].strip()
        if not valid_printer_uri(uri) or uri in seen:
            continue
        seen.add(uri)
        found.append(PrinterDevice(uri, printer_is_driverless(uri)))
    return found


def queue_name_for(uri: str) -> str:
    """Stable CUPS queue name derived from the URI, not from free text."""
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:6]
    return "printer-" + digest


def valid_queue_name(name: str) -> bool:
    return isinstance(name, str) and _QUEUE_NAME.fullmatch(name) is not None


def printer_add_argv(queue: str, uri: str) -> List[str]:
    """Add a driverless IPP queue. Other URIs are refused here."""
    if not valid_queue_name(queue) or not valid_printer_uri(uri):
        raise ValueError("Invalid printer")
    if not printer_is_driverless(uri):
        raise ValueError("This printer needs a driver")
    return ["lpadmin", "-p", queue, "-E", "-v", uri, "-m", "everywhere"]


def parse_scanners(text: str) -> List[ScannerDevice]:
    """Parse ``scanimage -L``. Device ids come only from this output."""
    found = []
    seen = set()
    for line in (text or "").splitlines():
        match = _SCAN_LINE.search(line.strip())
        if not match:
            continue
        device = match.group(1).strip()
        if not device or len(device) > 256 or device in seen:
            continue
        if any(ord(char) < 32 for char in device):
            continue
        seen.add(device)
        found.append(ScannerDevice(device, match.group(2).strip()))
    return found


def redact(text: str, secret: str) -> str:
    """Remove a passphrase from command output before it is shown or stored."""
    if not secret or not text:
        return text or ""
    return text.replace(secret, "********")


def run_argv(argv: Sequence[str], timeout: int = 20, secret: str = "") -> Tuple[bool, str]:
    """Run an argv list without a shell. ``secret`` is stripped from the output."""
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return False, f"{argv[0]} is not installed."
    except subprocess.TimeoutExpired:
        return False, "The command timed out."
    except OSError as exc:
        return False, str(exc)
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return completed.returncode == 0, redact(output, secret)


def collect_wifi() -> Tuple[List[WifiNetwork], str]:
    """Rescan, then list. A failed rescan still uses the current cache."""
    run_argv(["nmcli", "device", "wifi", "rescan"], timeout=8)
    ok, output = run_argv(
        ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
        timeout=15,
    )
    networks = parse_wifi_list(output)
    if networks:
        return networks, ""
    if not ok:
        return [], output or "nmcli failed."
    return [], ""


def collect_printers() -> Tuple[List[PrinterDevice], str]:
    ok, output = run_argv(["lpinfo", "-v"], timeout=20)
    devices = parse_printers(output)
    if devices:
        return devices, ""
    if not ok:
        return [], output or "lpinfo failed."
    return [], ""


def collect_scanners() -> Tuple[List[ScannerDevice], str]:
    ok, output = run_argv(["scanimage", "-L"], timeout=20)
    devices = parse_scanners(output)
    if devices:
        return devices, ""
    if not ok:
        return [], output or "scanimage failed."
    return [], ""


def connect_wifi(ssid: str, password: Optional[str] = None) -> Tuple[bool, str]:
    argv = wifi_connect_argv(ssid, password)
    return run_argv(argv, timeout=40, secret=password or "")


def confirmed(answer: str) -> bool:
    return (answer or "").strip().lower() in _YES


def format_command_offer(commands, privileged_note: str, plain_note: str) -> str:
    """Text shown before a yes/no prompt. Command lines contain no secrets."""
    lines = [privileged_note if any(command.privileged for command in commands) else plain_note]
    lines.extend(f"    $ {command.display()}" for command in commands)
    return "\n".join(lines)


def split_offer(reply) -> Tuple[str, list]:
    """Return a device chooser name and a real command list.

    Anything that is not one of the three choosers, or not a list of
    commands, is dropped. A stand-in object from a test double therefore
    cannot schedule a dialog.
    """
    if reply is None:
        return "", []
    interaction = getattr(reply, "interaction", "")
    commands = getattr(reply, "commands", None)
    if type(interaction) is not str or interaction not in {"wifi", "printer", "scanner"}:
        interaction = ""
    if type(commands) is not list:
        commands = []
    return interaction, commands


def choose_numbered(items: Sequence, read_line: Callable[[str], str], write: Callable[[str], None],
                    render: Callable, prompt: str = "Number (Enter cancels): ") -> Optional[int]:
    """Ask for a 1-based index. Empty input and invalid input cancel."""
    if not items:
        return None
    for index, item in enumerate(items, 1):
        write(f"{index}. {render(item)}\n")
    raw = read_line(prompt)
    if not raw or not raw.strip().isdigit():
        return None
    chosen = int(raw.strip())
    if not 1 <= chosen <= len(items):
        return None
    return chosen - 1
