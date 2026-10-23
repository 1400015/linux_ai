try:
    import gi
    from gi.repository import Gtk
except ImportError:
    gi = None
    Gtk = None

from . import i18n
from .credential_settings import APIKeySettings
from .provider_settings import ProviderSettings
from .remote_model_settings import RemoteModelSettings


def _current_language():
    return i18n.get_language() if i18n.get_language() in i18n.available_languages() else "en"


def _language_combo():
    if Gtk is None:
        raise RuntimeError("Gtk is not available")
    lang_combo = Gtk.ComboBoxText()
    for code in i18n.available_languages():
        lang_combo.append(code, i18n.LANGUAGE_NAMES.get(code, code))
    lang_combo.set_active_id(_current_language())
    return lang_combo


def _theme_combo(config):
    if Gtk is None:
        raise RuntimeError("Gtk is not available")
    theme_combo = Gtk.ComboBoxText()
    available = config.get_available_themes()
    for theme in available:
        theme_info = config.get_theme_info(theme)
        display_name = theme_info.get("name", theme) if theme_info else theme
        theme_combo.append(theme, display_name)
    theme_combo.set_active_id(config.get("app.theme", "dark"))
    return theme_combo


def _add_settings_page(notebook, box, title):
    if Gtk is None:
        raise RuntimeError("Gtk is not available")
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.add(box)
    notebook.append_page(scroll, Gtk.Label(label=i18n._(title)))


