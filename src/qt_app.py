"""Minimal Qt shell for the Windows track (phase 4a).

Foundation only: this module deliberately contains no chat logic, no
dialogs and no tray behaviour. It proves the Qt event loop, the shared
ConfigManager/i18n integration and the platform-aware startup, so later
sub-phases (chat, tray, dialogs) migrate onto a working base.

PySide6 is an optional dependency: when absent, ``available()`` is False and
``run()`` reports the same missing-binding message style used for GTK.
"""
import logging
import sys

logger = logging.getLogger(__name__)

QT_IMPORT_ERROR = None

try:
    from PySide6 import QtCore, QtWidgets
    QT_AVAILABLE = True
except ImportError as error:
    QT_AVAILABLE = False
    QT_IMPORT_ERROR = str(error)


MIN_WINDOW_SIZE = (420, 260)


def available() -> bool:
    return QT_AVAILABLE


def missing_dependency_message() -> str:
    return (
        "PySide6 is not installed. Install it with:\n"
        "  pip install PySide6\n"
        "or add the `qt` extra: pip install -e .[qt]"
    )


if QT_AVAILABLE:
    _BaseShell = QtWidgets.QMainWindow
else:
    _BaseShell = object


class QtShell(_BaseShell):
    """Qt main window: status line plus the chat widget (phase 4b)."""

    def __init__(self, config_manager, platform_name):
        if not QT_AVAILABLE:
            raise RuntimeError(missing_dependency_message())
        super().__init__()
        self.config = config_manager
        self.platform_name = platform_name
        self.chat = None
        self.setWindowTitle("Linux AI Assistant")
        self.resize(*MIN_WINDOW_SIZE)
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        status = QtWidgets.QLabel(self._status_text(), central)
        status.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(status)
        self._build_chat(layout)
        self.setCentralWidget(central)
        self._build_tray()

    def open_settings_dialog(self):
        from .qt_dialogs import QT_AVAILABLE as DIALOGS_QT_AVAILABLE, QtSettingsDialog
        if not DIALOGS_QT_AVAILABLE:
            return
        QtSettingsDialog(self.config, self).exec()

    def open_history_dialog(self):
        from .qt_dialogs import QT_AVAILABLE as DIALOGS_QT_AVAILABLE, QtHistoryDialog
        if not DIALOGS_QT_AVAILABLE or getattr(self, "history_store", None) is None:
            return
        def reopen(session_id):
            if self.chat is not None and hasattr(self.chat, "load_session"):
                self.chat.load_session(session_id)
        QtHistoryDialog(self.history_store, self, on_open=reopen, on_new=reopen).exec()

    def toggle_visibility(self):
        """Single toggle point for the Qt track (tray, future shortcut)."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
        tray = getattr(self, "tray_icon", None)
        if tray is not None:
            tray.update_toggle_label(self.isVisible())

    def _build_tray(self):
        from .qt_tray import QT_AVAILABLE as TRAY_QT_AVAILABLE, QtTrayIcon
        if not TRAY_QT_AVAILABLE or not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        try:
            self.tray_icon = QtTrayIcon(self.config, self, self)
        except Exception as error:
            logger.warning("Qt tray icon unavailable: %s", type(error).__name__)
            self.tray_icon = None

    def _build_chat(self, layout):
        from .qt_chat import QT_AVAILABLE as CHAT_QT_AVAILABLE, QtChatWidget
        if not CHAT_QT_AVAILABLE:
            return
        from .offline_assistant import OfflineAssistant
        from .system_utils import SystemUtils
        try:
            system_utils = SystemUtils(self.config)
            offline = OfflineAssistant(system_utils, self.config)
        except Exception as error:
            logger.warning("Offline assistant unavailable for the Qt shell: %s",
                           type(error).__name__)
            return
        self.history_store = None
        try:
            from .history_store import HistoryStore
            self.history_store = HistoryStore()
        except Exception as error:
            logger.warning("History store unavailable for the Qt shell: %s",
                           type(error).__name__)
        self.chat = QtChatWidget(self.config, offline, self,
                                 history_store=self.history_store)
        layout.addWidget(self.chat, 1)

    def _status_text(self):
        from . import i18n
        name = {"windows": "Windows", "wsl": "WSL", "linux": "Linux"}.get(
            self.platform_name, self.platform_name)
        title = i18n._("Welcome to Linux AI Assistant!")
        return "{}\nQt shell — platform: {}".format(title, name)

    def closeEvent(self, event):
        logger.info("Qt shell closed by the user")
        super().closeEvent(event)


def run(config_manager, argv=None):
    """Start the minimal Qt shell. Returns a process exit code."""
    if not QT_AVAILABLE:
        print("\nError: {}\n\n{}".format(QT_IMPORT_ERROR, missing_dependency_message()),
              file=sys.stderr)
        return 1
    from .platform import detect_platform
    platform_name = detect_platform()
    argv = list(sys.argv[:1]) if argv is None else list(argv)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("linux-ai-assistant")
    shell = QtShell(config_manager, platform_name)
    shell.show()
    logger.info("Starting Qt event loop (platform: %s)", platform_name)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(None))
