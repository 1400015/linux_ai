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

from . import device_dialogs, dock, file_actions, offline_assistant
from .device_actions import split_offer
from .ai_client import AIClient, AIProviderError, AIRequestCancelled, AIResponseLimitError, AIImageRequestError
from .chat_view import ChatView
from .history_store import HistoryStore, MAX_HISTORY_MESSAGES, MAX_MESSAGE_CHARS
from .i18n import _, get_language
from .assistant_context import build_system_message
from .conversation_dialog import show_conversations
from .conversation_actions import ConversationActions
from .change_journal import ChangeJournal
from .change_dialog import show_file_changes
from .diagnostic_dialog import show_diagnostic_report
from .trial_dialog import show_trials
from .provider_settings import ProviderSettings, MODE_LABELS, STATUS_LABELS
from .credential_settings import APIKeySettings
from .remote_model_settings import RemoteModelSettings
from .document_context import MAX_DOCUMENT_CONTEXT_CHARS, display_document_context, with_document_context
from .document_store import DocumentStore
from .document_review import review_document_context
from .image_dialog import pick_image, review_image
from .image_attachments import prepare_image

# Set up logger
logger = logging.getLogger(__name__)

# Values coming from theme files are interpolated into GTK's CSS; validating
# the format prevents a malicious JSON from injecting arbitrary style rules.
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 _.,'-]{1,64}$")

