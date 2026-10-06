"""Qt dialogs for the Windows track (phase 4d): settings and history.

The presentation logic lives in pure functions (testable without PySide6,
following the repository's decision-point pattern); the dialog classes stay
thin wrappers over the shared ConfigManager and HistoryStore backends.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from PySide6 import QtCore, QtWidgets
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Pure presentation logic (no Qt import needed to test these)
# ---------------------------------------------------------------------------

def provider_rows(config, providers=("openrouter", "google")):
    """Rows for the API settings table, without exposing stored keys."""
    rows = []
    for provider in providers:
        try:
            key = config.get_api_key(provider) or ""
            stored = bool(key)
            env_var = config.api_key_env_var(provider)
            overridden = config.get_api_key_env_override(provider) is not None
        except Exception:
            stored, env_var, overridden = False, "", False
        rows.append({
            "provider": provider,
            "status": "configured" if stored else "missing",
            "env_var": env_var,
            "overridden": overridden,
        })
    return rows


def history_rows(store, include_archived=False):
    """Session summaries for the history dialog."""
    sessions = store.list_sessions(include_archived=include_archived)
    rows = []
    for session in sessions:
        rows.append({
            "id": session.get("id"),
            "title": session.get("title") or "Untitled",
            "updated_at": session.get("updated_at"),
            "archived": bool(session.get("archived")),
            "active": session.get("id") == store.active_session_id,
        })
    return rows


def save_api_key(config, provider, key_text):
    """Validate and persist an API key. Returns (ok, message)."""
    if not provider or not isinstance(provider, str):
        return False, "invalid-provider"
    key_text = (key_text or "").strip()
    if not key_text:
        return False, "empty-key"
    try:
        config.set_api_key(provider, key_text)
    except Exception:
        logger.error("Failed to store the API key for %s", provider)
        return False, "store-failed"
    return True, "stored"


# ---------------------------------------------------------------------------
# Dialogs (thin Qt wrappers)
# ---------------------------------------------------------------------------

if QT_AVAILABLE:
    _BaseDialog = QtWidgets.QDialog
else:
    _BaseDialog = object


class QtSettingsDialog(_BaseDialog):
    """API settings: provider selector, key entry, save."""

    def __init__(self, config, parent=None):
        if not QT_AVAILABLE:
            raise RuntimeError("PySide6 is required for QtSettingsDialog")
        super().__init__(parent)
        from . import i18n
        self.config = config
        self.setWindowTitle(i18n._("Settings"))
        self.setMinimumWidth(420)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.provider_combo = QtWidgets.QComboBox(self)
        for row in provider_rows(config):
            label = row["provider"]
            if row["overridden"]:
                label += " ({})".format(i18n._("overridden by environment"))
            self.provider_combo.addItem(label, row["provider"])
        form.addRow(i18n._("Provider"), self.provider_combo)
        self.key_edit = QtWidgets.QLineEdit(self)
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key_edit.setPlaceholderText(i18n._("API Key"))
        form.addRow(i18n._("API Key"), self.key_edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_provider(self):
        return self.provider_combo.currentData()

    def _save(self):
        ok, _message = save_api_key(self.config, self.selected_provider(),
                                    self.key_edit.text())
        if ok:
            self.accept()


class QtHistoryDialog(_BaseDialog):
    """Conversation history: session list, open, new."""

    def __init__(self, store, parent=None, on_open=None, on_new=None):
        if not QT_AVAILABLE:
            raise RuntimeError("PySide6 is required for QtHistoryDialog")
        super().__init__(parent)
        from . import i18n
        self.store = store
        self.on_open = on_open
        self.on_new = on_new
        self.setWindowTitle(i18n._("Conversation History"))
        self.setMinimumSize(460, 320)
        layout = QtWidgets.QVBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget(self)
        layout.addWidget(self.list_widget, 1)
        row = QtWidgets.QHBoxLayout()
        new_button = QtWidgets.QPushButton(i18n._("New conversation"), self)
        new_button.clicked.connect(self._new)
        open_button = QtWidgets.QPushButton(i18n._("Open"), self)
        open_button.clicked.connect(self._open)
        row.addWidget(new_button)
        row.addWidget(open_button)
        layout.addLayout(row)
        self.reload()

    def reload(self):
        self.list_widget.clear()
        from . import i18n
        for entry in history_rows(self.store):
            label = entry["title"]
            if entry["active"]:
                label = "• " + label
            if entry["archived"]:
                label += " — " + i18n._("archived")
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, entry["id"])
            self.list_widget.addItem(item)

    def selected_session_id(self):
        item = self.list_widget.currentItem()
        return item.data(QtCore.Qt.UserRole) if item is not None else None

    def _new(self):
        session = self.store.create_session(select=True)
        session_id = session.get("id") if isinstance(session, dict) else session
        if callable(self.on_new):
            self.on_new(session_id)
        self.reload()

    def _open(self):
        session_id = self.selected_session_id()
        if session_id is None:
            return
        self.store.select_session(session_id)
        if callable(self.on_open):
            self.on_open(session_id)
        self.accept()
