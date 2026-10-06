"""Platform detection shared by the UI, CLI and knowledge selection.

Phase 1 of the Windows support plan: a pure-Python, dependency-free module
that answers "where am I running" without subprocesses, network calls or
privileged reads. Later phases build the WSL bridge and UI backends on top
of these primitives.
"""
import os
import sys

LINUX = "linux"
WINDOWS = "windows"
WSL = "wsl"

PLATFORMS = (LINUX, WINDOWS, WSL)


def _read_proc_version(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return stream.read(4097)[:4096]
    except OSError:
        return ""


def wsl_distro_name(environ=None):
    """The WSL distribution name when running inside WSL, else an empty string."""
    env = os.environ if environ is None else environ
    return str(env.get("WSL_DISTRO_NAME", ""))[:128]


def detect_platform(environ=None, read_text=None, system_platform=None):
    """Return one of ``linux``, ``windows`` or ``wsl``.

    No subprocesses, network calls or privileged reads: Windows and inner
    WSL detection come from the interpreter and the session environment;
    the ``/proc/version`` fallback covers WSL sessions that clear the
    ``WSL_DISTRO_NAME`` variable.
    """
    env = os.environ if environ is None else environ
    current = sys.platform if system_platform is None else system_platform
    if str(current).startswith("win"):
        return WINDOWS
    if wsl_distro_name(env):
        return WSL
    read = _read_proc_version if read_text is None else read_text
    if "microsoft" in (read("/proc/version") or "").lower():
        return WSL
    return LINUX


def effective_platform(platform=None, **kwargs):
    """Validate an explicit platform value, falling back to detection."""
    if platform in PLATFORMS:
        return platform
    return detect_platform(**kwargs)
