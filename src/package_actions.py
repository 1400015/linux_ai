"""Repository-backed software actions, independent of GTK and AI providers.

The caller owns consent and conversation state. Only names and versions read
from the configured repositories reach an installer; neither model output nor
repository descriptions are executable commands. APT/XBPS queries use cached
indexes and do not refresh indexes, add repositories or download scripts.

Formats and exact-version syntax:
https://manpages.debian.org/trixie/apt/apt-cache.8.en.html
https://manpages.debian.org/trixie/apt/apt-get.8.en.html
https://manpages.debian.org/trixie/dpkg/dpkg-query.1.en.html
https://man.voidlinux.org/xbps-query.1
https://man.voidlinux.org/xbps-install.1
https://github.com/void-linux/xbps/blob/master/bin/xbps-query/search.c
"""

from dataclasses import asdict, dataclass
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .action_audit import record_command


SUPPORTED_MANAGERS = ("apt", "xbps")
MAX_CANDIDATES = 12
MAX_OUTPUT_BYTES = 1024 * 1024
SEARCH_BUDGET = 40
INSTALL_TIMEOUT = 600
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,127}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,127}\Z")


def _plain(value: str, limit: int, allow_empty: bool = True) -> bool:
    return (type(value) is str and len(value) <= limit
            and (allow_empty or bool(value))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _name(value: str) -> bool:
    # '+' is a valid package-name character (e.g. g++). Installs always pin
    # name=version, so '+' cannot become APT's suffix operation modifier.
    return type(value) is str and bool(_NAME.fullmatch(value)) and not value.endswith("-")


def _version(value: str) -> bool:
    return type(value) is str and bool(_VERSION.fullmatch(value))


def _summary(value: str) -> str:
    return " ".join("".join(char for char in value if ord(char) >= 32 and ord(char) != 127).split())[:512]


def _literal_pattern(value: str) -> str:
    """Escape POSIX ERE metacharacters, preserving spaces and ordinary text."""
    return "".join("\\" + char if char in r".[]\*^$()+?{}|" else char for char in value)


def _query(value: str) -> Optional[str]:
    if not _plain(value, 128, False):
        return None
    value = " ".join(value.strip().split())
    if (not value or value.startswith("-")
            or not all(char.isalnum() or char in " +._-()[]" for char in value)):
        return None
    return value


@dataclass(frozen=True)
class PackageCandidate:
    name: str
    version: str = ""
    source: str = ""
    summary: str = ""

    def __post_init__(self):
        if (not _name(self.name) or (self.version and not _version(self.version))
                or not _plain(self.source, 1024) or not _plain(self.summary, 512)
                or type(self.version) is not str):
            raise ValueError("Invalid package candidate metadata.")

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if (type(value) is not dict or "name" not in value
                or set(value) - {"name", "version", "source", "summary"}):
            raise ValueError("Invalid package candidate metadata.")
        return cls(**value)


def _run(argv: Sequence[str], timeout: int = 20, environment=None) -> Tuple[bool, str]:
    """Bound memory/output, disable stdin and force parseable query output."""
    env = dict(os.environ if environment is None else environment)
    env.update(LC_ALL="C", LANG="C", LANGUAGE="C", NO_COLOR="1")
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                list(argv), stdout=output, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, timeout=timeout, env=env,
            )
            output.seek(0)
            raw = output.read(MAX_OUTPUT_BYTES + 1)
        if len(raw) > MAX_OUTPUT_BYTES:
            return False, "Package command output exceeded the size limit."
        return completed.returncode == 0, raw.decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return False, "The package command is not installed."
    except subprocess.TimeoutExpired:
        return False, "The package command timed out."
    except OSError as exc:
        return False, str(exc)


def _fields(text: str) -> Dict[str, str]:
    """Only top-level scalar fields; ignore continuation lines and scripts."""
    result = {}
    for line in text.splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in result:
            # Duplicate identity fields make metadata ambiguous.
            return {}
        result[key] = value.strip()
    return result