# Context budget: by default 12000 characters (~3k tokens) and 20 messages.
MAX_CONTEXT_CHARS = 12000
MAX_CONTEXT_MESSAGES = 20

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

    def __init__(self, app, config_manager, ai_client, system_utils, history_store=None):
        super().__init__(title="Linux AI Assistant")

        self.app = app
        self.config = config_manager
        self.ai_client = ai_client
        self.system_utils = system_utils
        # Answers basic questions and offers local tasks when no API key is
        # configured or the provider cannot be reached.
        self.offline = offline_assistant.OfflineAssistant(system_utils, config_manager)
        self.change_journal = ChangeJournal()

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
        self._pending_image = None
        self._pending_image_destination = None
        self._document_store = None
        # Settings dialog currently open (or None); lets the "Manage Themes"
        # button switch tabs instead of opening a second dialog.
        self._settings_notebook = None
        # Interval (start, end) of the "Thinking..." placeholder and the
        # streamed body offset vivem no ChatView (ver _create_ui).
        # History is persisted from a single background writer (FIFO) owned
        # by HistoryStore (GTK-free), so the GTK main loop never blocks on a
        # full file rewrite per message.
        self.history_store = history_store if history_store is not None else HistoryStore()
        self.history_store.list_sessions()
        self._history_recovering = False
        self._file_action_controllers = []
        from .system_context import detect_system_context
        self.system_context = detect_system_context(self.offline.distro)
        self.offline.set_system_context(self.system_context)
        self.actions = ConversationActions(self.history_store, self.offline.distro.pkg_manager, context=self.system_context,
                                           independent_display_watchdog=self.config.get('app.display_independent_watchdog', False))
        self._refreshing_sessions = False

        # Create interface
        self._create_ui()
        self._setup_style()

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
        self._refresh_session_controls()
        self._refresh_mode_status()
        self._history_watch_source = GLib.timeout_add_seconds(1, self._check_history_writer)
        self.connect('destroy', self._stop_history_watch)

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
            self.capture_btn.set_no_show_all(True)
            self.capture_btn.set_visible(self.config.get("features.screen_capture", True))
        if hasattr(self, "expert_btn"):
            self.expert_btn.set_no_show_all(True)
            self.expert_btn.set_visible(self.config.get("features.expert_mode", True))

    def close_history_writer(self, timeout=None):
        """Flush pending history writes and stop the writer thread."""
        event = getattr(self, '_cancel_event', None)
        self._audit_closed = True
        if getattr(self, '_history_watch_source', None) is not None:
            self._stop_history_watch()
        for controller in getattr(self, '_file_action_controllers', ()):
            controller.cancel()
        if event is not None:
            event.set()
        actions = getattr(self, 'actions', None)
        if actions is not None:
            actions.close()
        document_store = getattr(self, '_document_store', None)
        if document_store is not None:
            document_store.close()
        if not self.history_store.close(timeout):
            logger.error("History writer did not finish successfully: %s", self.history_store.last_error)

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
        if hasattr(self, 'chat_view'):
            self.chat_view.set_style(font_family, font_size, {
                'user': user_msg_color, 'ai': ai_msg_color, 'system': system_msg_color,
                'code': safe_color(syntax_colors.get('code'), text_color),
                'code_background': secondary_color,
            })

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

        conversations = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        main_box.pack_start(conversations, False, False, 0)
        self.session_combo = Gtk.ComboBoxText()
        self.session_combo.set_hexpand(True)
        self.session_combo.set_tooltip_text(_("Conversations"))
        self.session_combo.connect('changed', self._on_session_selected)
        conversations.pack_start(self.session_combo, True, True, 0)
        new_conversation = Gtk.Button.new_from_icon_name('document-new', Gtk.IconSize.MENU)
        new_conversation.set_tooltip_text(_("New conversation"))
        new_conversation.connect('clicked', self._new_conversation)
        conversations.pack_start(new_conversation, False, False, 0)
        actions_button = Gtk.Button.new_from_icon_name('view-list-symbolic', Gtk.IconSize.MENU)
        actions_button.set_tooltip_text(_("Actions"))
        actions_button.connect('clicked', self._show_action_audit)
        conversations.pack_start(actions_button, False, False, 0)
        self.trials_button = Gtk.Button.new_from_icon_name('applications-science-symbolic', Gtk.IconSize.MENU)
        self.trials_button.set_tooltip_text(_("Trials / Debug"))
        self.trials_button.connect('clicked', lambda button: show_trials(self))
        conversations.pack_start(self.trials_button, False, False, 0)
        self.mode_label = Gtk.Label(xalign=0, wrap=True)
        main_box.pack_start(self.mode_label, False, False, 0)

        self.history_warning = Gtk.InfoBar()
        self.history_warning.set_message_type(Gtk.MessageType.WARNING)
        self.history_warning.set_no_show_all(True)
        warning = Gtk.Label(label=_("Conversation history could not be saved. Recover it before continuing; pending messages have not been discarded."),
                            xalign=0, wrap=True)
        self.history_warning.get_content_area().pack_start(warning, True, True, 0)
        warning.show()
        self.history_warning.add_button(_("Recover history"), Gtk.ResponseType.OK)
        self.history_warning.connect('response', self._recover_history)
        main_box.pack_start(self.history_warning, False, False, 0)

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

        references = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        main_box.pack_start(references, False, False, 0)
        self.documents_check = Gtk.CheckButton(label=_("Use selected documents"))
        self.documents_check.set_tooltip_text(_("Review matching excerpts before sending them to the model. Offline mode shows local matches."))
        references.pack_start(self.documents_check, True, True, 0)
        documents_button = Gtk.Button.new_from_icon_name('folder-documents-symbolic', Gtk.IconSize.MENU)
        documents_button.set_tooltip_text(_("Manage selected documents"))
        documents_button.connect('clicked', self.on_documents_clicked)
        references.pack_start(documents_button, False, False, 0)
        self.attachment_label = Gtk.Label(xalign=0, wrap=True)
        main_box.pack_start(self.attachment_label, False, False, 0)
        self.remove_image_button = Gtk.Button(label=_("Remove image"))
        self.remove_image_button.connect('clicked', lambda button: self._clear_pending_image())
        self.remove_image_button.set_no_show_all(True)
        self.remove_image_button.hide()
        references.pack_start(self.remove_image_button, False, False, 0)

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

        image_btn = Gtk.Button.new_from_icon_name('insert-image-symbolic', Gtk.IconSize.MENU)
        image_btn.connect('clicked', self.on_attach_image_clicked)
        image_btn.set_tooltip_text(_("Attach an image for the next question"))
        button_box.pack_start(image_btn, False, False, 0)

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

        recover_item = Gtk.MenuItem(label=_("Recover history"))
        recover_item.connect('activate', self._recover_history)
        menu.append(recover_item)

        # Statistics option
        stats_item = Gtk.MenuItem(label=_("Statistics"))
        stats_item.connect("activate", self.on_stats_clicked)
        menu.append(stats_item)

        report_item = Gtk.MenuItem(label=_("Diagnostic report"))
        report_item.connect('activate', lambda item: show_diagnostic_report(self))
        menu.append(report_item)
        trials_item = Gtk.MenuItem(label=_("Trials / Debug"))
        trials_item.connect('activate', lambda item: show_trials(self))
        menu.append(trials_item)
        changes_item = Gtk.MenuItem(label=_("File changes"))
        changes_item.connect('activate', lambda item: show_file_changes(self))
        menu.append(changes_item)
        image_item = Gtk.MenuItem(label=_("Capture an image for AI"))
        image_item.connect('activate', lambda item: self.on_capture_screen_clicked(None, for_ai=True))
        menu.append(image_item)
        documents_item = Gtk.MenuItem(label=_("Manage selected documents"))
        documents_item.connect('activate', self.on_documents_clicked)
        menu.append(documents_item)

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

    def _get_document_store(self):
        if self._document_store is None:
            self._document_store = DocumentStore()
        return self._document_store

    def on_documents_clicked(self, button=None):
        from .document_dialog import DocumentDialog
        try:
            dialog = DocumentDialog(self, self._get_document_store())
            try:
                dialog.run()
            finally:
                dialog.destroy()
        except (OSError, ValueError) as error:
            self._add_system_message(_("Could not open documents: {error}").format(error=error))

    def _clear_pending_image(self):
        self._pending_image = None
        self._pending_image_destination = None
        label = getattr(self, 'attachment_label', None)
        if label is not None:
            label.set_text('')
        button = getattr(self, 'remove_image_button', None)
        if button is not None:
            button.hide()

    def _review_and_attach_image(self, image):
        try:
            if not isinstance(self.ai_client, AIClient):
                raise AIImageRequestError(_("This client does not support reviewed images."))
            provider, model = self.ai_client.validate_image_request((image,))
            if review_image(self, image, provider, model):
                self._pending_image = image
                self._pending_image_destination = (provider, model)
                self.attachment_label.set_text(_("Image ready for the next question: {name} ({width} × {height})")
                                               .format(name=image.filename, width=image.width, height=image.height))
                self.remove_image_button.show()
        except (OSError, ValueError, AIProviderError) as error:
            self._add_system_message(_("Could not attach image: {error}").format(error=error))
        return False

    def on_attach_image_clicked(self, button=None):
        if self.is_loading:
            self._add_system_message(_("Wait for the current message to be processed"))
            return
        try:
            image = pick_image(self)
            if image is not None:
                self._review_and_attach_image(image)
        except (OSError, ValueError) as error:
            self._add_system_message(_("Could not attach image: {error}").format(error=error))

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
        show_conversations(self)

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

        credential_settings = APIKeySettings(self.config, provider_combo)
        api_box.pack_start(credential_settings, False, False, 0)

        notebook.append_page(api_box, Gtk.Label(label=_("API")))
        assistance_settings = ProviderSettings(self.config, self.ai_client)
        model_settings = RemoteModelSettings(self.config, self.ai_client, provider_combo, assistance_settings.mode)
        api_box.pack_start(model_settings, False, False, 0)
        notebook.append_page(assistance_settings, Gtk.Label(label=_("Assistance")))

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

        global_shortcut_check = Gtk.CheckButton(label=_("Enable global shortcut to open the assistant"))
        global_shortcut_check.set_active(self.config.get('app.global_shortcut_enabled', False))
        ui_box.pack_start(global_shortcut_check, False, False, 0)
        global_shortcut_entry = Gtk.Entry()
        global_shortcut_entry.set_max_length(100)
        global_shortcut_entry.set_text(self.config.get('app.global_shortcut', '<Ctrl><Alt>space'))
        global_shortcut_entry.set_placeholder_text('<Ctrl><Alt>space')
        ui_box.pack_start(global_shortcut_entry, False, False, 0)
        global_shortcut_status = Gtk.Label(label=_(getattr(self.app, 'global_shortcut_status', 'Global shortcut disabled')),
                                          wrap=True, xalign=0)
        ui_box.pack_start(global_shortcut_status, False, False, 0)
        previous_shortcut_callback = getattr(self.app, 'global_shortcut_status_changed', None)

        def update_shortcut_status(status):
            global_shortcut_status.set_text(_(status))
            if callable(previous_shortcut_callback):
                previous_shortcut_callback(status)

        self.app.global_shortcut_status_changed = update_shortcut_status
        dialog.connect('destroy', lambda widget: setattr(self.app, 'global_shortcut_status_changed', previous_shortcut_callback))

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

        # Language selector: qualquer língua não suportada recai em inglês
        # (i18n.normalize_language); aqui o utilizador redefine explicitamente.
        lang_label = Gtk.Label(label=_("Language:"))
        ui_box.pack_start(lang_label, False, False, 0)

        from .i18n import available_languages, LANGUAGE_NAMES
        lang_combo = Gtk.ComboBoxText()
        for code in available_languages():
            lang_combo.append(code, LANGUAGE_NAMES.get(code, code))
        lang_combo.set_active_id(get_language() if get_language() in
                                 available_languages() else "en")
        ui_box.pack_start(lang_combo, False, False, 0)

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

        # Validate the draft before saving; Cancel never persists probe settings.
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                break
            try:
                if (assistance_settings.mode.get_active_id() != self.config.get_assistance_mode()
                        or assistance_settings.draft() != {
                            key: self.config.get_local_model_settings()[key]
                            for key in assistance_settings.draft()
                        }):
                    self._cancel_for_session_change()
                assistance_settings.save()
                model_settings.save()
                if not credential_settings.save():
                    continue
                break
            except ValueError as error:
                warning = Gtk.MessageDialog(transient_for=dialog, modal=True,
                                            message_type=Gtk.MessageType.ERROR,
                                            buttons=Gtk.ButtonsType.OK, text=str(error))
                warning.run()
                warning.destroy()
        if response == Gtk.ResponseType.OK:
            # Save provider (get_active_id() is None when nothing matches)
            new_provider = provider_combo.get_active_id()
            if new_provider:
                self.config.set("api.default_provider", new_provider)

            # Save opacity
            opacity = opacity_scale.get_value()
            self.config.set("app.opacity", opacity)
            self.set_opacity(opacity)

            # Save always on top
            always_on_top = always_on_top_check.get_active()
            self.config.set("app.always_on_top", always_on_top)
            self.set_keep_above(always_on_top)
            shortcut = global_shortcut_entry.get_text().strip()
            self.config.set('app.global_shortcut_enabled', global_shortcut_check.get_active())
            self.config.set('app.global_shortcut', shortcut)
            configure_shortcut = getattr(self.app, 'configure_global_shortcut', None)
            if configure_shortcut is not None:
                status = configure_shortcut(global_shortcut_check.get_active(), shortcut)
                self._add_system_message(_(status))

            # Save theme
            theme = theme_combo.get_active_id()
            if theme:
                self.config.set("app.theme", theme)
                # Rebuild the CSS so the new theme takes effect now
                self._setup_style()

            # Save language (normalizada: não suportada -> inglês). Os textos
            # novos (mensagens, diálogos) aplicam-se de imediato; os rótulos
            # já desenhados aplicam-se após reiniciar.
            from .i18n import set_language as set_app_language
            new_lang = lang_combo.get_active_id()
            if new_lang:
                set_app_language(new_lang, self.config)

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
            self._refresh_mode_status()

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
            self.show_all()
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
        self.offline.restore_diagnostic(self.history_store.get_diagnostic_state())
        self.conversation_history = self.history_store.load_messages()
        self.chat_view.abort_stream()
        self.chat_view.buffer.set_text('')
        if len(self.conversation_history) > 100:
            self._add_system_message(_("Showing the latest 100 messages. Export to view the full conversation."))
        for message in self.conversation_history[-100:]:
            if message['role'] == 'user':
                self._add_user_message(message['content'])
            else:
                self._add_ai_message(message['content'])

    def _refresh_session_controls(self):
        self._refreshing_sessions = True
        try:
            self.session_combo.remove_all()
            for session in self.history_store.list_sessions():
                self.session_combo.append(session['id'], session['title'])
            self.session_combo.set_active_id(self.history_store.active_session_id)
        except (OSError, ValueError) as error:
            self._show_history_warning(error)
        finally:
            self._refreshing_sessions = False

    def _show_history_warning(self, error=None):
        if error is not None:
            logger.error("Conversation history needs attention: %s", error)
        warning = getattr(self, 'history_warning', None)
        if warning is not None:
            warning.show()

    def _check_history_writer(self):
        if getattr(self, '_audit_closed', False):
            return False
        if self.history_store.last_error is not None:
            self._show_history_warning()
        return True

    def _stop_history_watch(self, *args):
        source = getattr(self, '_history_watch_source', None)
        if source is not None:
            GLib.source_remove(source)
            self._history_watch_source = None

    def _recover_history(self, *args):
        if getattr(self, '_history_recovering', False):
            return
        if (any(controller.active for controller in getattr(self, '_file_action_controllers', ()))
                or getattr(self, '_file_recovery_active', 0)):
            self._add_system_message(_("Wait for the approved file operation to finish before recovering history."))
            return
        from .history_recovery import recover_history
        self._history_recovering = True
        try:
            # A reply already in flight must not append after the replacement
            # history has become a new conversation.
            self._cancel_for_session_change()
            result = recover_history(self, self.history_store, self.history_store.last_error)
            if getattr(getattr(self, 'app', None), '_shutdown_pending', False):
                return
            if result.status == 'recovered':
                self._renew_offline_assistant()
                self._load_conversation_history()
                self._refresh_session_controls()
                self.history_warning.hide()
                self._add_system_message(_("History recovered. Original backup: {path}").format(path=result.backup))
            elif result.status == 'failed':
                self._show_history_warning(result.error)
        except (OSError, ValueError, RuntimeError) as error:
            self._show_history_warning(error)
            self._add_system_message(_("Could not recover history: {error}").format(error=error))
        finally:
            self._history_recovering = False

    def _refresh_mode_status(self):
        status = self.ai_client.provider_status()
        label = _(MODE_LABELS.get(status.mode, status.mode))
        detail = _(STATUS_LABELS.get(status.state, status.state))
        self.mode_label.set_text('{} — {}'.format(label, detail))
        self.status_icon.set_tooltip_text(detail)

    def _cancel_for_session_change(self):
        self._cancel_event.set()
        self._request_seq += 1
        self._active_request = self._request_seq
        # Workers retain the cancelled event they were given. A fresh event
        # lets the selected/recovered conversation render its stored replies.
        self._cancel_event = threading.Event()
        self.is_loading = self.streaming = False
        self.cancel_btn.set_sensitive(False)
        self.chat_view.abort_stream()
        self.chat_view.clear_loading()
        self._clear_pending_image()
        documents_check = getattr(self, 'documents_check', None)
        if documents_check is not None:
            documents_check.set_active(False)
        self.status_icon.set_from_icon_name('emblem-ok', Gtk.IconSize.MENU)
        self._renew_offline_assistant()
        self._refresh_mode_status()

    def _renew_offline_assistant(self):
        self.offline = offline_assistant.OfflineAssistant(self.system_utils, self.config)
        try:
            self.offline.restore_diagnostic(self.history_store.get_diagnostic_state())
        except (OSError, ValueError) as error:
            self._show_history_warning(error)

    def _switch_session(self, identifier):
        self._cancel_for_session_change()
        self.history_store.select_session(identifier)
        # A cancelled worker keeps its own assistant; it cannot alter the next session's diagnostic.
        self._renew_offline_assistant()
        self._load_conversation_history()
        self._refresh_session_controls()
        self._refresh_mode_status()

    def _on_session_selected(self, combo):
        identifier = combo.get_active_id()
        if (not self._refreshing_sessions and identifier
                and identifier != self.history_store.active_session_id):
            try:
                self._switch_session(identifier)
            except (OSError, ValueError, KeyError) as error:
                self._add_system_message(str(error))
                self._refresh_session_controls()

    def _new_conversation(self, button=None):
        try:
            session = self.history_store.create_session(_("New conversation"), select=False)
            self._switch_session(session['id'])
        except (OSError, ValueError) as error:
            self._add_system_message(str(error))

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
        # Validate/enqueue first. A rejected history entry must not leave an
        # apparently saved message in the context of the next request.
        self._save_message_to_history(role, content)
        self.conversation_history.append({"role": role, "content": content})
        if len(self.conversation_history) > MAX_HISTORY_MESSAGES:
            del self.conversation_history[:-MAX_HISTORY_MESSAGES]

    def _get_context_message(self) -> Optional[Dict[str, str]]:
        query = next((item['content'] for item in reversed(self.conversation_history)
                      if item['role'] == 'user'), '')
        return build_system_message(self.expert_mode, self.offline.distro, query, get_language(), getattr(self, 'system_context', None))

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
        if (getattr(self, '_history_recovering', False)
                or getattr(getattr(self, 'history_store', None), 'last_error', None) is not None):
            self._show_history_warning()
            return
        if self.is_loading:
            logger.warning("A message is already being processed")
            return

        image = getattr(self, '_pending_image', None)
        text = self.input_entry.get_text().strip()
        if not text and image is not None:
            text = _("Describe this image.")
        if not text:
            return

        if len(text) > MAX_MESSAGE_CHARS:
            self._add_system_message(_("Message exceeds the local history limit. Shorten it before sending."))
            return

        try:
            provider = self.ai_client.active_provider()
            route = {'provider': provider, 'ready': self.ai_client.provider_ready(provider)}
        except AIProviderError:
            route = {'provider': None, 'ready': False}
        images = ()
        document_context = ''
        try:
            if image is not None:
                provider, model = self.ai_client.validate_image_request((image,))
                if not route['ready']:
                    raise AIImageRequestError(_("Configure a usable OpenRouter provider before sending an image."))
                if ((provider, model) != getattr(self, '_pending_image_destination', None)
                        and not review_image(self, image, provider, model)):
                    return
                route.update(provider=provider, model=model)
                images = (image,)
            documents_check = getattr(self, 'documents_check', None)
            if documents_check is not None and documents_check.get_active():
                document_context = self._get_document_store().context(text, max_chars=MAX_DOCUMENT_CONTEXT_CHARS)
                if not document_context:
                    self._add_system_message(_("No matching passages in the selected documents. Add documents or refine your question."))
                    return
                if route['ready'] and not review_document_context(self, document_context, route['provider']):
                    return
        except (OSError, ValueError, AIProviderError) as error:
            self._add_system_message(str(error))
            return

        try:
            history_text = text
            if image is not None:
                history_text += '\n\n' + _("[Image attached for this request; pixels are not stored.]")
            self._remember("user", history_text)
        except (OSError, ValueError, RuntimeError) as error:
            self._add_system_message(_("Could not save the message: {error}").format(error=error))
            return

        self.input_entry.set_text("")
        self._clear_pending_image()

        # Add the user message to the chat...
        self._add_user_message(history_text)

        # ...and to the context + history.json (ponto único de persistência)

        # Snapshot do pedido construído NA main thread: a worker deixou de
        # ler conversation_history/expert_mode/config sem sincronização.
        context = self._get_context_message()
        messages = self._build_request_messages()
        full_history = ([context] + messages) if context else messages
        if document_context:
            full_history = with_document_context(full_history, document_context)

        # Per-request state: fresh id + cancel event. A worker that is
        # still draining after a cancel can no longer touch this request.
        self._request_seq += 1
        request_id = self._request_seq
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._active_request = request_id
        self.chat_view.abort_stream()

        # Update state
        self.is_loading = True
        self.streaming = True
        self.cancel_btn.set_sensitive(True)
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Processing..."))

        # Process in a separate thread so the UI is not blocked
        threading.Thread(
            target=self._process_message,
            args=(text, full_history, request_id, cancel_event, self.offline, route,
                  self.history_store.active_session_id, images, document_context),
            daemon=True,
        ).start()

    def _process_message(self, message: str, full_history: list,
                         request_id: int, cancel_event: threading.Event, offline=None, route=None,
                         session_id=None, images=(), document_context=''):
        """Process the message and get the AI response.

        All UI/history mutations are queued to the main loop with
        `request_id`, so output from a superseded or cancelled request is
        dropped instead of corrupting the next turn.
        """
        if cancel_event.is_set() or request_id != self._active_request:
            return
        offline = offline if offline is not None else self.offline
        GLib.idle_add(self._add_loading_message, request_id, cancel_event)
        response_text = ""
        # Coalescing de chunks: acumula no worker e faz flush à UI no
        # máximo ~12x/segundo (STREAM_FLUSH_INTERVAL), em vez de um idle
        # por chunk (milhares por resposta rápida).
        pending_chunks = []
        last_flush = 0.0

        try:
            actions = getattr(self, 'actions', None)
            if actions is not None and session_id is not None and not images and not document_context:
                reply = actions.handle(
                    message, session_id, get_language(),
                    is_current=lambda: request_id == self._active_request and not cancel_event.is_set(),
                )
                if reply is not None:
                    GLib.idle_add(self._complete_action_reply, request_id, cancel_event, reply, session_id)
                    return
            offline_reply = None

            ready = route['ready'] if route is not None else self.ai_client.provider_ready()
            if not ready and images:
                raise AIImageRequestError(_("The image request cannot be answered offline."))
            if not ready and document_context:
                GLib.idle_add(self._finish_local_document_reply, request_id, cancel_event,
                              _("Local document matches (no model request):") + '\n\n' + display_document_context(document_context))
                return
            if not ready:
                # No key for the selected provider: answer from local knowledge
                # instead of failing with "API key not configured".
                logger.info("No usable provider; using the offline assistant")
                offline_reply = offline.handle(message, get_language())
            else:
                try:
                    if cancel_event.is_set():
                        return
                    options = {'provider': route['provider']} if route is not None else {}
                    if images:
                        options['images'] = images
                        if route is not None:
                            options['model'] = route['model']
                    # Keep old third-party clients/test shims callable, while
                    # the built-in client propagates this request's event.
                    if isinstance(self.ai_client, AIClient):
                        options['cancel_event'] = cancel_event
                    stream = self.ai_client.stream_chat(full_history, **options)
                    try:
                        for chunk in stream:
                            if cancel_event.is_set():
                                break
                            if len(response_text) + len(chunk) > MAX_MESSAGE_CHARS:
                                raise AIResponseLimitError(
                                    _("AI response exceeds the local history limit. The incomplete response was not saved; ask for a shorter response.")
                                )
                            response_text += chunk
                            pending_chunks.append(chunk)
                            now = time.monotonic()
                            if now - last_flush >= STREAM_FLUSH_INTERVAL:
                                GLib.idle_add(self._update_ai_message, request_id,
                                              "".join(pending_chunks), True)
                                pending_chunks = []
                                last_flush = now
                    finally:
                        close = getattr(stream, 'close', None)
                        if callable(close):
                            close()
                except AIRequestCancelled:
                    GLib.idle_add(self._finalize_response, request_id, "", True)
                    return
                except (AIResponseLimitError, AIImageRequestError):
                    # A local limit is an incomplete answer, not a reason to
                    # silently replace it with an unrelated offline answer.
                    raise
                except AIProviderError as e:
                    if images:
                        raise
                    if document_context and not cancel_event.is_set():
                        GLib.idle_add(self._finish_local_document_reply, request_id, cancel_event,
                                      _("The model is unavailable. Local document matches:") + '\n\n' + display_document_context(document_context),
                                      )
                        return
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
                        pending_chunks.clear()
                        response_text = ""
                        offline_reply = offline.handle(message, get_language())

                if offline_reply is None and not response_text and not cancel_event.is_set():
                    if images:
                        raise AIImageRequestError(_("The model returned no answer for the image."))
                    if document_context:
                        GLib.idle_add(self._finish_local_document_reply, request_id, cancel_event,
                                      _("The model is unavailable. Local document matches:") + '\n\n' + display_document_context(document_context))
                        return
                    offline_reply = offline.handle(message, get_language())
                if pending_chunks and offline_reply is None and not cancel_event.is_set():
                    GLib.idle_add(self._update_ai_message, request_id,
                                  "".join(pending_chunks), True)
                    pending_chunks = []

            action = None
            if offline_reply is not None:
                pending_chunks.clear()
                response_text = offline_reply.text
                GLib.idle_add(self._replace_ai_reply, request_id, cancel_event, response_text)
                action = offline_reply
            elif response_text and not cancel_event.is_set() and not images and not document_context:
                # The model may explain the step. The command, if any, comes
                # from the user's sentence and the local catalog.
                propose = getattr(offline, "propose", None)
                if callable(propose):
                    try:
                        action = propose(message, get_language())
                    except Exception as exc:
                        logger.error("Could not prepare a confirmed action: %s", exc, exc_info=True)

            cancelled = cancel_event.is_set()
            if images or document_context:
                GLib.idle_add(self._finalize_response, request_id, response_text, cancelled, False)
            else:
                GLib.idle_add(self._finalize_response, request_id, response_text, cancelled)
            interaction, commands = split_offer(action)
            if (interaction or commands) and not cancelled:
                GLib.idle_add(self._offer_confirmed_action, interaction, commands,
                              request_id, cancel_event)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            GLib.idle_add(self._abort_ai_stream_if_active, request_id)
            GLib.idle_add(self._add_system_message_if_active, request_id,
                          cancel_event, _("Error: {error}").format(error=e))
            GLib.idle_add(self._finalize_response, request_id, "", cancel_event.is_set())

    def _complete_action_reply(self, request_id, cancel_event, reply, session_id):
        """Record a local task without interpreting its output as model code."""
        if request_id != self._active_request or cancel_event.is_set():
            if reply.executed:
                self._record_offline_result(request_id, reply.text, session_id)
            if reply.change is not None:
                self._finish_display_change(reply.change, False, request_id, session_id)
            return False
        self.streaming = False
        self.chat_view.abort_stream()
        try:
            self._add_ai_message(reply.text, False)
            self._remember('assistant', reply.text)
        except (OSError, ValueError, RuntimeError) as error:
            self._add_system_message(_("Could not save the response: {error}").format(error=error))
        finally:
            self._on_message_processed(request_id)
        if reply.change is not None:
            self._confirm_display_change(reply.change, request_id, cancel_event, session_id)
        return False

    def _show_action_audit(self, button=None):
        session_id = self.history_store.active_session_id

        def show(events, error):
            if getattr(self, '_audit_closed', False):
                return False
            dialog = Gtk.Dialog(title=_("Actions"), transient_for=self, modal=True)
            dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
            dialog.set_default_size(720, 400)
            tree = Gtk.TreeStore(str, str, str, str)
            parents = {}
            for event in events:
                identifier = event['operation_id']
                if identifier not in parents:
                    parents[identifier] = tree.append(None, [event['action_id'], event['resource'], event['phase'], identifier])
                tree.set_value(parents[identifier], 2, event['phase'])
                tree.append(parents[identifier], [event['phase'], event['detail'], '', ''])
            view = Gtk.TreeView(model=tree)
            for column, title in enumerate((_("Actions"), _("Details"), _("Status"), 'ID')):
                view.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=column))
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(view)
            dialog.get_content_area().pack_start(scrolled, True, True, 0)
            if error or not events:
                dialog.get_content_area().pack_start(Gtk.Label(label=error or _("No actions recorded.")), False, False, 8)
            dialog.show_all()
            dialog.run()
            dialog.destroy()
            return False

        def load():
            try:
                events, error = self.actions.audit.events(session_id), ''
            except (OSError, ValueError):
                events, error = [], _("Cannot read the operation audit.")
            GLib.idle_add(show, events, error)
        threading.Thread(target=load, daemon=True).start()

    def _finish_display_change(self, change, keep, request_id, session_id):
        def finish():
            ok, detail = change.confirm() if keep else change.revert()
            GLib.idle_add(self._record_offline_result, request_id, detail, session_id)
        threading.Thread(target=finish, daemon=False).start()

    def _confirm_display_change(self, change, request_id, cancel_event, session_id):
        dialog = Gtk.Dialog(title=_("Keep display configuration?"), transient_for=self, modal=True)
        dialog.add_button(_("Revert"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Keep"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        label = Gtk.Label()
        label.set_margin_top(16)
        label.set_margin_bottom(16)
        label.set_margin_start(16)
        label.set_margin_end(16)
        dialog.get_content_area().add(label)
        timer_active = [True]

        def tick():
            if (change.status != 'pending' or request_id != self._active_request
                    or cancel_event.is_set() or change.remaining_seconds <= 0):
                timer_active[0] = False
                dialog.response(Gtk.ResponseType.CANCEL)
                return False
            label.set_text(_("Reverting in {seconds} seconds unless you keep this configuration.").format(
                seconds=max(1, int(change.remaining_seconds + 0.999)),
            ))
            return True

        if tick():
            timer = GLib.timeout_add(200, tick)
            dialog.show_all()
            response = dialog.run()
            if timer_active[0]:
                GLib.source_remove(timer)
        else:
            response = Gtk.ResponseType.CANCEL
        dialog.destroy()
        keep = (response == Gtk.ResponseType.OK and request_id == self._active_request
                and not cancel_event.is_set())
        self._finish_display_change(change, keep, request_id, session_id)

    def _abort_ai_stream_if_active(self, request_id):
        if request_id == self._active_request:
            self.chat_view.abort_stream()
        return False

    def _replace_ai_reply(self, request_id, cancel_event, response_text):
        if request_id != self._active_request or cancel_event.is_set():
            return False
        self.chat_view.abort_stream()
        self._add_system_message(_("Offline mode: answering from local knowledge."))
        self._add_ai_message(response_text, False)
        return False

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

    def _offer_confirmed_action(self, interaction, commands, request_id, cancel_event):
        """Confirm a catalog action after the reply is on screen.

        A device chooser owns its own command list: the generic dialog must
        not also open, or a scanner install would be offered before the scan.
        """
        if request_id != self._active_request or cancel_event.is_set():
            return False
        if interaction:
            self._start_device_choice(interaction, commands, request_id, cancel_event)
            return False
        if commands:
            return self._offer_offline_commands(commands, request_id, cancel_event)
        return False

    def _start_device_choice(self, kind, commands, request_id, cancel_event):
        """Look up devices on a worker, then let the user pick on this thread."""
        store = getattr(self, "history_store", None)
        session_id = store.active_session_id if store is not None else None
        looking = {
            "wifi": _("Looking up Wi-Fi networks…"),
            "printer": _("Looking up printers…"),
            "scanner": _("Looking up scanners…"),
        }.get(kind)
        if looking:
            self._add_system_message(looking)

        def still_current():
            return request_id == self._active_request and not cancel_event.is_set()

        def report(text):
            # `text` is already redacted. The password never reaches history.
            GLib.idle_add(self._record_offline_result, request_id, text, session_id)

        device_dialogs.start(self, kind, commands, report, still_current)

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
        if request_id != self._active_request or cancel_event.is_set():
            return False

        self._local_running_request = request_id
        self.is_loading = True
        self.cancel_btn.set_sensitive(True)
        self.status_icon.set_from_icon_name('process-working', Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Running confirmed commands…"))
        self._add_system_message(_("Running confirmed commands. Cancellation stops later commands; an operation already started may still finish."))
        threading.Thread(
            target=self._run_offline_commands,
            args=(commands, request_id, cancel_event, self.history_store.active_session_id),
            daemon=True,
        ).start()
        return False

    def _run_offline_commands(self, commands, request_id, cancel_event, session_id=None):
        """Run the confirmed commands via pkexec (worker thread)."""
        store = getattr(self, 'history_store', None)
        session_id = session_id or (store.active_session_id if store is not None else None)
        try:
            for command in commands:
                # Cancellation does not roll back an operation already begun.
                if cancel_event.is_set() or request_id != self._active_request:
                    logger.info("Skipping offline command: request was cancelled")
                    return False
                try:
                    ok, output = offline_assistant.OfflineAssistant.run_privileged(command)
                except Exception:
                    # An exception after dispatch does not prove that no
                    # mutation happened. Avoid exposing command secrets.
                    ok = False
                    output = _("The operation result could not be determined. Check the target before trying again.")
                detail = output or (_("Done.") if ok else _("Failed."))
                result = f"$ {command.display()}\n{detail}"
                GLib.idle_add(self._record_offline_result, request_id, result, session_id)
                if not ok:
                    logger.info("Stopping confirmed command sequence after failure")
                    break
        finally:
            GLib.idle_add(MainWindow._finish_offline_commands, self, request_id, cancel_event)
        return False

    def _finish_offline_commands(self, request_id, cancel_event):
        if getattr(self, '_local_running_request', None) == request_id:
            self._local_running_request = None
        if request_id == self._active_request:
            self._on_message_processed(request_id, cancel_event.is_set())
        return False

    def _record_offline_result(self, request_id, result, session_id=None):
        """Show a command result and keep it in the conversation context.

        Without the history append, follow-up questions had no idea what
        was executed (the text only reached the chat buffer).
        """
        if request_id != self._active_request:
            # A completed action belongs to its original conversation even after a switch.
            if session_id is not None:
                available = self.history_store.list_sessions(include_archived=True)
                if any(session['id'] == session_id for session in available):
                    try:
                        self.history_store.append('assistant', result, session_id=session_id)
                    except (OSError, ValueError, RuntimeError) as error:
                        logger.error("Could not save the completed operation in its original session: %s", error)
            return False
        # A cancelled operation's actual outcome remains visible in the same
        # conversation. It must never be presented as if cancellation undid it.
        if self._cancel_event.is_set():
            self._add_system_message(_("Operation completed after cancellation. Check its result below; cancellation did not undo it."))
        self._add_system_message(result)
        try:
            self._remember("assistant", result)
        except (OSError, ValueError, RuntimeError) as error:
            self._add_system_message(_("Could not save the operation result: {error}").format(error=error))
        return False

    def _record_file_action_result(self, session_id, status, message):
        """Retain the outcome of an approved operation in its own conversation."""
        if status == 'writing':
            if not getattr(self, '_audit_closed', False) and session_id == self.history_store.active_session_id:
                self._add_system_message(message)
            return False
        if status not in ('written', 'restored', 'error'):
            return False
        store = self.history_store
        current = session_id == store.active_session_id
        if not getattr(self, '_audit_closed', False):
            if current:
                self._add_system_message(message)
            else:
                self._add_system_message(_("File operation completed in another conversation: {message}").format(message=message))
        try:
            if current and not getattr(self, '_audit_closed', False):
                self._remember('assistant', message)
            else:
                store.append('assistant', message, session_id=session_id)
        except (OSError, ValueError, RuntimeError) as error:
            logger.error("Could not save the file operation result: %s", error)
            if not getattr(self, '_audit_closed', False):
                self._show_history_warning(error)
        return False

    def _finalize_response(self, request_id: int, response_text: str,
                           cancelled: bool, allow_file_actions=True, history_text=None):
        """Persist the response and settle the UI (main loop only)."""
        if request_id != self._active_request:
            # A newer request replaced this one; its output is stale.
            logger.debug("Dropping result from stale request %s", request_id)
            return False

        self.streaming = False

        # Cancellation can arrive after the worker queued this final callback.
        cancelled = cancelled or self._cancel_event.is_set()
        if cancelled:
            self.chat_view.abort_stream()

        try:
            if response_text and len(response_text) > MAX_MESSAGE_CHARS:
                self.chat_view.abort_stream()
                self._add_system_message(_("AI response exceeds the local history limit. The incomplete response was not saved; ask for a shorter response."))
                return False
            if response_text and not cancelled:
                # The streamed body was inserted chunk by chunk; now that it is
                # complete, tag its code spans and close with the "\n\n"
                # separator (tudo dentro do ChatView, dono dos offsets).
                self.chat_view.close_streamed_message(response_text)

                # Ponto único de persistência do turno (memória + history.json)
                self._remember("assistant", response_text if history_text is None else history_text)
                store = getattr(self, 'history_store', None)
                if store is not None:
                    try:
                        store.set_diagnostic_state(self.offline.diagnostic_state())
                    except (OSError, ValueError) as error:
                        logger.error("Could not persist diagnostic progress: %s", error)
                        self._add_system_message(str(error))

                # GTK owns the consent dialog; digest/preview and the approved
                # transaction run in workers, including any polkit wait.
                if self.expert_mode and allow_file_actions:
                    session_id = self.history_store.active_session_id
                    cancel_event = self._cancel_event
                    controller = file_actions.offer_file_blocks_async(
                        self, response_text,
                        lambda status, msg: self._record_file_action_result(session_id, status, msg),
                        lambda: self.config.get("permissions.allowed_edit_dirs", []),
                        self.change_journal, session_id,
                        is_current=lambda: (not getattr(self, '_audit_closed', False)
                                            and not getattr(self, '_history_recovering', False)
                                            and request_id == self._active_request
                                            and not cancel_event.is_set()
                                            and session_id == self.history_store.active_session_id),
                    )
                    controllers = getattr(self, '_file_action_controllers', None)
                    if controllers is not None and controller is not None:
                        controllers[:] = [item for item in controllers if not item.finished]
                        controllers.append(controller)

        except (OSError, ValueError, RuntimeError) as error:
            logger.error("Could not finish or persist the response: %s", error)
            self._add_system_message(_("Could not save the response: {error}").format(error=error))
            if getattr(getattr(self, 'history_store', None), 'last_error', None) is not None:
                self._show_history_warning(error)
        finally:
            self._on_message_processed(request_id, cancelled)
        return False

    def _finish_local_document_reply(self, request_id, cancel_event, text):
        if request_id != self._active_request or cancel_event.is_set():
            self._on_message_processed(request_id, True)
            return False
        # Replace any partial provider output with the actual local excerpts.
        # Unapproved local source text must not travel later via chat history.
        visible = text + '\n\n' + _("These local excerpts are not saved in conversation history.")
        self.chat_view.abort_stream()
        self._add_ai_message(visible, False)
        summary = _("Local document matches were shown. Excerpts remain only in the local document index.")
        return self._finalize_response(request_id, visible, False, False, summary)

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
        local_running = getattr(self, '_local_running_request', None) == self._active_request
        self.is_loading = local_running
        self.streaming = False
        self.cancel_btn.set_sensitive(False)
        self.status_icon.set_from_icon_name("dialog-error", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Cancelled"))

        self.chat_view.abort_stream()
        if getattr(self, 'history_store', None) is not None:
            self._renew_offline_assistant()
        if local_running:
            self._add_system_message(_("Cancellation requested. An operation already started may still finish; its result will be shown here."))
        else:
            self._add_system_message(_("Streaming cancelled"))
        logger.info("Streaming cancelled by the user")

    def on_capture_screen_clicked(self, button, for_ai=False):
        """Handler for screen capture"""
        # The button/shortcut stays available even if the feature was
        # disabled after startup, so enforce the setting here too.
        if not self.config.get("features.screen_capture", True):
            self._add_system_message(_("Screen capture is disabled in settings."))
            return
        if self.is_loading:
            self.show_notification("Linux AI Assistant", _("Wait for the current message to be processed"))
            return

        # A capture is its own request. Reusing a cancelled chat's event would
        # discard a new capture, and reusing its ID lets old callbacks alter it.
        previous_event = getattr(self, '_cancel_event', None)
        if previous_event is not None:
            previous_event.set()
        capture_request = max(getattr(self, '_request_seq', 0) or 0,
                              getattr(self, '_active_request', 0) or 0) + 1
        self._request_seq = self._active_request = capture_request
        capture_event = threading.Event()
        self._cancel_event = capture_event
        capture_session = getattr(getattr(self, 'history_store', None), 'active_session_id', None)
        ocr_enabled = self.config.get("features.ocr_enabled", True)

        def current():
            return (not capture_event.is_set() and not getattr(self, '_audit_closed', False)
                    and capture_request == getattr(self, '_active_request', None)
                    and capture_session == getattr(getattr(self, 'history_store', None), 'active_session_id', None))

        def dispatch(callback, *args):
            if current():
                callback(*args)
            return False

        def queue(callback, *args):
            GLib.idle_add(dispatch, callback, *args)

        def remember_ocr(text):
            # Keep one guarded main-loop callback for persistence and display;
            # a rejected entry must not appear to have been saved successfully.
            try:
                self._remember('user', text)
                self._add_user_message(text)
            except (OSError, ValueError, RuntimeError) as error:
                self._add_system_message(_("Could not save the message: {error}").format(error=error))

        self._add_system_message(_("Capturing screen..."))
        self.is_loading = True
        self.streaming = False
        cancel_button = getattr(self, 'cancel_btn', None)
        if cancel_button is not None:
            cancel_button.set_sensitive(True)
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Capturing screen..."))

        def capture_and_process():
            temporary_image = None
            try:
                # Capture screen
                success, image_path = self.system_utils.capture_screen()

                if success:
                    temporary_image = image_path
                    if not current():
                        return
                    if for_ai:
                        image = prepare_image(image_path)
                        queue(self._review_captured_image, image, capture_request, capture_session, capture_event)
                        return
                    queue(self._add_system_message, _("Screen captured: {path}").format(path=image_path))

                    # Extract text
                    if ocr_enabled:
                        queue(self._add_system_message, _("Extracting text from the image..."))
                        success, text = self.system_utils.extract_text_from_image(image_path)

                        if success and text:
                            # Limit the text so it does not overload the UI
                            max_length = 2000
                            if len(text) > max_length:
                                text = text[:max_length] + "\n\n... " + _("(text truncated)")

                            queue(remember_ocr, f"[{_('Screen capture')}]\n{text}")
                        else:
                            queue(self._add_system_message, _("Could not extract text from the image."))
                    else:
                        queue(self._add_system_message, _("OCR disabled in settings."))

                else:
                    queue(self._add_system_message, _("Error capturing screen: {detail}").format(detail=image_path))

            except Exception as e:
                logger.error(f"Error in screen capture: {e}", exc_info=True)
                queue(self._add_system_message, _("Error: {error}").format(error=e))
            finally:
                if temporary_image is not None:
                    try:
                        os.unlink(temporary_image)
                    except OSError:
                        pass
                queue(self._on_capture_complete, capture_request, capture_session, capture_event)

        threading.Thread(target=capture_and_process, daemon=True).start()

    def _review_captured_image(self, image, request_id, session_id, cancel_event=None):
        event = cancel_event if cancel_event is not None else getattr(self, '_cancel_event', None)

        def current():
            return (not getattr(self, '_audit_closed', False)
                    and (event is None or not event.is_set())
                    and request_id == getattr(self, '_active_request', None)
                    and session_id == getattr(getattr(self, 'history_store', None), 'active_session_id', None))

        if not current():
            return False
        try:
            if not isinstance(self.ai_client, AIClient):
                raise AIImageRequestError(_("This client does not support reviewed images."))
            provider, model = self.ai_client.validate_image_request((image,))
            # A modal dialog runs a nested main loop: cancellation or a session
            # change can happen while the reviewed image is on screen.
            if review_image(self, image, provider, model) and current():
                self._pending_image = image
                self._pending_image_destination = (provider, model)
                self.attachment_label.set_text(_("Image ready for the next question: {name} ({width} × {height})")
                                               .format(name=image.filename, width=image.width, height=image.height))
                self.remove_image_button.show()
        except (OSError, ValueError, AIProviderError) as error:
            if current():
                self._add_system_message(_("Could not attach image: {error}").format(error=error))
        return False

    def _on_capture_complete(self, request_id=None, session_id=None, cancel_event=None):
        """Callback when the capture is completed."""
        if (getattr(self, '_audit_closed', False)
                or (cancel_event is not None and cancel_event.is_set())
                or (request_id is not None and request_id != getattr(self, '_active_request', None))
                or (session_id is not None and session_id != getattr(getattr(self, 'history_store', None), 'active_session_id', None))):
            return False
        self.is_loading = False
        self.streaming = False
        cancel_button = getattr(self, 'cancel_btn', None)
        if cancel_button is not None:
            cancel_button.set_sensitive(False)
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

        # Expertise changes the prompt; conversation boundaries are explicit sessions.
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
