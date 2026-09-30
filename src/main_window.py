import gi
import os
import json
import time
import threading
from typing import Optional, Dict
from pathlib import Path

try:
    import notify2
except ImportError:
    notify2 = None

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Notify', '0.7')

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango
import logging

from . import dock, file_actions
from .i18n import _

# Configurar logger
logger = logging.getLogger(__name__)


class MainWindow(Gtk.Window):
    """Application main window"""
    
    def __init__(self, app, config_manager, ai_client, system_utils):
        super().__init__(title="Linux AI Assistant")
        
        self.app = app
        self.config = config_manager
        self.ai_client = ai_client
        self.system_utils = system_utils
        
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
        
        # Docked mode: pin to an edge and reserve screen space
        if config_manager.get("app.dock_mode", "float") == "dock":
            self.set_decorated(False)
            self.dock_method = dock.apply_dock(
                self,
                config_manager.get("app.dock_edge", "right"),
                config_manager.get("app.width", 400)
            )
        else:
            self.dock_method = None
        
        # Make window resizable
        self.set_resizable(True)
        
        # Configurar estilo
        self._setup_style()
        
        # Configure notifications
        self._setup_notifications()
        
        # State variables
        self.expert_mode = False
        self.conversation_history = []
        self.current_response = ""
        self.streaming = False
        self.is_loading = False
        self.cancel_streaming = False
        
        # Create interface
        self._create_ui()
        
        # Conectar sinais
        self.connect("delete-event", self.on_delete_event)
        self.connect("configure-event", self.on_configure_event)
        self.connect("size-allocate", self.on_size_allocate)
        
        # Atalhos de teclado
        self._setup_keybindings()
        
        # Load conversation history
        self._load_conversation_history()
        
        logger.info("Janela main inicializada")
    
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
        """Configurar atalhos de teclado"""
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        
        # Ctrl+Enter for enviar
        key, mod = Gtk.accelerator_parse("<Control>Return")
        self.input_entry.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        
        # Ctrl+Shift+Enter for new linha
        key, mod = Gtk.accelerator_parse("<Control><Shift>Return")
        self.input_entry.add_accelerator("insert-at-cursor", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        
        # Escape for clear input
        key, mod = Gtk.accelerator_parse("Escape")
        accel_group.connect(accel_group.find_entry_keyval(key, mod), 
                          Gtk.AccelFlags.VISIBLE, self.on_clear_input)
        
        # Ctrl+E for alternar mode especialista
        key, mod = Gtk.accelerator_parse("<Control>e")
        self.expert_btn.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        
        # Ctrl+Q for fechar
        key, mod = Gtk.accelerator_parse("<Control>q")
        accel_group.connect(accel_group.find_entry_keyval(key, mod), 
                          Gtk.AccelFlags.VISIBLE, lambda *args: self.on_close_clicked())
        
        # Ctrl+S to capture screen
        key, mod = Gtk.accelerator_parse("<Control>s")
        accel_group.connect(accel_group.find_entry_keyval(key, mod), 
                          Gtk.AccelFlags.VISIBLE, lambda *args: self.on_capture_screen_clicked(None))
        
        logger.info("Atalhos de teclado configurados")
    
    def on_clear_input(self, *args):
        """Limpar input ao pressionar Escape"""
        self.input_entry.set_text("")
        logger.debug("Input limpo")
    
    def _setup_style(self):
        """Configurar estilo CSS da janela"""
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
        
        # Usar valores do theme ou defaults
        bg_color = colors.get('background', '#1e1e1e')
        text_color = colors.get('text', '#e0e0e0')
        accent_color = colors.get('accent', '#4CAF50')
        secondary_color = colors.get('secondary', '#2d2d2d')
        tertiary_color = colors.get('tertiary', '#252525')
        
        font_family = theme_ui.get('font_family', self.config.get('ui.font_family', 'Monospace'))
        font_size = theme_ui.get('font_size', self.config.get('ui.font_size', 12))
        border_radius = theme_ui.get('border_radius', self.config.get('ui.border_radius', 10))
        
        # Cores de syntax highlighting
        user_msg_color = syntax_colors.get('user_message', '#e0e0e0')
        ai_msg_color = syntax_colors.get('ai_message', '#a0d0a0')
        system_msg_color = syntax_colors.get('system_message', '#808080')
        
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
        logger.debug("Estilo CSS aplicado with tema: " + self.config.get("app.theme", "dark"))
    
    def _create_ui(self):
        """Criar interface da janela"""
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
        menu_btn.set_tooltip_text("Menu")
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
        close_btn.set_tooltip_text("Fechar")
        header.pack_end(close_btn, False, False, 0)
        
        # Minimize button
        minimize_btn = Gtk.Button.new_from_icon_name("window-minimize", Gtk.IconSize.MENU)
        minimize_btn.connect("clicked", lambda btn: self.iconify())
        minimize_btn.set_tooltip_text("Minimizar")
        header.pack_end(minimize_btn, False, False, 0)
        
        # Chat area
        chat_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        chat_area.set_property("name", "chat-area")
        main_box.pack_start(chat_area, True, True, 0)
        
        # ScrolledWindow for the chat
        self.chat_scrolled = Gtk.ScrolledWindow()
        self.chat_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.chat_scrolled.set_shadow_type(Gtk.ShadowType.NONE)
        chat_area.pack_start(self.chat_scrolled, True, True, 0)
        
        # TextView for the chat
        self.chat_textview = Gtk.TextView()
        self.chat_textview.set_editable(False)
        self.chat_textview.set_cursor_visible(False)
        self.chat_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.chat_textview.set_justification(Gtk.Justification.LEFT)
        self.chat_textview.set_left_margin(10)
        self.chat_textview.set_right_margin(10)
        self.chat_textview.set_top_margin(10)
        self.chat_textview.set_bottom_margin(10)
        
        # Configure tags for formatting
        text_buffer = self.chat_textview.get_buffer()
        
        text_buffer.create_tag("user-message", 
                                           foreground="#e0e0e0",
                                           font=f"{self.config.get('ui.font_family', 'Monospace')} {self.config.get('ui.font_size', 12)}")
        text_buffer.create_tag("ai-message",
                                        foreground="#a0d0a0",
                                        font=f"{self.config.get('ui.font_family', 'Monospace')} {self.config.get('ui.font_size', 12)}")
        text_buffer.create_tag("system-message",
                                           foreground="#808080",
                                           font=f"{self.config.get('ui.font_family', 'Monospace')} {self.config.get('ui.font_size', 11)}")
        text_buffer.create_tag("loading",
                                           foreground="#808080",
                                           font=f"{self.config.get('ui.font_family', 'Monospace')} {self.config.get('ui.font_size', 12)}",
                                           style=Pango.Style.ITALIC)
        
        self.chat_textview.set_buffer(text_buffer)
        self.chat_scrolled.add(self.chat_textview)
        
        # Input area
        input_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        input_area.set_property("name", "input-area")
        main_box.pack_start(input_area, False, False, 0)
        
        # Entry for input
        self.input_entry = Gtk.Entry()
        self.input_entry.set_placeholder_text(_("Type your message... (Ctrl+Enter to send)"))
        self.input_entry.connect("activate", self.on_input_activate)
        self.input_entry.set_hexpand(True)
        input_area.pack_start(self.input_entry, True, True, 0)
        
        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        input_area.pack_end(button_box, False, False, 0)
        
        # Cancel button
        self.cancel_btn = Gtk.Button.new_from_icon_name("process-stop", Gtk.IconSize.MENU)
        self.cancel_btn.connect("clicked", self.on_cancel_streaming)
        self.cancel_btn.set_tooltip_text("Cancelar")
        self.cancel_btn.set_sensitive(False)
        button_box.pack_start(self.cancel_btn, False, False, 0)
        
        # Screen capture button
        capture_btn = Gtk.Button.new_from_icon_name("camera-photo", Gtk.IconSize.MENU)
        capture_btn.connect("clicked", self.on_capture_screen_clicked)
        capture_btn.set_tooltip_text("Capture screen (Ctrl+S)")
        button_box.pack_start(capture_btn, False, False, 0)
        
        # Expert mode button
        self.expert_btn = Gtk.Button.new_from_icon_name("system-run", Gtk.IconSize.MENU)
        self.expert_btn.connect("clicked", self.on_expert_mode_toggled)
        self.expert_btn.set_tooltip_text("Modo Especialista (Ctrl+E)")
        button_box.pack_start(self.expert_btn, False, False, 0)
        
        # Send button
        send_btn = Gtk.Button.new_from_icon_name("go-next", Gtk.IconSize.MENU)
        send_btn.connect("clicked", lambda btn: self.on_send_clicked())
        send_btn.set_tooltip_text("Enviar (Ctrl+Enter)")
        button_box.pack_start(send_btn, False, False, 0)
        
        # Adicionar message de boas-vindas
        self._add_system_message("Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen.")
        
        # Scroll automatic for baixo
        self._scroll_to_bottom()
        
        logger.info("UI criada with sucesso")
    
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
        """Open theme management dialog"""
        self._show_config_dialog()
        # Selecionar o separador de temas
        # (Done automatically when the dialog opens)
    
    def on_add_theme_clicked(self, button):
        """Adicionar new tema"""
        dialog = Gtk.Dialog(
            title="Adicionar Tema",
            parent=self,
            flags=0,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )
        
        content = dialog.get_content_area()
        
        # Nome do tema
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
        
        # Cores
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
            if not theme_name:
                self.show_notification("Linux AI Assistant", "A theme name is required")
                dialog.destroy()
                return
            
            # Create tema
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
            
            # Guardar tema
            themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            themes_dir.mkdir(parents=True, exist_ok=True)
            theme_file = themes_dir / f"{theme_name}.json"
            
            try:
                with open(theme_file, 'w', encoding='utf-8') as f:
                    json.dump(theme, f, indent=2, ensure_ascii=False)
                
                self.show_notification("Linux AI Assistant", f"Tema '{theme_name}' criado")
                self._populate_themes_list()
                
            except Exception as e:
                self.show_notification("Linux AI Assistant", f"Erro a save tema: {e}")
        
        dialog.destroy()
    
    def on_remove_theme_clicked(self, button):
        """Remover theme selecionado"""
        selected_row = self.themes_listbox.get_selected_row()
        if not selected_row:
            self.show_notification("Linux AI Assistant", "Nenhum theme selecionado")
            return
        
        theme_name = selected_row.get_children()[0].get_text()
        
        # Do not allow removing built-in themes
        predefined_themes = ["dark", "light", "dracula", "solarized-dark"]
        if theme_name in predefined_themes:
            self.show_notification("Linux AI Assistant", "Cannot remove built-in themes")
            return
        
        # Confirm removal
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Remover theme '{theme_name}'?"
        )
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            theme_file = themes_dir / f"{theme_name}.json"
            
            try:
                if theme_file.exists():
                    theme_file.unlink()
                    self.show_notification("Linux AI Assistant", f"Tema '{theme_name}' removido")
                    self._populate_themes_list()
            except Exception as e:
                self.show_notification("Linux AI Assistant", f"Erro a remover tema: {e}")
    
    def _populate_themes_list(self):
        """Preencher a list de temas"""
        # Clear lista
        for child in self.themes_listbox.get_children():
            self.themes_listbox.remove(child)
        
        # Get available themes
        available_themes = self.config.get_available_themes()
        
        for theme_name in available_themes:
            theme_info = self.config.get_theme_info(theme_name)
            display_name = theme_info.get("name", theme_name) if theme_info else theme_name
            description = theme_info.get("description", "") if theme_info else ""
            
            row = Gtk.ListBoxRow()
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
        
        # Tokens por provedor
        for provider, usage in token_usage.items():
            provider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            
            provider_label = Gtk.Label(label=f"{provider}:")
            provider_label.set_halign(Gtk.Align.START)
            provider_box.pack_start(provider_label, False, False, 0)
            
            tokens_label = Gtk.Label(label=f"Input: {usage.get('input', 0)}, Output: {usage.get('output', 0)}, Total: {usage.get('total', 0)}")
            tokens_label.set_halign(Gtk.Align.END)
            provider_box.pack_end(tokens_label, True, True, 0)
            
            box.pack_start(provider_box, False, False, 0)
        
        # Button to reset statistics
        reset_btn = Gtk.Button(label=_("Reset Statistics"))
        reset_btn.connect("clicked", lambda btn: self.ai_client.reset_token_usage())
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
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                
                buffer = textview.get_buffer()
                for msg in history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", 0)
                    
                    timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                    
                    buffer.insert(buffer.get_end_iter(), f"[{timestamp_str}] [{role}]\n{content}\n\n")
            except Exception as e:
                logger.error(f"Error loading history: {e}")
                buffer = textview.get_buffer()
                buffer.insert(buffer.get_end_iter(), f"Error loading history: {e}")
        else:
            buffer = textview.get_buffer()
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
        
        # Notebook for separadores
        notebook = Gtk.Notebook()
        content.add(notebook)
        
        # Separador API
        api_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        api_box.set_border_width(10)
        
        api_label = Gtk.Label(label="<b>" + _("API Configuration") + "</b>")
        api_label.set_use_markup(True)
        api_box.pack_start(api_label, False, False, 0)
        
        # Provedor
        provider_label = Gtk.Label(label=_("AI Provider:"))
        api_box.pack_start(provider_label, False, False, 0)
        
        provider_combo = Gtk.ComboBoxText()
        for provider_name in self.config.get("api.providers", {}).keys():
            provider_combo.append(provider_name, provider_name)
        provider_combo.set_active_id(self.config.get("api.default_provider", "openrouter"))
        api_box.pack_start(provider_combo, False, False, 0)
        
        # API Key
        api_key_label = Gtk.Label(label=_("API Key:"))
        api_box.pack_start(api_key_label, False, False, 0)
        
        api_key_entry = Gtk.Entry()
        api_key_entry.set_visibility(False)
        api_key_entry.set_invisible_char('*')
        api_key_entry.set_placeholder_text(_("Enter your API Key"))
        
        # Carregar API key atual
        current_provider = self.config.get("api.default_provider", "openrouter")
        api_key_entry.set_text(self.config.get_api_key(current_provider) or "")
        api_box.pack_start(api_key_entry, False, False, 0)
        
        notebook.append_page(api_box, Gtk.Label(label="API"))
        
        # Separador Appearance
        ui_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ui_box.set_border_width(10)
        
        ui_label = Gtk.Label(label="<b>" + _("UI Configuration") + "</b>")
        ui_label.set_use_markup(True)
        ui_box.pack_start(ui_label, False, False, 0)
        
        # Opacidade
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
        
        # Tema
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
        
        # Mode docked (dock)
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
        
        # Separador Temas
        themes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        themes_box.set_border_width(10)
        
        themes_label = Gtk.Label(label="<b>" + _("Custom Themes") + "</b>")
        themes_label.set_use_markup(True)
        themes_box.pack_start(themes_label, False, False, 0)
        
        # Lista de temas
        self.themes_listbox = Gtk.ListBox()
        self.themes_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
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
        
        # Separador Funcionalidades
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
        
        # Mode especialista
        expert_check = Gtk.CheckButton(label=_("Expert Mode"))
        expert_check.set_active(self.config.get("features.expert_mode", True))
        features_box.pack_start(expert_check, False, False, 0)
        
        notebook.append_page(features_box, Gtk.Label(label="Funcionalidades"))
        
        # Show dialog
        dialog.show_all()
        
        # Guardar settings ao fechar
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            # Guardar provedor
            new_provider = provider_combo.get_active_id()
            self.config.set("api.default_provider", new_provider)
            
            # Guardar API key
            api_key = api_key_entry.get_text()
            self.config.set_api_key(new_provider, api_key)
            
            # Guardar opacidade
            opacity = opacity_scale.get_value()
            self.config.set("app.opacity", opacity)
            self.set_opacity(opacity)
            
            # Save always on top
            always_on_top = always_on_top_check.get_active()
            self.config.set("app.always_on_top", always_on_top)
            self.set_keep_above(always_on_top)
            
            # Guardar tema
            theme = theme_combo.get_active_id()
            self.config.set("ui.theme", theme)
            
            # Guardar funcionalidades
            self.config.set("features.screen_capture", screen_capture_check.get_active())
            self.config.set("features.ocr_enabled", ocr_check.get_active())
            self.config.set("features.expert_mode", expert_check.get_active())
            
            # Guardar mode ancorado
            self.config.set("app.dock_mode", "dock" if dock_check.get_active() else "float")
            self.config.set("app.dock_edge", dock_edge_combo.get_active_id() or "right")
            
            # Save settings
            self.config.save()
            
            # Show notification
            self.show_notification("Linux AI Assistant", _("Settings saved successfully"))
        
        dialog.destroy()
    
    def _add_user_message(self, message: str):
        """Adicionar message do user ao chat"""
        if not message:
            return
            
        buffer = self.chat_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        
        buffer.insert(end_iter, f"\n[{_('User')}]\n{message}\n\n")
        
        # Aplicar tag
        start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 12)
        end = buffer.get_end_iter()
        buffer.apply_tag_by_name("user-message", start, end)
        
        # Save to history
        self._save_message_to_history("user", message)
        
        self._scroll_to_bottom()
        logger.debug(f"Mensagem do user adicionada: {message[:50]}...")
    
    def _add_ai_message(self, message: str, streaming: bool = False):
        """Adicionar message da IA ao chat"""
        if self.cancel_streaming:
            return
            
        buffer = self.chat_textview.get_buffer()
        
        if streaming and self.streaming:
            # Adicionar a message em stream
            end_iter = buffer.get_end_iter()
            buffer.insert(end_iter, message)
            self._scroll_to_bottom()
        else:
            # Nova mensagem
            end_iter = buffer.get_end_iter()
            buffer.insert(end_iter, f"\n[{_('AI')}]\n{message}\n\n")
            
            # Aplicar tag
            start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 6)
            end = buffer.get_end_iter()
            buffer.apply_tag_by_name("ai-message", start, end)
            
            # Guardar em history (apenas message completa)
            if not streaming:
                self._save_message_to_history("assistant", message)
            
            self._scroll_to_bottom()
        
        logger.debug(f"Mensagem da IA adicionada: {message[:50]}...")
    
    def _add_system_message(self, message: str):
        """Adicionar message do system ao chat"""
        buffer = self.chat_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        
        buffer.insert(end_iter, f"\n[{_('System')}]\n{message}\n\n")
        
        # Aplicar tag
        start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 10)
        end = buffer.get_end_iter()
        buffer.apply_tag_by_name("system-message", start, end)
        
        self._scroll_to_bottom()
        logger.info(f"Mensagem do sistema: {message}")
    
    def _add_loading_message(self, message: str = None):
        """Add loading message"""
        if message is None:
            message = _("Thinking...")
        buffer = self.chat_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        
        buffer.insert(end_iter, f"\n[{_('AI')}]\n{message}")
        
        # Aplicar tag de loading
        start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 6)
        end = buffer.get_end_iter()
        buffer.apply_tag_by_name("loading", start, end)
        
        self._scroll_to_bottom()
    
    def _remove_loading_message(self):
        """Remover message de loading"""
        buffer = self.chat_textview.get_buffer()
        start = buffer.get_end_iter()
        
        # Search for the last loading message
        text = buffer.get_text(buffer.get_start_iter(), start)
        lines = text.split('\n')
        
        # Remove the last lines starting with [AI]
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith('[' + _('AI') + ']'):
                # Remove this line and the next one (content)
                line_start = buffer.get_iter_at_line(i)
                buffer.delete(line_start, start)
                break
    
    def _scroll_to_bottom(self):
        """Auto-scroll to the bottom of the chat"""
        GLib.idle_add(self._do_scroll_to_bottom)
    
    def _do_scroll_to_bottom(self):
        """Executa o scroll for the fundo"""
        adjustment = self.chat_scrolled.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return False
    
    def _load_conversation_history(self):
        """Load conversation history"""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        try:
            if history_file.exists():
                with open(history_file, 'r', encoding='utf-8') as f:
                    self.conversation_history = json.load(f)
                logger.info(f"History loaded with {len(self.conversation_history)} messages")
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            self.conversation_history = []
    
    def _save_message_to_history(self, role: str, content: str):
        """Save message to persistent history"""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        try:
            history = []
            if history_file.exists():
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            
            history.append({
                "timestamp": time.time(),
                "role": role,
                "content": content
            })
            
            # Keep the last 1000 messages
            history = history[-1000:]
            
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Message saved to history: {role}")
        except Exception as e:
            logger.error(f"Error saving history: {e}")
    
    def _get_context_message(self) -> Optional[Dict[str, str]]:
        """Obter message de contexto with base no modo"""
        if self.expert_mode:
            return {
                "role": "system",
                "content": """Eres a especialista em sistemas Linux with vastos conhecimentos sobre:
- Configuration of systems and services
- Management de pacotes (apt, dnf, pacman, xbps, etc.)
- Configuration de network e firewall
- Scripting em Bash e Python
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
        """Get system information for context"""
        try:
            info = self.system_utils.get_system_info()
            return f"""
Sistema: {info.get('distro', 'Unknown')}
Kernel: {info.get('release', 'Unknown')}
Architecture: {info.get('machine', 'Unknown')}
Memory: {info.get('memory_used', 'N/A')} used of {info.get('memory_total', 'N/A')}
CPU: {info.get('cpu_cores', 'N/A')} cores
"""
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
        
        # Adicionar message do utilizador
        self._add_user_message(text)
        
        # Add the message to the history
        self.conversation_history.append({"role": "user", "content": text})
        
        # Update estado
        self.is_loading = True
        self.streaming = True
        self.cancel_streaming = False
        self.cancel_btn.set_sensitive(True)
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text("Processando...")
        
        # Processar em thread separado for no bloquear a UI
        threading.Thread(target=self._process_message, args=(text,), daemon=True).start()
    
    def _process_message(self, message: str):
        """Processar message e get response da IA"""
        GLib.idle_add(self._add_loading_message)
        
        try:
            # Preparar contexto
            context = self._get_context_message()
            
            # Add context to the history
            full_history = [context] + self.conversation_history if context else self.conversation_history
            
            # Get response da IA
            self.streaming = True
            self.current_response = ""
            
            response_text = ""
            for chunk in self.ai_client.stream_chat(full_history):
                if self.cancel_streaming:
                    break
                
                response_text += chunk
                GLib.idle_add(self._update_ai_message, chunk, True)
            
            self.streaming = False
            
            # Add to the history
            if not self.cancel_streaming:
                self.conversation_history.append({"role": "assistant", "content": response_text})
                
                # Oferecer write de ficheiros em mode especialista
                # GTK is not thread-safe: show dialogs from the main loop
                if self.expert_mode and response_text:
                    def _offer_blocks():
                        file_actions.offer_file_blocks(
                            self, response_text,
                            lambda msg: GLib.idle_add(self._add_system_message, msg)
                        )
                        return False
                    GLib.idle_add(_offer_blocks)
            
            # Update UI
            GLib.idle_add(self._on_message_processed)
            
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            GLib.idle_add(self._add_system_message, f"Erro: {e}")
            GLib.idle_add(self._on_message_processed)
    
    def _update_ai_message(self, chunk: str, streaming: bool):
        """Atualizar message da IA"""
        self._add_ai_message(chunk, streaming)
        return False
    
    def _on_message_processed(self):
        """Callback when message is processed"""
        self.is_loading = False
        self.cancel_streaming = False
        self.cancel_btn.set_sensitive(False)
        self.status_icon.set_from_icon_name("emblem-ok", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Ready"))
        
        # Remover message de loading
        GLib.idle_add(self._remove_loading_message)
        
        # Show notification se no estiver ativa
        if not self.get_window().get_property("is-active"):
            self.show_notification("Linux AI Assistant", "Nova response recebida")
    
    def on_cancel_streaming(self, button):
        """Cancelar streaming atual"""
        self.cancel_streaming = True
        self.is_loading = False
        self.streaming = False
        self.cancel_btn.set_sensitive(False)
        self.status_icon.set_from_icon_name("dialog-error", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text("Cancelado")
        
        self._add_system_message("Streaming cancelado")
        GLib.idle_add(self._remove_loading_message)
        logger.info("Streaming cancelado pelo utilizador")
    
    def on_capture_screen_clicked(self, button):
        """Handler for screen capture"""
        if self.is_loading:
            self.show_notification("Linux AI Assistant", "Wait for the current message to be processed")
            return
            
        self._add_system_message("Capturing screen...")
        self.is_loading = True
        self.status_icon.set_from_icon_name("process-working", Gtk.IconSize.MENU)
        
        def capture_and_process():
            try:
                # Capture screen
                success, image_path = self.system_utils.capture_screen()
                
                if success:
                    GLib.idle_add(self._add_system_message, f"Screen captured: {image_path}")
                    
                    # Extrair texto
                    if self.config.get("features.ocr_enabled", True):
                        GLib.idle_add(self._add_system_message, "A extrair text da imagem...")
                        success, text = self.system_utils.extract_text_from_image(image_path)
                        
                        if success and text:
                            # Limitar text for no sobrecarregar
                            max_length = 2000
                            if len(text) > max_length:
                                text = text[:max_length] + "\n\n... (texto truncado)"
                            
                            GLib.idle_add(self._add_user_message, f"[Screen capture]\n{text}")
                            GLib.idle_add(self._update_conversation_history, "user", f"[Screen capture]\n{text}")
                        else:
                            GLib.idle_add(self._add_system_message, "Could not extract text from the image.")
                    else:
                        GLib.idle_add(self._add_system_message, "OCR disabled in settings.")
                    
                    # Remove temporary image
                    try:
                        os.unlink(image_path)
                    except:
                        pass
                else:
                    GLib.idle_add(self._add_system_message, f"Error capturing screen: {image_path}")
                    
            except Exception as e:
                logger.error(f"Error in screen capture: {e}", exc_info=True)
                GLib.idle_add(self._add_system_message, f"Erro: {e}")
            finally:
                GLib.idle_add(self._on_capture_complete)
        
        threading.Thread(target=capture_and_process, daemon=True).start()
    
    def _on_capture_complete(self):
        """Callback when capture is completed"""
        self.is_loading = False
        self.status_icon.set_from_icon_name("emblem-ok", Gtk.IconSize.MENU)
        self.status_icon.set_tooltip_text(_("Ready"))
    
    def _update_conversation_history(self, role: str, content: str):
        """Update conversation history"""
        self.conversation_history.append({"role": role, "content": content})
        return False
    
    def on_expert_mode_toggled(self, button):
        """Alternar mode especialista"""
        self.expert_mode = not self.expert_mode
        
        if self.expert_mode:
            self.expert_btn.get_style_context().add_class("expert")
            self._add_system_message("Expert Mode ENABLED - Helping with system configuration")
            self.show_notification("Linux AI Assistant", "Modo Especialista Ativado")
        else:
            self.expert_btn.get_style_context().remove_class("expert")
            self._add_system_message("Modo Especialista DESATIVADO")
            self.show_notification("Linux AI Assistant", "Modo Especialista Desativado")
        
        # Clear history for new context
        self.conversation_history = []
        logger.info(f"Modo especialista {'ativado' if self.expert_mode else 'desativado'}")
    
    def on_close_clicked(self):
        """Handler for fechar janela"""
        self.on_delete_event(None, None)
    
    def on_delete_event(self, widget, event):
        """Handler for fechar janela"""
        # Guardar geometria da janela
        if hasattr(self, 'get_window') and self.get_window():
            geometry = self.get_window().get_geometry()
            self.config.set_window_geometry(
                geometry.width,
                geometry.height,
                self.get_window().get_position().x,
                self.get_window().get_position().y
            )
        
        # Save history
        # (Already saved automatically when adding messages)
        
        # Close application
        logger.info("Janela fechada")
        Gtk.main_quit()
        return True
    
    def on_configure_event(self, widget, event):
        """Handler for redimensionar/mover janela"""
        # Save position
        if hasattr(self, 'get_window') and self.get_window():
            x, y = self.get_window().get_position()
            self.config.set("app.x_position", x)
            self.config.set("app.y_position", y)
            
            # Guardar tamanho
            geometry = self.get_window().get_geometry()
            self.config.set("app.width", geometry.width)
            self.config.set("app.height", geometry.height)
        
        return True
    
    def on_size_allocate(self, widget, allocation):
        """Handler for when size is allocated"""
        # Guardar tamanho
        if hasattr(self, 'get_window') and self.get_window():
            geometry = self.get_window().get_geometry()
            self.config.set("app.width", geometry.width)
            self.config.set("app.height", geometry.height)
        
        return True
