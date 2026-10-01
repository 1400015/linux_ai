import gi
import os
import json
import time
import re
import threading
from typing import Optional, Dict
from pathlib import Path

try:
    import notify2
except ImportError:
    notify2 = None

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')

from gi.repository import Gtk, GLib
import logging

from . import dock, file_actions, offline_assistant
from .ai_client import AIProviderError
from .chat_view import ChatView
from .history_store import HistoryStore
from .i18n import _, get_language

# Set up logger
logger = logging.getLogger(__name__)

# Values coming from theme files are interpolated into GTK's CSS; validating
# the format prevents a malicious JSON from injecting arbitrary style rules.
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 _.,'-]{1,64}$")

# Context budget: by default 12000 characters (~3k tokens) and 20 messages.
MAX_CONTEXT_CHARS = 12000
MAX_CONTEXT_MESSAGES = 20

# Teto de mensagens em memória e em history.json (o writer usa o mesmo).
MAX_HISTORY_MESSAGES = 1000

# Intervalo mínimo entre flushes de chunks streaming para a UI (segundos).
# Coalesce os chunks recebidos entre flushes: sem isto, cada chunk agendava
# um idle próprio (+ um de scroll), milhares por resposta rápida.
STREAM_FLUSH_INTERVAL = 0.08


def safe_color(value, fallback="#1e1e1e"):
    """Return a valid CSS color or `fallback`."""
    if isinstance(value, str) and _COLOR_RE.match(value.strip()):
        return value.strip()
    if value is not None:
        logger.warning(f"Invalid theme color ignored: {value!r}")
    return fallback


def safe_font_family(value, fallback="Monospace"):
    """Return a safe font-family name for CSS."""
    if isinstance(value, str) and _FONT_FAMILY_RE.match(value.strip()):
        return value.strip()
    if value is not None:
        logger.warning(f"Invalid theme font family ignored: {value!r}")
    return fallback


