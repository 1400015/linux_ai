"""Centralized, reusable path validation for file and command operations.

Every module that touches the filesystem should import from here rather than
reimplementing ``realpath``/``expanduser``/containment checks. The helpers are
GTK-free and side-effect free so they can be unit-tested headless.

Design rules:
- ``~`` is always expanded and every path is resolved with ``realpath`` so
  symlinks and ``..`` components cannot escape containment.
- Containment uses ``os.path.commonpath`` (not ``startswith``) so a sibling
  directory sharing a name prefix (``/etcfoo`` vs ``/etc``) is rejected.
- Sensitive system paths are always rejected, even inside an allowed dir.
"""

import os
import stat
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

PathLike = Union[str, os.PathLike]

# Paths that must never be read or written by the assistant, even when they
# live inside an allowed directory (e.g. ~/.ssh is inside $HOME).
SENSITIVE_PATHS: frozenset = frozenset({
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/root",
    "/boot",
})

_SENSITIVE_PREFIXES: tuple = (
    "/etc/sudoers.d/",
    "/root/",
    "/boot/",
)

# Directories that hold credentials or private keys and must never be edited
# through the AI file-block path, even inside $HOME.
_SENSITIVE_HOME_SUFFIXES: tuple = (
    "/.ssh",
    "/.gnupg",
    "/.config/google-chrome",
    "/.config/chromium",
    "/.mozilla",
    "/.thunderbird",
    "/.local/share/keyrings",
    "/.aws",
    "/.azure",
    "/.kube",
    "/.docker",
)


def expand_path(path: PathLike) -> str:
    """Expand ``~`` and return the absolute, ``realpath``-resolved string."""
    return os.path.realpath(os.path.expanduser(os.fspath(path)))


def is_sensitive_path(path: PathLike) -> bool:
    """True for credential stores, privilege files and browser profiles."""
    real = expand_path(path)
    if real in SENSITIVE_PATHS:
        return True
    for prefix in _SENSITIVE_PREFIXES:
        if real.startswith(prefix):
            return True
    home = os.path.realpath(os.path.expanduser("~"))
    if real == home or real.startswith(home + os.sep):
        relative = real[len(home):]
        for suffix in _SENSITIVE_HOME_SUFFIXES:
            if relative == suffix or relative.startswith(suffix + os.sep):
                return True
    return False


def is_within(path: PathLike, allowed_dir: PathLike) -> bool:
    """True if ``path`` resolves to ``allowed_dir`` or a descendant of it.

    Uses ``os.path.commonpath`` so a sibling sharing a name prefix is rejected.
    """
    real = expand_path(path)
    allowed_real = expand_path(allowed_dir)
    try:
        return os.path.commonpath([real, allowed_real]) == allowed_real
    except ValueError:
        # Different roots (e.g. separate mounts) — never contained.
        return False


def is_within_any(path: PathLike, allowed_dirs: Iterable[PathLike]) -> bool:
    """True if ``path`` is inside any of ``allowed_dirs``."""
    return any(is_within(path, allowed_dir) for allowed_dir in allowed_dirs)


def validate_path(
    path: PathLike,
    allowed_dirs: Optional[Sequence[PathLike]] = None,
    *,
    require_home: bool = True,
) -> bool:
    """Validate a path against the home directory and optional extra dirs.

    A path is valid when it:
    1. Is not a sensitive path (credentials, shadow, sudoers, browser profiles).
    2. Is inside ``$HOME`` (when ``require_home``) or one of ``allowed_dirs``.

    When ``allowed_dirs`` is provided and non-empty, the home check is skipped
    if the path is inside one of the allowed dirs (matches the historical
    behaviour of ``file_actions.is_allowed_path``).
    """
    real = expand_path(path)
    if is_sensitive_path(real):
        return False
    home = os.path.realpath(os.path.expanduser("~"))
    if real == home or real.startswith(home + os.sep):
        return True
    if allowed_dirs:
        for allowed_dir in allowed_dirs:
            if is_within(real, allowed_dir):
                return True
    return False


def resolve_safe(
    path: PathLike,
    allowed_dirs: Optional[Sequence[PathLike]] = None,
    *,
    must_exist: bool = False,
) -> Optional[Path]:
    """Return the resolved ``Path`` if valid, else ``None``.

    When ``must_exist`` is True the resolved path must exist (after symlink
    resolution). This is the preferred entry point for callers that need a
    concrete ``Path`` object.
    """
    real = expand_path(path)
    if not validate_path(real, allowed_dirs):
        return None
    resolved = Path(real)
    if must_exist and not resolved.exists():
        return None
    return resolved


def is_regular_file(path: PathLike) -> bool:
    """True if ``path`` exists, is not a symlink, and is a regular file."""
    try:
        info = os.lstat(os.fspath(path))
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode)


def safe_join(base: PathLike, *parts: str) -> Optional[Path]:
    """Join ``parts`` onto ``base`` and return the resolved path if contained.

    Returns ``None`` when the result escapes ``base`` (e.g. via ``..`` or an
    absolute part).
    """
    candidate = Path(expand_path(base)).joinpath(*parts)
    if not is_within(candidate, base):
        return None
    return candidate
