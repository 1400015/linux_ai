"""Qt chat widget on the shared backends (phase 4b).

Hosts the conversation inside the Qt shell using the same OfflineAssistant
and i18n used by the GTK track. The widget is deliberately simple: a
read-only message log, an input field and a send action. Provider streaming,
sessions and dialogs arrive in later sub-phases; the offline replies already
flow through the exact same Reply contract as the GTK window.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from PySide6 import QtCore, QtWidgets
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

MAX_LOG_CHARS = 131072


def offline_reply_text(offline_assistant, message, lang="en"):
    """Pure send path: run the offline assistant and return its answer.

    Kept Qt-free so the routing contract (including failure handling) is
    testable without PySide6, exactly like the GTK track's decision points.
    """
    message = (message or "").strip()
    if not message:
        return None
    try:
        reply = offline_assistant.handle(message, lang)
        return reply.text if reply is not None else None
    except Exception as error:
        logger.error("Offline assistant failed: %s", type(error).__name__)
        return None


def append_is_bounded(log_chars, chunk):
    """Pure bound decision for the message log."""
    return log_chars + len(chunk) <= MAX_LOG_CHARS


if QT_AVAILABLE:
    _BaseWidget = QtWidgets.QWidget
else:
    _BaseWidget = object


class QtChatWidget(_BaseWidget):
    """Message log plus input line, driven by the offline assistant."""

    send_requested = QtCore.Signal(str) if QT_AVAILABLE else None

    def __init__(self, config_manager, offline_assistant, parent=None,
                 history_store=None):
        if not QT_AVAILABLE:
            raise RuntimeError("PySide6 is required for QtChatWidget")
        super().__init__(parent)
        self.config = config_manager
        self.offline = offline_assistant
        self.history_store = history_store
        self._log_chars = 0
        self._build()
        if self.send_requested is not None:
            self.send_requested.connect(self._on_send)
        self.load_session()

    def _build(self):
        from . import i18n
        layout = QtWidgets.QVBoxLayout(self)
        self.log = QtWidgets.QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4096)
        layout.addWidget(self.log, 1)
        row = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit(self)
        self.input.setPlaceholderText(i18n._("Type a message..."))
        self.input.returnPressed.connect(self._on_return)
        self.send_button = QtWidgets.QPushButton(i18n._("Send"), self)
        self.send_button.clicked.connect(self._on_send_clicked)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        layout.addLayout(row)

    def _on_return(self):
        self._on_send()

    def _on_send_clicked(self):
        self._on_send()

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        from . import i18n
        self.append_message(i18n._("User"), text)
        answer = offline_reply_text(self.offline, text) or ""
        self.append_message(i18n._("AI"), answer)
        if self.history_store is not None:
            try:
                self.history_store.append("user", text)
                self.history_store.append("assistant", answer)
            except Exception as error:
                logger.warning("History write failed: %s", type(error).__name__)

    def load_session(self, session_id=None):
        """Render the active (or given) session's stored messages."""
        if self.history_store is None:
            return
        try:
            entries = self.history_store.load_messages(session_id)
        except Exception as error:
            logger.warning("History read failed: %s", type(error).__name__)
            return
        self.log.clear()
        self._log_chars = 0
        from . import i18n
        labels = {"user": i18n._("User"), "assistant": i18n._("AI"),
                  "system": i18n._("System")}
        for entry in entries:
            label = labels.get(entry.get("role"), entry.get("role", "?"))
            self.append_message(label, entry.get("content", ""))

    def append_message(self, label, text):
        chunk = "{}: {}\n".format(label, text)
        if not append_is_bounded(self._log_chars, chunk):
            return
        self._log_chars += len(chunk)
        self.log.appendPlainText(chunk.rstrip("\n"))
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
