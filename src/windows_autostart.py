"""Windows autostart for the Qt track (phase 4c).

The Registry ``Run`` key is the Windows equivalent of scripts/autostart.sh:
a single value under HKCU pointing at the launcher. Registry access is
injected (``read_value``/``write_value``/``delete_value``) so the decision
logic — value construction, idempotency, comparison — is fully testable
without Windows, exactly like the polkit-free decision points elsewhere.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

AUTOSTART_VALUE_NAME = "LinuxAIAssistant"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command(powershell_exe, script_path, ui="qt"):
    """Build the launcher command stored in the Registry Run key.

    The Registry Run key starts a process without the project working
    directory, so the value launches ``run.ps1`` (which sets it) through
    PowerShell instead of calling ``python -m src.app`` directly.
    """
    if ui not in ("qt", "gtk"):
        ui = "qt"
    return '"{}" -WindowStyle Hidden -ExecutionPolicy Bypass -File "{}" -Ui {}'.format(
        powershell_exe, script_path, ui)


def autostart_should_update(current_value, desired_command):
    """Idempotency: only rewrite when the stored value differs."""
    return current_value != desired_command


def apply_autostart(enabled, desired_command, read_value, write_value, delete_value):
    """Pure orchestration over injected Registry primitives.

    Returns a short status string for the UI/log; never raises on its own
    primitives (callers wrap winreg errors).
    """
    if enabled:
        current = read_value(RUN_KEY, AUTOSTART_VALUE_NAME)
        if autostart_should_update(current, desired_command):
            write_value(RUN_KEY, AUTOSTART_VALUE_NAME, desired_command)
            logger.info("Autostart enabled via Registry Run key")
            return "enabled"
        return "already-enabled"
    if read_value(RUN_KEY, AUTOSTART_VALUE_NAME) is not None:
        delete_value(RUN_KEY, AUTOSTART_VALUE_NAME)
        logger.info("Autostart disabled (Registry value removed)")
        return "disabled"
    return "already-disabled"


def windows_backends():
    """Real winreg-backed primitives, or None off Windows."""
    try:
        import winreg
    except ImportError:
        return None

    def read_value(key, name):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                winreg.KEY_READ) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
                return value
        except OSError:
            return None

    def write_value(key, name, value):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete_value(key, name):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                            winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, name)

    return read_value, write_value, delete_value
