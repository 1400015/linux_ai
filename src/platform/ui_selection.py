"""UI track selection shared by the GTK and Qt shells (phase 4e)."""
from __future__ import annotations

from . import LINUX, WINDOWS, WSL, detect_platform

AUTO = "auto"
GTK = "gtk"
QT = "qt"
CHOICES = (AUTO, GTK, QT)


def select_ui_track(requested=AUTO, platform=None):
    """Resolve the UI track: Qt on the Windows host, GTK elsewhere.

    ``auto`` (the default) picks Qt when the host is Windows and GTK on
    Linux/WSL, where the mature track keeps its users. An explicit ``qt``
    or ``gtk`` is honoured as an override; an unknown value falls back to
    auto resolution.
    """
    if requested not in CHOICES:
        requested = AUTO
    if requested != AUTO:
        return requested
    current = platform if platform in (LINUX, WINDOWS, WSL) else detect_platform()
    if current == WINDOWS:
        return QT
    return GTK