def safe_number(value, fallback, cast, minimum=None, maximum=None):
    """Convert `value` to a number with bounds, or return `fallback`."""
    try:
        number = cast(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None and number < minimum:
        return fallback
    if maximum is not None and number > maximum:
        return fallback
    return number


class MainWindow(Gtk.Window):
    """Application main window"""

    def __init__(self, app, config_manager, ai_client, system_utils):
        super().__init__(title="Linux AI Assistant")

        self.app = app
        self.config = config_manager
        self.ai_client = ai_client
        self.system_utils = system_utils
        # Answers basic questions and offers local tasks when no API key is
        # configured or the provider cannot be reached.
        self.offline = offline_assistant.OfflineAssistant(system_utils, config_manager)

        # Configurar janela
        self.set_default_size(
            config_manager.get("app.width", 400),
            config_manager.get("app.height", 500)
        )

        # Posicionar janela
        self.move(
            config_manager.get("app.x_position", 100),
            config_manager.get("app.y_position", 100)
        )

        # Configure transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
            self.set_opacity(config_manager.get("app.opacity", 0.9))

        # Make window always visible
        self.set_keep_above(config_manager.get("app.always_on_top", True))
        self.stick()

        # Docked mode: pin to an edge and reserve screen space.
        # O gtk-layer-shell exige init ANTES de realize: em Wayland tentamos
        # aplicar já no __init__ (apply_dock confirma is_layer_window e cai
        # para o fallback se não tiver efeito); em X11 os struts precisam do
        # GdkWindow, pelo que ficam para _on_realize_dock().
        self._dock_edge = config_manager.get("app.dock_edge", "right")
        self._dock_width = config_manager.get("app.width", 400)
        self.dock_method = None
        if config_manager.get("app.dock_mode", "float") == "dock":
            self.set_decorated(False)
            if dock.apply_dock(self, self._dock_edge, self._dock_width) == "layer-shell":
                self.dock_method = "layer-shell"
                logger.info("Dock applied pre-realize via layer-shell")
            else:
                self.connect("realize", self._on_realize_dock)
        else:
            self.set_decorated(True)

        # Make window resizable
        self.set_resizable(True)

        # Configurar estilo
        self._setup_style()

        # Configure notifications
        self._setup_notifications()

        # State variables
        # Expert mode is persisted (app.expert_mode); `features.expert_mode`
        # only controls whether the button is shown at all.
        self.expert_mode = bool(self.config.get("app.expert_mode", False))
        self.conversation_history = []
        self.streaming = False
        self.is_loading = False
        # Per-request state: each send gets a new id and its own cancel event,
        # so a cancelled worker can never re-arm itself or corrupt the next
        # request (see _process_message / _finalize_response).
        self._request_seq = 0
        self._active_request = 0
        self._cancel_event = threading.Event()
        # Settings dialog currently open (or None); lets the "Manage Themes"
        # button switch tabs instead of opening a second dialog.
        self._settings_notebook = None
        # Interval (start, end) of the "Thinking..." placeholder and the
        # streamed body offset vivem no ChatView (ver _create_ui).
        # History is persisted from a single background writer (FIFO) owned
        # by HistoryStore (GTK-free), so the GTK main loop never blocks on a
        # full file rewrite per message.
        self.history_store = HistoryStore()

        # Create interface
        self._create_ui()

        # Connect signals
        self.connect("delete-event", self.on_delete_event)
        # NB: apenas `configure-event` — já reporta x/y/width/height; ligar
        # também `size-allocate` duplicava a escrita de app.width/height por
        # evento de layout.
        self.connect("configure-event", self.on_configure_event)

        # Keyboard shortcuts
        self._setup_keybindings()

        # Load conversation history
        self._load_conversation_history()

        logger.info("Main window initialized")

    def _setup_notifications(self):
        """Configure system notifications"""
        self.notifications_enabled = False
        try:
            if notify2 is not None:
                notify2.init("Linux AI Assistant")
                self.notifications_enabled = True
                logger.info("System notifications enabled")
        except Exception as e:
            logger.warning(f"Could not enable notifications: {e}")

    def _on_realize_dock(self, widget):
        """Apply dock struts once the GdkWindow exists (X11 needs it)."""
        if self.dock_method == "layer-shell":
            return  # already applied pre-realize on Wayland
        self.dock_method = dock.apply_dock(self, self._dock_edge, self._dock_width)
        logger.info(f"Dock applied via {self.dock_method}")

    def _reapply_dock(self):
        """Re-apply dock/float settings after they change in the dialog."""
        mode = self.config.get("app.dock_mode", "float")
        self._dock_edge = self.config.get("app.dock_edge", "right")
        self._dock_width = self.config.get("app.width", 400)
        if mode == "dock":
            self.set_decorated(False)
            if self.get_realized():
                self.dock_method = dock.apply_dock(self, self._dock_edge, self._dock_width)
                logger.info(f"Dock re-applied via {self.dock_method}")
            # otherwise _on_realize_dock will handle it
        else:
            self.set_decorated(True)
            dock.apply_float(self, self.config.get("app.always_on_top", True))
            self.dock_method = None
            logger.info("Dock removed (floating mode)")

    def _apply_feature_toggles(self):
        """Show/hide the feature buttons according to features.* settings."""
        if hasattr(self, "capture_btn"):
            self.capture_btn.set_visible(self.config.get("features.screen_capture", True))
        if hasattr(self, "expert_btn"):
            self.expert_btn.set_visible(self.config.get("features.expert_mode", True))

    def close_history_writer(self, timeout: float = 1.0):
        """Flush pending history writes and stop the writer thread."""
        self.history_store.close(timeout)

    def show_notification(self, title: str, message: str, icon: str = "dialog-information"):
        """Show system notification"""
        if self.notifications_enabled and notify2 is not None:
            try:
                n = notify2.Notification(title, message, icon)
                n.show()
                logger.debug(f"Notification shown: {title}")
            except Exception as e:
                logger.error(f"Error showing notification: {e}")

    def _setup_keybindings(self):
        """Set up keyboard shortcuts"""
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)

        # Ctrl+Enter also sends (plain Enter already activates the entry)
        key, mod = Gtk.accelerator_parse("<Control>Return")
        self.input_entry.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

        # Escape to clear input
        key, mod = Gtk.accelerator_parse("Escape")
        accel_group.connect(key, mod,
                          Gtk.AccelFlags.VISIBLE, self.on_clear_input)

        # Ctrl+E to toggle expert mode
        key, mod = Gtk.accelerator_parse("<Control>e")
        self.expert_btn.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

        # Ctrl+Q to close
        key, mod = Gtk.accelerator_parse("<Control>q")
        accel_group.connect(key, mod,
                          Gtk.AccelFlags.VISIBLE, lambda *args: self.on_close_clicked())

        # Ctrl+S to capture screen
        key, mod = Gtk.accelerator_parse("<Control>s")
        accel_group.connect(key, mod,
                          Gtk.AccelFlags.VISIBLE, lambda *args: self.on_capture_screen_clicked(None))

        logger.info("Keyboard shortcuts configured")

    def on_clear_input(self, *args):
        """Clear input when Escape is pressed"""
        self.input_entry.set_text("")
        logger.debug("Input cleared")

    def _setup_style(self):
        """Set up window CSS styling"""
        # add_provider_for_screen() is cumulative: remove the previous provider
        # first so changing theme does not stack stylesheets indefinitely.
        previous = getattr(self, "_style_provider", None)
        if previous is not None:
            try:
                Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), previous)
            except Exception as e:
                logger.warning(f"Could not remove previous CSS provider: {e}")
        style_provider = Gtk.CssProvider()

        # Get cores do tema
        colors = self.config.get_theme_colors()
        theme_info = self.config.get_theme_info(self.config.get("app.theme", "dark"))

        # Get cores de syntax highlighting do tema
        syntax_colors = {}
        if theme_info and 'syntax_highlighting' in theme_info:
            syntax_colors = theme_info['syntax_highlighting']

        # Get settings de UI do tema
        theme_ui = {}
        if theme_info and 'ui' in theme_info:
            theme_ui = theme_info['ui']

        # Usar valores validados do theme (ver safe_color/safe_font_family)
        bg_color = safe_color(colors.get('background'), '#1e1e1e')
        text_color = safe_color(colors.get('text'), '#e0e0e0')
        accent_color = safe_color(colors.get('accent'), '#4CAF50')
        secondary_color = safe_color(colors.get('secondary'), '#2d2d2d')
        tertiary_color = safe_color(colors.get('tertiary'), '#252525')

        font_family = safe_font_family(
            theme_ui.get('font_family') or self.config.get('ui.font_family'),
            'Monospace',
        )
        font_size = safe_number(
            theme_ui.get('font_size') or self.config.get('ui.font_size'),
            12, int, minimum=4, maximum=72,
        )
        border_radius = safe_number(
            theme_ui.get('border_radius') or self.config.get('ui.border_radius'),
            10, int, minimum=0, maximum=64,
        )

        # Cores de syntax highlighting
        user_msg_color = safe_color(syntax_colors.get('user_message'), '#e0e0e0')
        ai_msg_color = safe_color(syntax_colors.get('ai_message'), '#a0d0a0')
        system_msg_color = safe_color(syntax_colors.get('system_message'), '#808080')

        css = f"""
        #main-box {{
            background-color: {bg_color};
            color: {text_color};
            border-radius: {border_radius}px;
            padding: 10px;
            margin: 5px;
        }}

        #header {{
            background-color: {secondary_color};
            border-radius: {border_radius}px {border_radius}px 0 0;
            padding: 8px;
            margin-bottom: 10px;
        }}

        #chat-area {{
            background-color: {tertiary_color};
            border-radius: 5px;
            padding: 10px;
            margin-bottom: 10px;
            min-height: 300px;
        }}

        #input-area {{
            background-color: {secondary_color};
            border-radius: 5px;
            padding: 10px;
        }}

        textview {{
            font-family: {font_family};
            font-size: {font_size}pt;
            background-color: {tertiary_color};
            color: {text_color};
            border: none;
            padding: 5px;
        }}

        textview.user-message {{
            color: {user_msg_color};
            font-family: {font_family};
            font-size: {font_size}pt;
        }}

        textview.ai-message {{
            color: {ai_msg_color};
            font-family: {font_family};
            font-size: {font_size}pt;
        }}

        textview.system-message {{
            color: {system_msg_color};
            font-family: {font_family};
            font-size: {font_size}pt;
        }}

        button {{
            background-color: {accent_color};
            color: white;
            border-radius: 5px;
            padding: 5px 10px;
            font-family: {font_family};
            font-size: 10pt;
            border: none;
            min-width: 40px;
        }}

        button:hover {{
            opacity: 0.9;
        }}

        button:active {{
            opacity: 0.7;
        }}

        button.expert {{
            background-color: #2196F3;
        }}

        button.expert:hover {{
            background-color: #0b7dda;
        }}

        button.danger {{
            background-color: #f44336;
        }}

        button.danger:hover {{
            background-color: #da190b;
        }}

        entry {{
            background-color: {secondary_color};
            color: {text_color};
            border-radius: 5px;
            padding: 5px;
            font-family: {font_family};
            font-size: {font_size}pt;
            border: none;
        }}

        entry:focus {{
            outline: none;
            border: 1px solid {accent_color};
        }}

        scrolledwindow {{
            background-color: {tertiary_color};
            border-radius: 5px;
            border: none;
        }}

        .loading {{
            opacity: 0.7;
            font-style: italic;
        }}

        .expert-mode {{
            border-left: 3px solid #2196F3;
            padding-left: 10px;
        }}
        """

        style_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._style_provider = style_provider
        logger.debug("CSS style applied with theme: " + self.config.get("app.theme", "dark"))

    def _create_ui(self):
        """Create the window interface"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        main_box.set_property("name", "main-box")
        self.add(main_box)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        header.set_property("name", "header")
        main_box.pack_start(header, False, False, 0)

        # Menu button
        menu_btn = Gtk.Button.new_from_icon_name("open-menu", Gtk.IconSize.MENU)
        menu_btn.connect("clicked", self.on_menu_clicked)
        menu_btn.set_tooltip_text(_("Menu"))
        header.pack_start(menu_btn, False, False, 0)

        # Title
        title_label = Gtk.Label(label=_("Linux AI Assistant"))
        title_label.set_halign(Gtk.Align.START)
        title_label.set_valign(Gtk.Align.CENTER)
        header.pack_start(title_label, True, True, 0)

        # Icon de estado
        self.status_icon = Gtk.Image.new_from_icon_name("emblem-ok", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Ready"))
        header.pack_end(self.status_icon, False, False, 0)

        # Close button
        close_btn = Gtk.Button.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        close_btn.connect("clicked", lambda btn: self.on_close_clicked())
        close_btn.set_tooltip_text(_("Close"))
        header.pack_end(close_btn, False, False, 0)

        # Minimize button
        minimize_btn = Gtk.Button.new_from_icon_name("window-minimize", Gtk.IconSize.MENU)
        minimize_btn.connect("clicked", lambda btn: self.iconify())
        minimize_btn.set_tooltip_text(_("Minimize"))
        header.pack_end(minimize_btn, False, False, 0)

        # Chat area
        chat_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        chat_area.set_property("name", "chat-area")
        main_box.pack_start(chat_area, True, True, 0)

        # ChatView: buffer, tags, offsets, placeholder e scroll (GTK-isolado)
        self.chat_view = ChatView(
            font_family=self.config.get('ui.font_family', 'Monospace'),
            font_size=self.config.get('ui.font_size', 12),
        )
        self.chat_scrolled = self.chat_view.scrolled
        self.chat_textview = self.chat_view.textview
        chat_area.pack_start(self.chat_scrolled, True, True, 0)

        # Input area
        input_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        input_area.set_property("name", "input-area")
        main_box.pack_start(input_area, False, False, 0)

        # Entry for input
        self.input_entry = Gtk.Entry()
        # Both plain Enter and Ctrl+Enter send (the accelerator below also
        # triggers "activate"), so the old "Ctrl+Enter" hint was misleading.
        self.input_entry.set_placeholder_text(_("Type your message... (Enter to send)"))
        self.input_entry.connect("activate", self.on_input_activate)
        self.input_entry.set_hexpand(True)
        input_area.pack_start(self.input_entry, True, True, 0)

        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        input_area.pack_end(button_box, False, False, 0)

        # Cancel button
        self.cancel_btn = Gtk.Button.new_from_icon_name("process-stop", Gtk.IconSize.MENU)
        self.cancel_btn.connect("clicked", self.on_cancel_streaming)
        self.cancel_btn.set_tooltip_text(_("Cancel"))
        self.cancel_btn.set_sensitive(False)
        button_box.pack_start(self.cancel_btn, False, False, 0)

        # Screen capture button (hidden when features.screen_capture is off)
        capture_btn = Gtk.Button.new_from_icon_name("camera-photo", Gtk.IconSize.MENU)
        capture_btn.connect("clicked", self.on_capture_screen_clicked)
        capture_btn.set_tooltip_text(_("Capture screen (Ctrl+S)"))
        button_box.pack_start(capture_btn, False, False, 0)
        self.capture_btn = capture_btn

        # Expert mode button (hidden when features.expert_mode is off)
        self.expert_btn = Gtk.Button.new_from_icon_name("system-run", Gtk.IconSize.MENU)
        self.expert_btn.connect("clicked", self.on_expert_mode_toggled)
        self.expert_btn.set_tooltip_text(_("Expert Mode (Ctrl+E)"))
        button_box.pack_start(self.expert_btn, False, False, 0)

        # Send button
        send_btn = Gtk.Button.new_from_icon_name("go-next", Gtk.IconSize.MENU)
        send_btn.connect("clicked", lambda btn: self.on_send_clicked())
        send_btn.set_tooltip_text(_("Send (Enter)"))
        button_box.pack_start(send_btn, False, False, 0)

        # Hide the feature buttons that are disabled in settings
        self._apply_feature_toggles()
        # Restore the persisted expert mode visually (the toggle only styles
        # the button when the user clicks it).
        if self.expert_mode and hasattr(self, "expert_btn"):
            self.expert_btn.get_style_context().add_class("expert")

        # Add welcome message
        self._add_system_message(_("Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen."))

        # Auto-scroll to bottom
        self._scroll_to_bottom()

        logger.info("UI created successfully")

    def on_menu_clicked(self, button):
        """Show options menu"""
        menu = Gtk.Menu()

        # Settings option
        config_item = Gtk.MenuItem(label=_("Settings"))
        config_item.connect("activate", self.on_config_clicked)
        menu.append(config_item)

        # History option
        history_item = Gtk.MenuItem(label=_("Conversation History"))
        history_item.connect("activate", self.on_history_clicked)
        menu.append(history_item)

        # Statistics option
        stats_item = Gtk.MenuItem(label=_("Statistics"))
        stats_item.connect("activate", self.on_stats_clicked)
        menu.append(stats_item)

        # Separador
        menu.append(Gtk.SeparatorMenuItem())

        # Quit option
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", lambda *args: self.on_close_clicked())
        menu.append(quit_item)

        menu.show_all()
        menu.popup_at_pointer(None)  # Show no cursor

    def on_config_clicked(self, item):
        """Open settings window"""
        self._show_config_dialog()

    def on_themes_clicked(self, button):
        """Open the settings dialog on the Themes tab.

        The "Manage Themes" button lives inside the dialog itself, so
        reopening it would stack a second modal dialog on top of the first.
        """
        if self._settings_notebook is None:
            self._show_config_dialog()
            return
        # Themes is the third page (API, Appearance, Themes, Features)
        self._settings_notebook.set_current_page(2)

    def on_add_theme_clicked(self, button):
        """Add a new theme"""
        dialog = Gtk.Dialog(
            title="Add Theme",
            parent=self,
            flags=0,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )

        content = dialog.get_content_area()

        # Theme name
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        name_label = Gtk.Label(label=_("Name:"))
        name_entry = Gtk.Entry()
        name_box.pack_start(name_label, False, False, 0)
        name_box.pack_start(name_entry, True, True, 0)
        content.pack_start(name_box, False, False, 0)

        # Description
        desc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        desc_label = Gtk.Label(label=_("Description:"))
        desc_entry = Gtk.Entry()
        desc_box.pack_start(desc_label, False, False, 0)
        desc_box.pack_start(desc_entry, True, True, 0)
        content.pack_start(desc_box, False, False, 0)

        # Colors
        colors_frame = Gtk.Frame(label=_("Colors"))
        colors_grid = Gtk.Grid()
        colors_grid.set_column_spacing(10)
        colors_grid.set_row_spacing(5)
        colors_frame.add(colors_grid)
        content.pack_start(colors_frame, False, False, 0)

        # Background
        bg_label = Gtk.Label(label=_("Background:"))
        bg_entry = Gtk.Entry()
        bg_entry.set_placeholder_text("#1e1e1e")
        bg_entry.set_text("#1e1e1e")
        colors_grid.attach(bg_label, 0, 0, 1, 1)
        colors_grid.attach(bg_entry, 1, 0, 1, 1)

        # Text
        text_label = Gtk.Label(label=_("Text:"))
        text_entry = Gtk.Entry()
        text_entry.set_placeholder_text("#e0e0e0")
        text_entry.set_text("#e0e0e0")
        colors_grid.attach(text_label, 0, 1, 1, 1)
        colors_grid.attach(text_entry, 1, 1, 1, 1)

        # Accent
        accent_label = Gtk.Label(label=_("Accent:"))
        accent_entry = Gtk.Entry()
        accent_entry.set_placeholder_text("#4CAF50")
        accent_entry.set_text("#4CAF50")
        colors_grid.attach(accent_label, 0, 2, 1, 1)
        colors_grid.attach(accent_entry, 1, 2, 1, 1)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            theme_name = name_entry.get_text().strip()
            # The name becomes a file name: reject anything that is not a
            # simple identifier (path separators, "..", spaces, etc.).
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", theme_name):
                self.show_notification(
                    "Linux AI Assistant",
                    _("A theme name is required (letters, digits, '-' or '_', max 64)")
                )
                dialog.destroy()
                return

            # Create theme
            theme = {
                "name": theme_name,
                "description": desc_entry.get_text().strip(),
                "colors": {
                    "background": bg_entry.get_text().strip(),
                    "text": text_entry.get_text().strip(),
                    "accent": accent_entry.get_text().strip(),
                    "secondary": "#2d2d2d",
                    "tertiary": "#252525"
                },
                "ui": {
                    "font_family": "Monospace",
                    "font_size": 12,
                    "border_radius": 10
                }
            }

            # Save theme
            themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            themes_dir.mkdir(parents=True, exist_ok=True)
            theme_file = themes_dir / f"{theme_name}.json"

            try:
                with open(theme_file, 'w', encoding='utf-8') as f:
                    json.dump(theme, f, indent=2, ensure_ascii=False)

                self.show_notification("Linux AI Assistant", f"Theme '{theme_name}' created")
                self._populate_themes_list()

            except Exception as e:
                self.show_notification("Linux AI Assistant", f"Error saving theme: {e}")

        dialog.destroy()

    def on_remove_theme_clicked(self, button):
        """Remove the selected theme"""
        selected_row = self.themes_listbox.get_selected_row()
        if not selected_row:
            self.show_notification("Linux AI Assistant", _("No theme selected"))
            return

        theme_name = getattr(selected_row, "_theme_id", None)
        if not theme_name:
            self.show_notification("Linux AI Assistant", _("No theme selected"))
            return

        # Do not allow removing built-in themes (they ship with the app,
        # under the package's themes dir, not the user's)
        predefined_themes = ["dark", "light", "dracula", "solarized-dark"]
        if theme_name in predefined_themes:
            self.show_notification("Linux AI Assistant", _("Cannot remove built-in themes"))
            return

        # Confirm removal
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Remove theme '{theme_name}'?"
        )

        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            theme_file = themes_dir / f"{theme_name}.json"

            try:
                # Resolve and confirm the target really is inside the
                # user's themes dir before deleting.
                resolved = theme_file.resolve()
                if resolved.parent != themes_dir.resolve():
                    raise ValueError(f"Refusing to delete outside themes dir: {resolved}")
                if resolved.exists():
                    resolved.unlink()
                    self.show_notification("Linux AI Assistant", f"Theme '{theme_name}' removed")
                    self._populate_themes_list()
            except Exception as e:
                self.show_notification("Linux AI Assistant", f"Error removing theme: {e}")

    def _populate_themes_list(self):
        """Populate the themes list"""
        # Clear list
        for child in self.themes_listbox.get_children():
            self.themes_listbox.remove(child)

        # Get available themes
        available_themes = self.config.get_available_themes()

        for theme_name in available_themes:
            theme_info = self.config.get_theme_info(theme_name)
            display_name = theme_info.get("name", theme_name) if theme_info else theme_name
            description = theme_info.get("description", "") if theme_info else ""

            row = Gtk.ListBoxRow()
            # Keep the file stem on the row: the visible label is the
            # display name, which may differ from the file name.
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
            self.themes_listbox.add(row)
            row.show_all()

    def on_history_clicked(self, item):
        """Show conversation history"""
        self._show_history_dialog()

    def on_stats_clicked(self, item):
        """Show usage statistics"""
        self._show_stats_dialog()

    def _show_stats_dialog(self):
        """Show statistics dialog"""
        dialog = Gtk.Dialog(
            title=_("Statistics - Linux AI Assistant"),
            parent=self,
            flags=0,
            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )
        dialog.set_default_size(400, 300)

        content = dialog.get_content_area()

        # Get statistics
        token_usage = self.ai_client.get_token_usage()

        # Create box vertical
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(10)
        content.add(box)

        # Title
        title = Gtk.Label(label="<b>" + _("Usage Statistics") + "</b>")
        title.set_use_markup(True)
        box.pack_start(title, False, False, 0)

        # Tokens by provider. The labels are kept so the reset button can
        # refresh them in place instead of leaving stale numbers on screen.
        token_labels = []
        for provider, usage in token_usage.items():
            provider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

            provider_label = Gtk.Label(label=f"{provider}:")
            provider_label.set_halign(Gtk.Align.START)
            provider_box.pack_start(provider_label, False, False, 0)

            tokens_label = Gtk.Label(label=f"Input: {usage.get('input', 0)}, Output: {usage.get('output', 0)}, Total: {usage.get('total', 0)}")
            tokens_label.set_halign(Gtk.Align.END)
            provider_box.pack_end(tokens_label, True, True, 0)

            box.pack_start(provider_box, False, False, 0)
            token_labels.append((provider, tokens_label))

        # Button to reset statistics
        def _reset_stats(btn):
            self.ai_client.reset_token_usage()
            for provider, tokens_label in token_labels:
                tokens_label.set_text("Input: 0, Output: 0, Total: 0")
        reset_btn = Gtk.Button(label=_("Reset Statistics"))
        reset_btn.connect("clicked", _reset_stats)
        reset_btn.set_halign(Gtk.Align.CENTER)
        box.pack_start(reset_btn, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _show_history_dialog(self):
        """Show history dialog"""
        dialog = Gtk.Dialog(
            title=_("Conversation History - Linux AI Assistant"),
            parent=self,
            flags=0,
            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )
        dialog.set_default_size(500, 400)

        content = dialog.get_content_area()

        # Create scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        content.add(scrolled)

        # Create text view
        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_cursor_visible(False)
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled.add(textview)

        # Load history
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        buffer = textview.get_buffer()
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)

                lines = []
                for msg in history:
                    role = msg.get("role", "unknown")
                    text = msg.get("content", "")
                    timestamp = msg.get("timestamp", 0)
                    timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S',
                                                  time.localtime(timestamp))
                    lines.append(f"[{timestamp_str}] [{role}]\n{text}\n")
                # Uma única inserção: 1000 buffer.insert síncronos na main
                # thread congelavam a UI antes de o diálogo abrir.
                buffer.insert(buffer.get_end_iter(), "\n".join(lines))
            except Exception as e:
                logger.error(f"Error loading history: {e}")
                buffer.insert(buffer.get_end_iter(), f"Error loading history: {e}")
        else:
            buffer.insert(buffer.get_end_iter(), "No history available.")

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _show_config_dialog(self):
        """Show settings dialog"""
        dialog = Gtk.Dialog(
            title=_("Settings - Linux AI Assistant"),
            parent=self,
            flags=0,
            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )

        dialog.set_default_size(500, 400)

        # Create dialog content
        content = dialog.get_content_area()

        # Notebook for sections
        notebook = Gtk.Notebook()
        content.add(notebook)
        # Kept so on_themes_clicked can jump to the Themes tab instead of
        # reopening a second (nested) dialog.
        self._settings_notebook = notebook

        # API section
        api_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        api_box.set_border_width(10)

        api_label = Gtk.Label(label="<b>" + _("API Configuration") + "</b>")
        api_label.set_use_markup(True)
        api_box.pack_start(api_label, False, False, 0)

        # Provider
        provider_label = Gtk.Label(label=_("AI Provider:"))
        api_box.pack_start(provider_label, False, False, 0)

        provider_combo = Gtk.ComboBoxText()
        for provider_name in self.config.get("api.providers", {}).keys():
            provider_combo.append(provider_name, provider_name)
        current_provider = self.config.get("api.default_provider", "openrouter")
        provider_combo.set_active_id(current_provider)
        api_box.pack_start(provider_combo, False, False, 0)

        # API Key
        api_key_label = Gtk.Label(label=_("API Key:"))
        api_box.pack_start(api_key_label, False, False, 0)

        api_key_entry = Gtk.Entry()
        api_key_entry.set_visibility(False)
        api_key_entry.set_invisible_char('*')
        api_key_entry.set_placeholder_text(_("Enter your API Key"))

        # Load current API key
        api_key_entry.set_text(self.config.get_api_key(current_provider) or "")
        api_box.pack_start(api_key_entry, False, False, 0)

        # Warn when an environment variable overrides the key: env vars take
        # precedence over config.json, so saving here would look like a no-op.
        api_key_warning = Gtk.Label()
        api_key_warning.set_halign(Gtk.Align.START)
        api_key_warning.set_line_wrap(True)
        api_key_warning.set_no_show_all(True)
        api_box.pack_start(api_key_warning, False, False, 0)

        # Shown only while an env override is active: brings back the value
        # that is actually stored in config.json, so it can be inspected.
        reload_key_btn = Gtk.Button(label=_("Reload saved keys"))
        reload_key_btn.set_halign(Gtk.Align.START)
        reload_key_btn.set_no_show_all(True)
        api_box.pack_start(reload_key_btn, False, False, 0)

        # Persist the effective (env) key into config.json, so removing the
        # environment variable later still leaves a working key behind.
        copy_key_btn = Gtk.Button(label=_("Copy effective key to config"))
        copy_key_btn.set_halign(Gtk.Align.START)
        copy_key_btn.set_no_show_all(True)
        api_box.pack_start(copy_key_btn, False, False, 0)

        def _refresh_key_warning(provider):
            override = self.config.get_api_key_env_override(provider)
            if override:
                api_key_warning.set_text(
                    _("Note: the {var} environment variable overrides this key.")
                    .format(var=override)
                )
                api_key_warning.show()
                reload_key_btn.show()
                copy_key_btn.show()
            else:
                api_key_warning.hide()
                reload_key_btn.hide()
                copy_key_btn.hide()

        # The entry always holds the key of the provider shown in the combo.
        # Without this, switching provider saved the previous provider's key
        # under the newly selected provider's name (the entry was never
        # reloaded on change).
        loaded_provider = [current_provider]

        def on_provider_changed(combo):
            selected = combo.get_active_id()
            if selected:
                loaded_provider[0] = selected
                api_key_entry.set_text(self.config.get_api_key(selected) or "")
                _refresh_key_warning(selected)

        def on_reload_saved_keys(button):
            # Ignore the env override on purpose: show the stored value.
            api_key_entry.set_text(
                self.config.get_stored_api_key(loaded_provider[0])
            )

        def on_copy_effective_key(button):
            provider = loaded_provider[0]
            # While the env var is set, get_api_key() returns it: that is the
            # key the application is actually using.
            effective = self.config.get_api_key(provider) or ""
            self.config.set_api_key(provider, effective)
            self.config.save()
            api_key_entry.set_text(effective)
            logger.info("Effective API key copied to config.json for %s", provider)
            self.show_notification(
                "Linux AI Assistant", _("Key copied to config.json")
            )

        provider_combo.connect("changed", on_provider_changed)
        reload_key_btn.connect("clicked", on_reload_saved_keys)
        copy_key_btn.connect("clicked", on_copy_effective_key)
        _refresh_key_warning(current_provider)

        notebook.append_page(api_box, Gtk.Label(label=_("API")))

        # Appearance section
        ui_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ui_box.set_border_width(10)

        ui_label = Gtk.Label(label="<b>" + _("UI Configuration") + "</b>")
        ui_label.set_use_markup(True)
        ui_box.pack_start(ui_label, False, False, 0)

        # Opacity
        opacity_label = Gtk.Label(label=_("Window opacity:"))
        ui_box.pack_start(opacity_label, False, False, 0)

        opacity_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.1, 1.0, 0.1
        )
        opacity_scale.set_value(self.config.get("app.opacity", 0.9))
        ui_box.pack_start(opacity_scale, False, False, 0)

        # Always visible
        always_on_top_check = Gtk.CheckButton(label=_("Always visible"))
        always_on_top_check.set_active(self.config.get("app.always_on_top", True))
        ui_box.pack_start(always_on_top_check, False, False, 0)

        # Theme
        theme_label = Gtk.Label(label=_("Theme:"))
        ui_box.pack_start(theme_label, False, False, 0)

        theme_combo = Gtk.ComboBoxText()
        # Load available themes
        available_themes = self.config.get_available_themes()
        for theme in available_themes:
            theme_info = self.config.get_theme_info(theme)
            display_name = theme_info.get("name", theme) if theme_info else theme
            theme_combo.append(theme, display_name)

        theme_combo.set_active_id(self.config.get("app.theme", "dark"))
        ui_box.pack_start(theme_combo, False, False, 0)

        # Button to manage themes
        themes_btn = Gtk.Button(label=_("Manage Themes"))
        themes_btn.connect("clicked", self.on_themes_clicked)
        ui_box.pack_start(themes_btn, False, False, 0)

        # Docked mode (dock)
        dock_check = Gtk.CheckButton(label=_("Docked (reserves screen space)"))
        dock_check.set_active(self.config.get("app.dock_mode", "float") == "dock")
        ui_box.pack_start(dock_check, False, False, 0)

        dock_edge_label = Gtk.Label(label=_("Dock edge:"))
        ui_box.pack_start(dock_edge_label, False, False, 0)

        dock_edge_combo = Gtk.ComboBoxText()
        for edge_id, edge_name in [("right", _("Right")), ("left", _("Left")),
                                   ("top", _("Top")), ("bottom", _("Bottom"))]:
            dock_edge_combo.append(edge_id, edge_name)
        dock_edge_combo.set_active_id(self.config.get("app.dock_edge", "right"))
        ui_box.pack_start(dock_edge_combo, False, False, 0)

        notebook.append_page(ui_box, Gtk.Label(label=_("Appearance")))

        # Themes section
        themes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        themes_box.set_border_width(10)

        themes_label = Gtk.Label(label="<b>" + _("Custom Themes") + "</b>")
        themes_label.set_use_markup(True)
        themes_box.pack_start(themes_label, False, False, 0)

        # Theme list
        self.themes_listbox = Gtk.ListBox()
        # SINGLE (not NONE): with NONE the rows can never be selected and
        # "Remove Theme" always reports "No theme selected".
        self.themes_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._populate_themes_list()
        themes_box.pack_start(self.themes_listbox, True, True, 0)

        # Theme buttons
        theme_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        add_theme_btn = Gtk.Button(label=_("Add Theme"))
        add_theme_btn.connect("clicked", self.on_add_theme_clicked)
        theme_buttons.pack_start(add_theme_btn, False, False, 0)

        remove_theme_btn = Gtk.Button(label=_("Remove Theme"))
        remove_theme_btn.connect("clicked", self.on_remove_theme_clicked)
        theme_buttons.pack_start(remove_theme_btn, False, False, 0)

        themes_box.pack_start(theme_buttons, False, False, 0)

        notebook.append_page(themes_box, Gtk.Label(label=_("Themes")))

        # Features section
        features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        features_box.set_border_width(10)

        features_label = Gtk.Label(label="<b>" + _("Features") + "</b>")
        features_label.set_use_markup(True)
        features_box.pack_start(features_label, False, False, 0)

        # Screen capture
        screen_capture_check = Gtk.CheckButton(label=_("Screen capture"))
        screen_capture_check.set_active(self.config.get("features.screen_capture", True))
        features_box.pack_start(screen_capture_check, False, False, 0)

        # OCR
        ocr_check = Gtk.CheckButton(label=_("OCR (text recognition)"))
        ocr_check.set_active(self.config.get("features.ocr_enabled", True))
        features_box.pack_start(ocr_check, False, False, 0)

        # Expert mode button visibility (the active/inactive mode is toggled
        # from the window button or the tray menu and stored in app.expert_mode)
        expert_check = Gtk.CheckButton(label=_("Show Expert Mode button"))
        expert_check.set_active(self.config.get("features.expert_mode", True))
        features_box.pack_start(expert_check, False, False, 0)

        notebook.append_page(features_box, Gtk.Label(label=_("Features")))

        # Show dialog
        dialog.show_all()

        # Save settings on close
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            # Save provider (get_active_id() is None when nothing matches)
            new_provider = provider_combo.get_active_id()
            if new_provider:
                self.config.set("api.default_provider", new_provider)

                # Store the key under the provider the entry was loaded for
                # (updated by on_provider_changed), never under a provider the
                # key does not belong to.
                api_key = api_key_entry.get_text()
                self.config.set_api_key(loaded_provider[0], api_key)

            # Save opacity
            opacity = opacity_scale.get_value()
            self.config.set("app.opacity", opacity)
            self.set_opacity(opacity)

            # Save always on top
            always_on_top = always_on_top_check.get_active()
            self.config.set("app.always_on_top", always_on_top)
            self.set_keep_above(always_on_top)

            # Save theme
            theme = theme_combo.get_active_id()
            if theme:
                self.config.set("app.theme", theme)
                # Rebuild the CSS so the new theme takes effect now
                self._setup_style()

            # Save features
            self.config.set("features.screen_capture", screen_capture_check.get_active())
            self.config.set("features.ocr_enabled", ocr_check.get_active())
            self.config.set("features.expert_mode", expert_check.get_active())
            if not expert_check.get_active() and self.expert_mode:
                # Hiding the button must not leave an invisible active mode.
                self.expert_mode = False
                self.config.set("app.expert_mode", False)
                self.expert_btn.get_style_context().remove_class("expert")
                tray = getattr(self.app, "tray_icon", None)
                if tray is not None:
                    tray.update_expert_mode(False)
            self._apply_feature_toggles()

            # Save docked mode
            self.config.set("app.dock_mode", "dock" if dock_check.get_active() else "float")
            self.config.set("app.dock_edge", dock_edge_combo.get_active_id() or "right")
            # Apply the new dock/float setting immediately
            self._reapply_dock()

            # Save settings
            self.config.save()

            # Show notification
            self.show_notification("Linux AI Assistant", _("Settings saved successfully"))

        dialog.destroy()
        self._settings_notebook = None
        # O diálogo foi destruído: não deixar o listbox pendente a um widget
        # morto (qualquer uso futuro operaria sobre o objeto destruído).
        self.themes_listbox = None

    def toggle_visibility(self):
        """Alterna a visibilidade da janela e sincroniza o estado visível.

        Ponto ÚNICO do toggle (tray, botão flutuante): sem isto, o label da
        tray dessincronizava (update_toggle_label nunca era chamado pelos
        outros caminhos).
        """
        if self.get_visible():
            self.hide()
        else:
            self.show()
            self.present()
        self.sync_visibility()
        logger.debug("Window visibility toggled: visible=%s", self.get_visible())

    def sync_visibility(self):
        """Repõe o label do menu da tray em coerência com a janela."""
        tray = getattr(self.app, "tray_icon", None)
        if tray is not None:
            tray.update_toggle_label(self.get_visible())

    # ---- Delegações ao ChatView (buffer/tags/offsets/placeholder) ----
    # A mecânica de inserção vive em src/chat_view.py; aqui ficam apenas as
    # DECISÕES (o que mostrar, quando persistir).

    def _append_message(self, label: str, message: str, tag_name: str):
        return self.chat_view.append_message(label, message, tag_name)

    def _apply_code_tags(self, buffer, body_start: int, message: str):
        # `buffer` mantido na assinatura por compatibilidade interna
        self.chat_view.apply_code_tags(body_start, message)

    def _add_user_message(self, message: str):
        """Add a user message to the chat (apenas UI; persistência via _remember)"""
        if not message:
            return

        self.chat_view.append_message(_('User'), message, "user-message")
        self.chat_view.scroll_to_bottom()
        logger.debug(f"User message added: {message[:50]}...")

    def _add_ai_message(self, message: str, streaming: bool = False):
        """Add an AI message to the chat"""
        if self._cancel_event.is_set():
            return

        if streaming and self.streaming:
            # Replace the placeholder with the real header on the first chunk,
            # so the streaming response is formatted correctly.
            if self.chat_view.has_loading():
                self.chat_view.clear_loading()
                _start, _end, body_start = self.chat_view.append_stream_header()
                # Remembered so the finished response can get its code tags
                self.chat_view.body_start = body_start
            self.chat_view.insert_stream_chunk(message)
            self.chat_view.scroll_to_bottom()
        else:
            self.chat_view.body_start = None
            self.chat_view.append_message(_('AI'), message, "ai-message")
            self.chat_view.scroll_to_bottom()

        logger.debug(f"AI message added: {message[:50]}...")

    def _add_system_message(self, message: str):
        """Add a system message to the chat"""
        self.chat_view.append_message(_('System'), message, "system-message")
        self.chat_view.scroll_to_bottom()
        logger.debug(f"System message: {message}")

    def _add_loading_message(self, request_id=None, cancel_event=None):
        """Add a loading message (delegado ao ChatView, com guarda de pedido:
        um idle de um worker cancelado não pode inserir um "Thinking..."
        que ninguém remove — placeholder fantasma)."""
        self.chat_view.show_loading(
            request_id=request_id,
            is_active=lambda rid: rid == self._active_request,
            cancelled=(cancel_event.is_set if cancel_event is not None else None),
        )

    def _remove_loading_message(self):
        """Remove ONLY the loading placeholder."""
        self.chat_view.clear_loading()

    def _scroll_to_bottom(self):
        self.chat_view.scroll_to_bottom()

    def _load_conversation_history(self):
        """Load conversation history (via HistoryStore, já normalizado)."""
        self.conversation_history = self.history_store.load_messages()
        logger.info(f"History loaded with {len(self.conversation_history)} messages")

    def _build_request_messages(self):
        """Build the message list for the API.

        It removes `timestamp` (which only exists in the on-disk history) and
        limits the context to the configured budget, so a long conversation
        does not send 1000 messages on every turn.
        """
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in self.conversation_history
                    if m.get("role") and m.get("content")]

        max_messages = safe_number(
            self.config.get("context.max_messages"), MAX_CONTEXT_MESSAGES,
            int, minimum=2, maximum=500,
        )
        max_chars = safe_number(
            self.config.get("context.max_chars"), MAX_CONTEXT_CHARS,
            int, minimum=1000, maximum=400000,
        )

        if len(messages) > max_messages:
            logger.debug(
                f"Context truncated from {len(messages)} to {max_messages} messages"
            )
            messages = messages[-max_messages:]

        total = sum(len(m["content"]) for m in messages)
        # Floor of 1 (not 2): with exactly two messages over budget the old
        # loop never popped, so the character limit was silently ignored.
        while len(messages) > 1 and total > max_chars:
            removed = messages.pop(0)
            total -= len(removed["content"])
            logger.debug("Context truncated by character budget")

        return messages

    def _save_message_to_history(self, role: str, content: str):
        """Enfileira a mensagem no HistoryStore (writer background)."""
        self.history_store.append(role, content)

    def _remember(self, role: str, content: str):
        """Registar uma mensagem no contexto em memória E no history.json.

        Ponto ÚNICO de persistência de mensagens (o duplo save offline
        vinha de dois caminhos a gravar o mesmo turno). O contexto em
        memória tem o mesmo teto de 1000 mensagens do writer, para sessões
        longas não reterem memória ilimitada.
        """
        self.conversation_history.append({"role": role, "content": content})
        if len(self.conversation_history) > MAX_HISTORY_MESSAGES:
            del self.conversation_history[:-MAX_HISTORY_MESSAGES]
        self._save_message_to_history(role, content)

    def _get_context_message(self) -> Optional[Dict[str, str]]:
        """Get context message based on the current mode"""
        if self.expert_mode:
            return {
                "role": "system",
                "content": """You are the Linux systems expert with extensive knowledge of:
