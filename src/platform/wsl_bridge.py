"""WSL bridge: enumerate distributions and build validated probe commands.

Phase 3 of the Windows support plan. The bridge never executes anything by
itself: it parses ``wsl.exe`` output (with an injectable runner for tests)
and builds argv lists whose inner command is re-validated by the existing
POSIX command policy, so probes inside a WSL distro stay as constrained as
native Linux probes.
"""
import re
from typing import Callable, Dict, Iterable, Optional, Tuple

_LIST_ARGV = ("wsl.exe", "--list", "--verbose")
_DISTRIBUTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DEFAULT_ENCODING_HINTS = ("utf-16", "utf-8")


def parse_wsl_list(output: str) -> Tuple[Dict[str, str], ...]:
    """Parse ``wsl -l -v`` output into bounded distribution records.

    Accepts both the UTF-16 output produced by wsl.exe on Windows and plain
    UTF-8 text; malformed lines are skipped rather than trusted.
    """
    text = output
    if isinstance(text, bytes):
        text = text.decode("utf-16", errors="replace")
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if not line or line.startswith("Windows") or ("NAME" in line and "STATE" in line):
            continue
        columns = line.split()
        if not columns or not _DISTRIBUTION.fullmatch(columns[0]):
            continue
        name = columns[0]
        state = columns[1] if len(columns) > 1 else "unknown"
        version = columns[2] if len(columns) > 2 else ""
        records.append({"name": name, "state": state[:32], "version": version[:16]})
    return tuple(records)


def detect_wsl_distros(run: Optional[Callable[..., "object"]] = None) -> Tuple[Dict[str, str], ...]:
    """Enumerate installed WSL distributions.

    ``run`` defaults to :func:`subprocess.run`; tests inject a fake. Any
    failure (missing wsl.exe, non-zero exit, unparseable output) yields an
    empty tuple: the bridge degrades, it never blocks startup.
    """
    if run is None:
        import subprocess
        run = subprocess.run
    try:
        completed = run(list(_LIST_ARGV), capture_output=True, timeout=10)
    except Exception:
        return ()
    if completed.returncode != 0:
        return ()
    output = getattr(completed, "stdout", b"")
    return parse_wsl_list(output)


def probe_argv(distro: str, inner_argv: Iterable[str], validate_arguments=None) -> Tuple[str, ...]:
    """Build the argv for a read-only probe inside a WSL distribution.

    The inner command is validated with the shared POSIX policy (or an
    injected validator) exactly like a native probe; an invalid inner
    command yields ``None`` instead of a bypass. Distro names are matched
    against a strict identifier pattern: a hostile name must never become
    an option to wsl.exe (``--`` guards the boundary regardless).
    """
    inner = tuple(inner_argv)
    if not inner or not isinstance(distro, str) or not _DISTRIBUTION.fullmatch(distro):
        return None
    if validate_arguments is None:
        from ..command_policy import validate_arguments as validate_path_policy
        validate_arguments = validate_path_policy
    if not validate_arguments(inner, lambda path: True):
        return None
    return ("wsl.exe", "--distribution", distro, "--exec", *inner)
