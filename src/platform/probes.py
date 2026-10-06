"""Platform-aware read probes for the offline assistant diagnostics.

Phase 3 integration: the offline assistant's probe registry is POSIX-only.
On Windows the equivalent evidence comes from read-only PowerShell cmdlets;
inside a WSL distribution the POSIX probes remain valid but must be wrapped
through the WSL bridge. This module maps probe keys to platform commands
and never executes anything: the caller (SystemUtils or the assistant)
remains the single execution authority.
"""
from typing import Callable, Optional, Tuple

from . import WINDOWS, WSL, effective_platform

# POSIX probe registry (mirrors offline_assistant._probe commands).
POSIX_PROBES = {
    "links": ("ip", "link", "show"),
    "addresses": ("ip", "addr", "show"),
    "routes": ("ip", "route"),
    "disk": ("df", "-h"),
    "inodes": ("df", "-i"),
    "memory": ("free", "-h"),
}

# Windows equivalents: read-only, allowlisted PowerShell cmdlets wrapped
# for ``powershell -Command`` so system_utils can validate the cmdlet tail.
WINDOWS_PROBES = {
    "links": ("powershell", "-Command", "Get-NetAdapter"),
    "addresses": ("powershell", "-Command", "Get-NetIPConfiguration"),
    "routes": ("powershell", "-Command", "Get-NetRoute"),
    "disk": ("powershell", "-Command", "Get-Volume"),
    "memory": ("powershell", "-Command", "Get-Process"),
}

PROBE_KEYS = tuple(POSIX_PROBES)


def probe_argv_for(key: str, platform: Optional[str] = None,
                   wsl_distro: Optional[str] = None,
                   validate_pwsh: Optional[Callable] = None,
                   validate_posix: Optional[Callable] = None) -> Optional[Tuple[str, ...]]:
    """Return the argv for a read probe on the given (or detected) platform.

    - Windows: the allowlisted PowerShell equivalent, validated by
      ``validate_pwsh`` (defaults to the shared policy).
    - WSL: the POSIX probe wrapped through the WSL bridge, whose inner
      command is re-validated by the shared POSIX policy.
    - Linux (or an unknown key): ``None``; the caller keeps its native path.
    """
    if key not in PROBE_KEYS:
        return None
    current = effective_platform(platform)
    if current == WINDOWS:
        argv = WINDOWS_PROBES[key]
        if validate_pwsh is None:
            from .shell_pwsh import validate_pwsh_arguments
            validate_pwsh = validate_pwsh_arguments
        return argv if validate_pwsh(argv[2:]) else None
    if current == WSL and wsl_distro:
        from .wsl_bridge import probe_argv as wsl_probe
        return wsl_probe(wsl_distro, POSIX_PROBES[key], validate_arguments=validate_posix)
    return None
