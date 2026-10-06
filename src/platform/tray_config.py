"""Toolkit-free tray toggle decision shared by the GTK and Qt tracks."""
from __future__ import annotations


def toggle_on_click_enabled(config_manager, default=True) -> bool:
    """Whether clicking the tray icon should toggle the main window.

    Reads ``app.tray_toggle_on_click`` (default true) through the shared
    config contract; a broken config keeps the given default. Lives in
    ``src.platform`` (not in tray_icon.py) so the Qt track can use it on
    Windows without importing GTK.
    """
    try:
        return bool(config_manager.get("app.tray_toggle_on_click", default))
    except Exception:
        return default
