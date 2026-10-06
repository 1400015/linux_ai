"""Qt system tray for the Windows track (phase 4c).

The drawer toggle lives here in full: ``QSystemTrayIcon`` delivers a real
left-click (Trigger) event on Windows, which the AppIndicator3 backend on
Linux cannot provide. The enable/disable decision reuses the shared
``app.tray_toggle_on_click`` config key and the same single toggle point
(the shell window), keeping both tracks coherent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from PySide6 import QtGui, QtWidgets
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


TOGGLE_REASONS = frozenset(("Trigger", "MiddleClick"))


def normalize_reason(activation_reason):
    """Normalize a Qt activation reason (enum, name or int) to a name."""
    if activation_reason is None:
        return ""
    text = str(activation_reason)
    if not text.isdigit():
        for separator in (".", "::"):
            if separator in text:
                return text.rsplit(separator, 1)[-1]
        return text
    return {"2": "DoubleClick", "3": "Trigger", "4": "MiddleClick"}.get(text, "")


def tray_click_toggles(config_manager, activation_reason, default=True):
    """Pure decision: does this activation reason toggle the drawer?

    ``Trigger`` is a real left click (and ``MiddleClick`` the middle button,
    matching the AppIndicator3 backend). Unknown reasons never toggle; the
    config key can still disable the behaviour entirely.
    """
    from .platform.tray_config import toggle_on_click_enabled
    if not toggle_on_click_enabled(config_manager, default):
        return False
    return normalize_reason(activation_reason) in TOGGLE_REASONS


def quit_only_after_confirmation(config_manager):
    """Placeholder decision point: quit stays explicit in the context menu."""
    return True


if QT_AVAILABLE:
    _BaseTray = QtWidgets.QSystemTrayIcon
else:
    _BaseTray = object


class QtTrayIcon(_BaseTray):
    """System tray icon with drawer toggle on left click."""

    def __init__(self, config_manager, shell_window, parent=None):
        if not QT_AVAILABLE:
            raise RuntimeError("PySide6 is required for QtTrayIcon")
        super().__init__(parent)
        self.config = config_manager
        self.shell = shell_window
        self.icon = QtGui.QIcon.fromTheme("linux-ai-assistant")
        if self.icon.isNull():
            self.icon = self.shell.windowIcon()
        self.setIcon(self.icon)
        self.setToolTip("Linux AI Assistant")
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self):
        from . import i18n
        self.menu = QtWidgets.QMenu()
        self.toggle_action = self.menu.addAction(i18n._("Hide Window"))
        self.toggle_action.triggered.connect(self.toggle_window)
        self.menu.addSeparator()
        quit_action = self.menu.addAction(i18n._("Quit"))
        quit_action.triggered.connect(self.quit)
        self.setContextMenu(self.menu)

    def _on_activated(self, reason):
        if tray_click_toggles(self.config, reason):
            self.toggle_window()

    def toggle_window(self):
        # Single toggle point: the shell keeps the state and the menu label
        # coherent wherever the toggle came from (click, menu, shortcut).
        self.shell.toggle_visibility()

    def quit(self):
        logger.info("Quit through the Qt tray menu")
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()

    def update_toggle_label(self, visible):
        from . import i18n
        if hasattr(self, "toggle_action"):
            self.toggle_action.setText(
                i18n._("Hide Window") if visible else i18n._("Show Window"))