- Configuration of systems and services
- Package management (apt, dnf, pacman, xbps, etc.)
- Network and firewall configuration
- Scripting in Bash and Python
- Troubleshooting common problems
- Performance optimization
- System security

You help the user solve problems, explain concepts and make changes to configuration files.
Be precise and provide specific commands the user can run.
If editing configuration files is needed, ask for explicit authorization before doing so.
Respond in English.

System information:
""" + self._get_system_info_for_context()
            }
        else:
            return {
                "role": "system",
                "content": """You are a helpful AI assistant that answers questions about the Linux system and general topics.
You can help with questions, explanations and suggestions.
Respond clearly and concisely in English."""
            }

    def _get_system_info_for_context(self) -> str:
        """Get system information for context."""
        try:
            info = self.system_utils.get_system_info()
            lines = [
                f"System: {info.get('distro', 'Unknown')}",
                f"Kernel: {info.get('release', 'Unknown')}",
                f"Architecture: {info.get('machine', 'Unknown')}",
                f"Memory: {info.get('memory_used', 'N/A')} used of {info.get('memory_total', 'N/A')}",
                f"CPU: {info.get('cpu_cores', 'N/A')} cores",
            ]
            return "\n".join(lines) + "\n"
        except Exception as e:
            logger.warning(f"Error getting system info for context: {e}")
            return ""

    def on_input_activate(self, entry):
        """Handler for Enter no input"""
        self.on_send_clicked()

    def on_send_clicked(self):
        """Handler for send button click"""
        if self.is_loading:
            logger.warning("A message is already being processed")
            return

        text = self.input_entry.get_text().strip()
        if not text:
            return

        self.input_entry.set_text("")

        # Add the user message to the chat...
        self._add_user_message(text)

        # ...and to the context + history.json (ponto único de persistência)
        self._remember("user", text)

        # Snapshot do pedido construído NA main thread: a worker deixou de
        # ler conversation_history/expert_mode/config sem sincronização.
        context = self._get_context_message()
        messages = self._build_request_messages()
        full_history = ([context] + messages) if context else messages

        # Per-request state: fresh id + cancel event. A worker that is
        # still draining after a cancel can no longer touch this request.
        self._request_seq += 1
        request_id = self._request_seq
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._active_request = request_id
        self.chat_view.body_start = None

        # Update state
        self.is_loading = True
        self.streaming = True
        self.cancel_btn.set_sensitive(True)
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Processing..."))

        # Process in a separate thread so the UI is not blocked
        threading.Thread(
            target=self._process_message,
            args=(text, full_history, request_id, cancel_event),
            daemon=True,
        ).start()

    def _process_message(self, message: str, full_history: list,
                         request_id: int, cancel_event: threading.Event):
        """Process the message and get the AI response.

        All UI/history mutations are queued to the main loop with
        `request_id`, so output from a superseded or cancelled request is
        dropped instead of corrupting the next turn.
        """
        GLib.idle_add(self._add_loading_message, request_id, cancel_event)
        response_text = ""
        # Coalescing de chunks: acumula no worker e faz flush à UI no
        # máximo ~12x/segundo (STREAM_FLUSH_INTERVAL), em vez de um idle
        # por chunk (milhares por resposta rápida).
        pending_chunks = []
        last_flush = 0.0

        try:
            offline_reply = None

            if not self.ai_client.provider_ready():
                # No key for the selected provider: answer from local knowledge
                # instead of failing with "API key not configured".
                logger.info("No usable provider; using the offline assistant")
                offline_reply = self.offline.handle(message, get_language())
            else:
                try:
                    for chunk in self.ai_client.stream_chat(full_history):
                        if cancel_event.is_set():
                            break

                        response_text += chunk
                        pending_chunks.append(chunk)
                        now = time.monotonic()
                        if now - last_flush >= STREAM_FLUSH_INTERVAL:
                            GLib.idle_add(self._update_ai_message, request_id,
                                          "".join(pending_chunks), True)
                            pending_chunks = []
                            last_flush = now
                except AIProviderError as e:
                    # Falha ESTRUTURADA do provider (rede/HTTP/config) — não
                    # é conteúdo do modelo. O fallback offline dispara tanto
                    # para falha antes do primeiro chunk como a meio do
                    # stream (o antigo _is_api_failure por prefixo perdia
                    # os erros a meio e disparava o fallback para respostas
                    # legítimas que começassem por "Error:").
                    logger.info(
                        "Provider unavailable (%s); using the offline assistant", e
                    )
                    if not cancel_event.is_set():
                        response_text = ""
                        offline_reply = self.offline.handle(message, get_language())

                if pending_chunks:
                    GLib.idle_add(self._update_ai_message, request_id,
                                  "".join(pending_chunks), True)
                    pending_chunks = []

            if offline_reply is not None:
                GLib.idle_add(self._add_system_message_if_active, request_id,
                              cancel_event,
                              _("Offline mode: answering from local knowledge."))
                response_text = offline_reply.text
                GLib.idle_add(self._update_ai_message, request_id,
                              response_text, False)
                if offline_reply.commands and not cancel_event.is_set():
                    GLib.idle_add(self._offer_offline_commands,
                                  offline_reply.commands, request_id,
                                  cancel_event)

            cancelled = cancel_event.is_set()
            GLib.idle_add(self._finalize_response, request_id, response_text, cancelled)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            GLib.idle_add(self._add_system_message_if_active, request_id,
                          cancel_event, _("Error: {error}").format(error=e))
            # Finalize with whatever was streamed so far: it is already on
            # screen, and the history must match what the user sees.
            GLib.idle_add(self._finalize_response, request_id, response_text, False)

    def _add_system_message_if_active(self, request_id, cancel_event, text):
        """_add_system_message com guarda de pedido (para callbacks idle).

        Sem a guarda, o aviso/erro de um pedido antigo podia aparecer a meio
        de uma conversa nova; os restantes outputs do pipeline já tinham
        guarda, estes não.
        """
        if request_id != self._active_request or cancel_event.is_set():
            return False
        self._add_system_message(text)
        return False

    def _offer_offline_commands(self, commands, request_id, cancel_event):
        """Ask for confirmation before running privileged offline commands."""
        if not commands:
            return False
        # A newer request may already be active by the time this idle
        # callback runs: confirming a superseded turn would run commands
        # the user no longer expects.
        if request_id != self._active_request or cancel_event.is_set():
            return False

        dialog = Gtk.Dialog(
            title=_("Run suggested commands?"),
            transient_for=self,
            modal=True,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Run"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        content = dialog.get_content_area()
        header = Gtk.Label(
            label=_("These changes need administrator rights (pkexec):")
        )
        header.set_halign(Gtk.Align.START)
        header.set_line_wrap(True)
        content.pack_start(header, False, False, 6)
        for command in commands:
            row = Gtk.Label(label=f"$ {command.display()}")
            row.set_halign(Gtk.Align.START)
            row.set_selectable(True)
            row.set_monospace(True)
            content.pack_start(row, False, False, 2)

        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return False

        threading.Thread(
            target=self._run_offline_commands,
            args=(commands, request_id, cancel_event),
            daemon=True,
        ).start()
        return False

    def _run_offline_commands(self, commands, request_id, cancel_event):
        """Run the confirmed commands via pkexec (worker thread)."""
        for command in commands:
            # Stop as soon as the request is cancelled or superseded:
            # pkexec prompts must not pop up for a stale turn.
            if cancel_event.is_set() or request_id != self._active_request:
                logger.info("Skipping offline command: request was cancelled")
                return False
            ok, output = offline_assistant.OfflineAssistant.run_privileged(
                command
            )
            detail = output or (_("Done.") if ok else _("Failed."))
            result = f"$ {command.display()}\n{detail}"
            GLib.idle_add(self._record_offline_result, request_id, result)
        return False

    def _record_offline_result(self, request_id, result):
        """Show a command result and keep it in the conversation context.

        Without the history append, follow-up questions had no idea what
        was executed (the text only reached the chat buffer).
        """
        self._add_system_message(result)
        if request_id == self._active_request:
            self._remember("assistant", result)
        return False

    def _finalize_response(self, request_id: int, response_text: str,
                           cancelled: bool):
        """Persist the response and settle the UI (main loop only)."""
        if request_id != self._active_request:
            # A newer request replaced this one; its output is stale.
            logger.debug("Dropping result from stale request %s", request_id)
            return False

        self.streaming = False

        if response_text and not cancelled:
            # The streamed body was inserted chunk by chunk; now that it is
            # complete, tag its code spans and close with the "\n\n"
            # separator (tudo dentro do ChatView, dono dos offsets).
            self.chat_view.close_streamed_message(response_text)

            # Ponto único de persistência do turno (memória + history.json)
            self._remember("assistant", response_text)

            # Offer file writes in expert mode. We are already on the main
            # loop, so the dialogs can be created directly (GTK is not
            # thread-safe).
            if self.expert_mode:
                file_actions.offer_file_blocks(
                    self, response_text,
                    lambda msg: GLib.idle_add(self._add_system_message, msg),
                    self.config.get("permissions.allowed_edit_dirs", []),
                )

        self._on_message_processed(request_id, cancelled)
        return False

    def _update_ai_message(self, request_id: int, chunk: str, streaming: bool):
        """Update the AI message, ignoring output from stale requests."""
        if request_id != self._active_request or self._cancel_event.is_set():
            # Pedido antigo ou cancelado: chunks em fila de idle não podem
            # ser despejados no chat como uma mensagem nova avulsa.
            logger.debug("Dropping chunk from stale/cancelled request %s", request_id)
            return False
        self._add_ai_message(chunk, streaming)
        return False

    def _on_message_processed(self, request_id=None, cancelled=False):
        """Callback when the message is processed."""
        if request_id is not None and request_id != self._active_request:
            return False

        self.is_loading = False
        self.cancel_btn.set_sensitive(False)

        # Remove the loading message (no-op if already replaced by the response)
        self._remove_loading_message()

        if cancelled:
            # on_cancel_streaming already painted the cancelled state.
            return False

        self.status_icon.set_from_icon_name("emblem-ok", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Ready"))

        # Show a notification if the window is not active. `is-active` is a
        # Gtk.Window property (GdkWindow does not have it).
        if not self.get_property("is-active"):
            self.show_notification("Linux AI Assistant", _("New response received"))
        return False

    def on_cancel_streaming(self, button):
        """Cancel the current streaming."""
        self._cancel_event.set()
        self.is_loading = False
        self.streaming = False
        self.cancel_btn.set_sensitive(False)
        self.status_icon.set_from_icon_name("dialog-error", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Cancelled"))

        self._add_system_message(_("Streaming cancelled"))
        self._remove_loading_message()
        logger.info("Streaming cancelled by the user")

    def on_capture_screen_clicked(self, button):
        """Handler for screen capture"""
        # The button/shortcut stays available even if the feature was
        # disabled after startup, so enforce the setting here too.
        if not self.config.get("features.screen_capture", True):
            self._add_system_message(_("Screen capture is disabled in settings."))
            return
        if self.is_loading:
            self.show_notification("Linux AI Assistant", _("Wait for the current message to be processed"))
            return

        self._add_system_message(_("Capturing screen..."))
        self.is_loading = True
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)

        def capture_and_process():
            try:
                # Capture screen
                success, image_path = self.system_utils.capture_screen()

                if success:
                    GLib.idle_add(self._add_system_message,
                                  _("Screen captured: {path}").format(path=image_path))

                    # Extract text
                    if self.config.get("features.ocr_enabled", True):
                        GLib.idle_add(self._add_system_message,
                                      _("Extracting text from the image..."))
                        success, text = self.system_utils.extract_text_from_image(image_path)

                        if success and text:
                            # Limit the text so it does not overload the UI
                            max_length = 2000
                            if len(text) > max_length:
                                text = text[:max_length] + "\n\n... " + _("(text truncated)")

                            GLib.idle_add(self._add_user_message,
                                          f"[{_('Screen capture')}]\n{text}")
                            GLib.idle_add(self._remember, "user",
                                          f"[{_('Screen capture')}]\n{text}")
                        else:
                            GLib.idle_add(self._add_system_message,
                                          _("Could not extract text from the image."))
                    else:
                        GLib.idle_add(self._add_system_message,
                                      _("OCR disabled in settings."))

                    # Remove temporary image
                    try:
                        os.unlink(image_path)
                    except OSError:
                        pass
                else:
                    GLib.idle_add(self._add_system_message,
                                  _("Error capturing screen: {detail}").format(detail=image_path))

            except Exception as e:
                logger.error(f"Error in screen capture: {e}", exc_info=True)
                GLib.idle_add(self._add_system_message,
                              _("Error: {error}").format(error=e))
            finally:
                GLib.idle_add(self._on_capture_complete)

        threading.Thread(target=capture_and_process, daemon=True).start()

    def _on_capture_complete(self):
        """Callback when the capture is completed."""
        self.is_loading = False
        self.status_icon.set_from_icon_name("emblem-ok", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Ready"))
        return False

    def on_expert_mode_toggled(self, button):
        """Toggle expert mode."""
        self.expert_mode = not self.expert_mode
        # Persist: the mode used to reset to normal on every restart.
        self.config.set("app.expert_mode", self.expert_mode)

        if self.expert_mode:
            self.expert_btn.get_style_context().add_class("expert")
            self._add_system_message(_("Expert Mode ENABLED - Helping with system configuration"))
            self.show_notification("Linux AI Assistant", _("Expert Mode enabled"))
        else:
            self.expert_btn.get_style_context().remove_class("expert")
            self._add_system_message(_("Expert Mode DISABLED"))
            self.show_notification("Linux AI Assistant", _("Expert Mode disabled"))

        # Clear the history for the new context
        self.conversation_history = []
        # Keep the tray menu checkbox in sync (it may have been the source,
        # or the window button may have been)
        tray = getattr(self.app, "tray_icon", None)
        if tray is not None:
            tray.update_expert_mode(self.expert_mode)
        logger.info(f"Expert mode {'enabled' if self.expert_mode else 'disabled'}")

    def on_close_clicked(self):
        """Handler for closing the window."""
        self.on_delete_event(None, None)

    def on_delete_event(self, widget, event):
        """Handler for closing the window."""
        # Save the window geometry. `GdkWindow.get_geometry()` returns the
        # client-side rect but its position is relative to the parent, and
        # `get_position()` on the GdkWindow is not reliable before the window
        # is mapped; Gtk.Window's accessors return absolute coordinates.
        try:
            x, y = self.get_position()
            width, height = self.get_size()
            self.config.set_window_geometry(width, height, x, y)
        except Exception as e:
            logger.warning(f"Could not save window geometry: {e}")
        # Persistir qualquer alteracao pendente antes de sair
        self.config.flush()
        # Drain the history writer so the last messages reach history.json
        self.close_history_writer()

        # Close application (quit() destroys windows and stops the main loop,
        # so it also covers quitting from the tray menu).
        logger.info("Janela fechada")
        self.app.quit()
        return True

    def on_configure_event(self, widget, event):
        """Handler for redimensionar/mover janela

        `set()` ignora valores repetidos e e debounced, por isso mover a
        janela nao provoca uma escrita de config.json por pixel.
        """
        self.config.set("app.x_position", event.x)
        self.config.set("app.y_position", event.y)
        self.config.set("app.width", event.width)
        self.config.set("app.height", event.height)

        return True

        return True