def build_settings_dialog(window):
    """Build a settings dialog owned by the window.

    Returns a `_SettingsDialogState` holding the dialog and the widgets the
    window needs to read the draft, save, and jump to the Themes tab without
    reopening a nested dialog.
    """
    if Gtk is None:
        raise RuntimeError("Gtk is not available")
    dialog = Gtk.Dialog(
        title=i18n._("Settings - Linux AI Assistant"),
        parent=window,
        flags=0,
        buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK),
    )

    dialog.set_default_size(500, 400)

    content = dialog.get_content_area()
    notebook = Gtk.Notebook()
    content.pack_start(notebook, True, True, 0)

    # API section
    api_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    api_box.set_border_width(10)

    api_label = Gtk.Label(label="<b>" + i18n._("API Configuration") + "</b>")
    api_label.set_use_markup(True)
    api_box.pack_start(api_label, False, False, 0)

    provider_label = Gtk.Label(label=i18n._("AI Provider:"))
    api_box.pack_start(provider_label, False, False, 0)

    provider_combo = Gtk.ComboBoxText()
    for provider_name in window.config.get("api.providers", {}).keys():
        provider_combo.append(provider_name, provider_name)
    current_provider = window.config.get("api.default_provider", "openrouter")
    provider_combo.set_active_id(current_provider)
    api_box.pack_start(provider_combo, False, False, 0)

    credential_settings = APIKeySettings(window.config, provider_combo)
    api_box.pack_start(credential_settings, False, False, 0)

    _add_settings_page(notebook, api_box, "API")

    assistance_settings = ProviderSettings(window.config, window.ai_client)
    model_settings = RemoteModelSettings(
        window.config,
        window.ai_client,
        provider_combo,
        assistance_settings.mode,
        credential_settings=credential_settings,
    )
    api_box.pack_start(model_settings, False, False, 0)
    _add_settings_page(notebook, assistance_settings, "Assistance")

    # Appearance section
    ui_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    ui_box.set_border_width(10)

    ui_label = Gtk.Label(label="<b>" + i18n._("UI Configuration") + "</b>")
    ui_label.set_use_markup(True)
    ui_box.pack_start(ui_label, False, False, 0)

    opacity_label = Gtk.Label(label=i18n._("Window opacity:"))
    ui_box.pack_start(opacity_label, False, False, 0)

    opacity_scale = Gtk.Scale.new_with_range(
        Gtk.Orientation.HORIZONTAL,
        0.1, 1.0, 0.1,
    )
    opacity_scale.set_value(window.config.get("app.opacity", 0.9))
    ui_box.pack_start(opacity_scale, False, False, 0)

    always_on_top_check = Gtk.CheckButton(label=i18n._("Always visible"))
    always_on_top_check.set_active(window.config.get("app.always_on_top", True))
    ui_box.pack_start(always_on_top_check, False, False, 0)

    global_shortcut_check = Gtk.CheckButton(
        label=i18n._("Enable global shortcut to open the assistant"),
    )
    global_shortcut_check.set_active(
        window.config.get('app.global_shortcut_enabled', False),
    )
    ui_box.pack_start(global_shortcut_check, False, False, 0)

    global_shortcut_entry = Gtk.Entry()
    global_shortcut_entry.set_max_length(100)
    global_shortcut_entry.set_text(
        window.config.get('app.global_shortcut', '<Ctrl><Alt>space'),
    )
    global_shortcut_entry.set_placeholder_text('<Ctrl><Alt>space')
    ui_box.pack_start(global_shortcut_entry, False, False, 0)

    global_shortcut_status = Gtk.Label(
        label=i18n._(
            getattr(window.app, 'global_shortcut_status', 'Global shortcut disabled'),
        ),
        wrap=True,
        xalign=0,
    )
    ui_box.pack_start(global_shortcut_status, False, False, 0)

    # The window installs/restores global_shortcut_status_changed while this
    # dialog is open; the label is handed back in _SettingsDialogState.

    # Theme

    theme_label = Gtk.Label(label=i18n._("Theme:"))
    ui_box.pack_start(theme_label, False, False, 0)

    theme_combo = _theme_combo(window.config)
    ui_box.pack_start(theme_combo, False, False, 0)

    lang_label = Gtk.Label(label=i18n._("Language:"))
    ui_box.pack_start(lang_label, False, False, 0)

    lang_combo = _language_combo()
    ui_box.pack_start(lang_combo, False, False, 0)

    themes_btn = Gtk.Button(label=i18n._("Manage Themes"))
    themes_btn.connect("clicked", window.on_themes_clicked)
    ui_box.pack_start(themes_btn, False, False, 0)

    dock_check = Gtk.CheckButton(
        label=i18n._("Docked (reserves screen space)"),
    )
    dock_check.set_active(
        window.config.get("app.dock_mode", "float") == "dock",
    )
    ui_box.pack_start(dock_check, False, False, 0)

    dock_edge_label = Gtk.Label(label=i18n._("Dock edge:"))
    ui_box.pack_start(dock_edge_label, False, False, 0)

    dock_edge_combo = Gtk.ComboBoxText()
    for edge_id, edge_name in [
        ("right", i18n._("Right")),
        ("left", i18n._("Left")),
        ("top", i18n._("Top")),
        ("bottom", i18n._("Bottom")),
    ]:
        dock_edge_combo.append(edge_id, edge_name)
    dock_edge_combo.set_active_id(
        window.config.get("app.dock_edge", "right"),
    )
    ui_box.pack_start(dock_edge_combo, False, False, 0)

    _add_settings_page(notebook, ui_box, "Appearance")

    # Themes section
    themes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    themes_box.set_border_width(10)

    themes_label = Gtk.Label(label="<b>" + i18n._("Custom Themes") + "</b>")
    themes_label.set_use_markup(True)
    themes_box.pack_start(themes_label, False, False, 0)

    themes_listbox = Gtk.ListBox()
    themes_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
    populate_themes_list(themes_listbox, window.config)
    themes_box.pack_start(themes_listbox, True, True, 0)

    theme_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

    add_theme_btn = Gtk.Button(label=i18n._("Add Theme"))
    add_theme_btn.connect("clicked", window.on_add_theme_clicked)
    theme_buttons.pack_start(add_theme_btn, False, False, 0)

    remove_theme_btn = Gtk.Button(label=i18n._("Remove Theme"))
    remove_theme_btn.connect("clicked", window.on_remove_theme_clicked)
    theme_buttons.pack_start(remove_theme_btn, False, False, 0)

    themes_box.pack_start(theme_buttons, False, False, 0)

    _add_settings_page(notebook, themes_box, "Themes")

    # Features section
    features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    features_box.set_border_width(10)

    features_label = Gtk.Label(label="<b>" + i18n._("Features") + "</b>")
    features_label.set_use_markup(True)
    features_box.pack_start(features_label, False, False, 0)

    screen_capture_check = Gtk.CheckButton(label=i18n._("Screen capture"))
    screen_capture_check.set_active(
        window.config.get("features.screen_capture", True),
    )
    features_box.pack_start(screen_capture_check, False, False, 0)

    ocr_check = Gtk.CheckButton(label=i18n._("OCR (text recognition)"))
    ocr_check.set_active(
        window.config.get("features.ocr_enabled", True),
    )
    features_box.pack_start(ocr_check, False, False, 0)

    expert_check = Gtk.CheckButton(label=i18n._("Show Expert Mode button"))
    expert_check.set_active(
        window.config.get("features.expert_mode", True),
    )
    features_box.pack_start(expert_check, False, False, 0)

    _add_settings_page(notebook, features_box, "Features")

    dialog.show_all()

    return _SettingsDialogState(
        dialog=dialog,
        notebook=notebook,
        provider_combo=provider_combo,
        credential_settings=credential_settings,
        assistance_settings=assistance_settings,
        model_settings=model_settings,
        opacity_scale=opacity_scale,
        always_on_top_check=always_on_top_check,
        global_shortcut_check=global_shortcut_check,
        global_shortcut_entry=global_shortcut_entry,
        global_shortcut_status=global_shortcut_status,
        theme_combo=theme_combo,
        lang_combo=lang_combo,
        themes_btn=themes_btn,
        dock_check=dock_check,
        dock_edge_combo=dock_edge_combo,
        screen_capture_check=screen_capture_check,
        ocr_check=ocr_check,
        expert_check=expert_check,
        themes_listbox=themes_listbox,
    )


class _SettingsDialogState:
    """Widgets and state returned by build_settings_dialog()."""

    __slots__ = (
        "dialog",
        "notebook",
        "provider_combo",
        "credential_settings",
        "assistance_settings",
        "model_settings",
        "opacity_scale",
        "always_on_top_check",
        "global_shortcut_check",
        "global_shortcut_entry",
        "global_shortcut_status",
        "theme_combo",
        "lang_combo",
        "themes_btn",
        "dock_check",
        "dock_edge_combo",
        "screen_capture_check",
        "ocr_check",
        "expert_check",
        "themes_listbox",
    )

    def __init__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)


def populate_themes_list(listbox, config):
    """Populate a themes listbox from ConfigManager."""
    for child in listbox.get_children():
        listbox.remove(child)

    available = config.get_available_themes()
    for theme_name in available:
        theme_info = config.get_theme_info(theme_name)
        display_name = theme_info.get("name", theme_name) if theme_info else theme_name
        description = theme_info.get("description", "") if theme_info else ""

        row = Gtk.ListBoxRow()
        row._theme_id = theme_name
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        name_label = Gtk.Label(label=display_name)
        name_label.set_halign(Gtk.Align.START)
        box.pack_start(name_label, False, False, 0)

        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.set_xalign(0)
        desc_label.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        box.pack_start(desc_label, False, False, 0)

        row.add(box)
        listbox.add(row)
        row.show_all()