def _xbps_identity(pkgver: str) -> Tuple[str, str]:
    if "-" not in pkgver:
        return "", ""
    name, version = pkgver.rsplit("-", 1)
    if not _name(name) or not _version(version) or not re.fullmatch(r"[0-9].*_[0-9]+", version):
        return "", ""
    return name, version


def _apt_policy(text: str, name: str) -> Tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != name + ":":
        return "", ""
    candidate = ""
    sources = []
    in_candidate = False
    for line in lines[1:]:
        if line.strip().startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
            if not _version(candidate):
                return "", ""
            continue
        # Version rows precede indented source rows; the latter end in Packages.
        match = re.fullmatch(r"\s*(?:\*\*\*\s+)?(\S+)\s+(-?\d+)\s*", line)
        if match:
            in_candidate = match.group(1) == candidate
            continue
        if in_candidate:
            match = re.fullmatch(r"\s*-?\d+\s+(.+?)\s+Packages\s*", line)
            if match and _plain(match.group(1), 1024, False):
                source = match.group(1)
                if source not in sources:
                    sources.append(source)
    source = " | ".join(sources)
    return (candidate, source) if candidate and _plain(source, 1024, False) else ("", "")


class PackageService:
    """Search and install one selected repository candidate after caller consent.

    Injected ``runner(argv, timeout=...)`` returns ``(success, output)``. It
    must preserve the same no-shell and timeout contract as the default runner.
    ``is_current`` lets the caller revoke consent while queries are running.
    """

    def __init__(self, manager: str, runner=None, which=shutil.which):
        self.manager = manager
        self.runner = runner or _run
        self.which = which

    def _call(self, argv: Sequence[str], timeout: int = 20) -> Tuple[bool, str]:
        try:
            ok, text = self.runner(list(argv), timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if type(text) is not str or len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return False, "Invalid or oversized package command output."
        record_command(argv, bool(ok))
        return bool(ok), text.strip()

    def _available(self) -> str:
        if self.manager not in SUPPORTED_MANAGERS:
            return "Conversational repository search is not supported for this package manager."
        executable = "apt-cache" if self.manager == "apt" else "xbps-query"
        if not self.which(executable):
            return f"{executable} is not installed."
        return ""

    def _lookup(self, name: str, timeout: int = 8) -> Optional[PackageCandidate]:
        if not _name(name):
            return None
        if self.manager == "apt":
            ok, policy = self._call(["apt-cache", "policy", "--", name], timeout)
            if not ok:
                return None
            version, source = _apt_policy(policy, name)
            if not version or not source:
                return None
            ok, text = self._call(["apt-cache", "show", "--", name + "=" + version], timeout)
            if not ok:
                return None
            # The same binary version can occur in multiple configured repos.
            records = [_fields(record) for record in re.split(r"\n\s*\n", text)]
            records = [record for record in records
                       if record.get("Package") == name and record.get("Version") == version]
            if not records:
                return None
            summary = records[0].get("Description", records[0].get("Description-en", ""))
        else:
            ok, text = self._call(["xbps-query", "-R", "--", name], timeout)
            if not ok:
                return None
            record = _fields(text)
            found, version = _xbps_identity(record.get("pkgver", ""))
            source = record.get("repository", "")
            if found != name or not _plain(source, 1024, False):
                return None
            summary = record.get("short_desc", "")
        return PackageCandidate(name, version, source, _summary(summary))

    def search(self, query: str) -> Tuple[List[PackageCandidate], str]:
        query = _query(query)
        if query is None:
            return [], "Use a plain package name or search text (up to 128 characters)."
        error = self._available()
        if error:
            return [], error
        argv = (["apt-cache", "search", "--", _literal_pattern(query)] if self.manager == "apt"
                else ["xbps-query", "-R", "--regex", "-s", _literal_pattern(query)])
        deadline = time.monotonic() + SEARCH_BUDGET
        ok, text = self._call(argv, timeout=20)
        if not ok:
            return [], text or "Repository search failed."
        names = []
        for line in text.splitlines():
            if self.manager == "apt":
                name = line.split(" - ", 1)[0].strip() if " - " in line else ""
            else:
                match = re.match(r"\[[*\-]\]\s+(\S+)(?:\s|$)", line)
                name, _ = _xbps_identity(match.group(1)) if match else ("", "")
            if _name(name) and name not in names:
                names.append(name)
        # Prefer an exact name over similarly named libraries and plugins.
        names.sort(key=lambda name: (name.lower() != query.lower(), name.lower()))
        candidates = []
        for name in names[:MAX_CANDIDATES]:
            remaining = deadline - time.monotonic()
            if remaining < 2:
                break
            candidate = self._lookup(name, timeout=max(1, min(8, int(remaining / 2))))
            if candidate is not None:
                candidates.append(candidate)
        if names and not candidates:
            return [], "No installable repository candidate could be verified. Check the local package indexes."
        return candidates, ""

    def _installed(self, candidate: PackageCandidate) -> bool:
        if self.manager == "apt":
            ok, text = self._call([
                "dpkg-query", "--show", "--showformat=${Package}\t${Status}\t${Version}\n",
                "--", candidate.name,
            ], timeout=10)
            expected = f"{candidate.name}\tinstall ok installed\t{candidate.version}"
            return ok and text == expected
        ok, text = self._call(["xbps-query", "--", candidate.name], timeout=10)
        record = _fields(text)
        return (ok and record.get("pkgver") == candidate.name + "-" + candidate.version
                and record.get("state") == "installed")

    def install(self, candidate: PackageCandidate,
                is_current: Optional[Callable[[], bool]] = None) -> Tuple[bool, str]:
        """Recheck identity, execute one exact version, then query installed state."""
        if not isinstance(candidate, PackageCandidate) or not candidate.version or not candidate.source:
            return False, "Select a verified repository package before installing."
        current = is_current or (lambda: True)
        if not current():
            return False, "Package installation cancelled."
        error = self._available()
        if error:
            return False, error
        found = self._lookup(candidate.name)
        if not current():
            return False, "Package installation cancelled."
        if found is None or found.version != candidate.version or found.source != candidate.source:
            return False, "The package version or repository changed. Search again before installing."
        executable = "apt-get" if self.manager == "apt" else "xbps-install"
        verify_tool = "dpkg-query" if self.manager == "apt" else "xbps-query"
        installer = self.which(executable)
        pkexec = self.which("pkexec")
        if not installer or not pkexec or not self.which(verify_tool):
            return False, "The installer, package verifier and pkexec must be installed."
        # Absolute paths prevent pkexec from resolving another executable.
        if not os.path.isabs(installer) or not os.path.isabs(pkexec):
            return False, "Package tools must resolve to absolute executable paths."
        if self._installed(candidate):
            return True, f"{candidate.name} {candidate.version} is already installed."
        if not current():
            return False, "Package installation cancelled."
        argv = ([pkexec, installer, "-y", "--no-remove", "install", "--",
                 candidate.name + "=" + candidate.version] if self.manager == "apt"
                else [pkexec, installer, "-y", "--", candidate.name + "-" + candidate.version])
        ok, output = self._call(argv, timeout=INSTALL_TIMEOUT)
        if not ok:
            return False, output or "Package installation failed."
        # Revocation cannot undo a transaction already committed by the manager;
        # still verify and report its real outcome instead of calling it cancelled.
        if not self._installed(candidate):
            detail = "The installer returned success, but the selected version is not fully installed."
            return False, (output + "\n" + detail).strip()
        return True, (output + f"\nInstalled and verified {candidate.name} {candidate.version}.").strip()
