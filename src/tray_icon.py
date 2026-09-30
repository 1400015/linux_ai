import gi
import os
import sys
from pathlib import Path

try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, AppIndicator3, GLib
except ImportError:
    # Fallback para sistemas sem AppIndicator3
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gtk, Gdk
    


class TrayIcon:
    """Ícone de system tray para a aplicação"""
    
    def __init__(self, app, config_manager, main_window):
        self.app = app
        self.config = config_manager
        self.main_window = main_window
        
        self._create_tray_icon()
    
    def _create_tray_icon(self):
        """Criar ícone de system tray"""
        try:
            # Tentar usar AppIndicator3 (Ubuntu)
            self.indicator = AppIndicator3.IndicatorApp.new(
                "linux-ai-assistant",
                "system-run",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self.indicator.set_attention_icon("dialog-information")
            
            # Criar menu
            self._create_menu()
            self.indicator.set_menu(self.menu)
            
        except (ImportError, AttributeError):
            # Fallback para Gtk.StatusIcon (funciona na maioria dos sistemas)
            self._create_status_icon()
    
    def _create_status_icon(self):
        """Criar ícone de status (fallback)"""
        self.status_icon = Gtk.StatusIcon()
        
        # Carregar ícone (usar ícone padrão do sistema)
        icon_theme = Gtk.IconTheme.get_default()
        icon = icon_theme.load_icon("system-run", 48, 0)
        if icon:
            self.status_icon.set_from_pixbuf(icon)
        else:
            # Criar ícone simples
            pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 48)
            pixbuf.fill(0x4CAF50FF)  # Verde
            self.status_icon.set_from_pixbuf(pixbuf)
        
        self.status_icon.set_tooltip_text("Linux AI Assistant")
        self.status_icon.connect("activate", self.on_tray_clicked)
        self.status_icon.connect("popup-menu", self.on_tray_menu)
        
        # Criar menu
        self._create_menu()
    
    def _create_menu(self):
        """Criar menu do ícone de system tray"""
        self.menu = Gtk.Menu()
        
        # Item para mostrar/esconder janela
        self.toggle_item = Gtk.MenuItem(label="Mostrar Janela")
        self.toggle_item.connect("activate", self.on_toggle_window)
        self.menu.append(self.toggle_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item para modo especialista
        self.expert_item = Gtk.CheckMenuItem(label="Modo Especialista")
        self.expert_item.connect("toggled", self.on_toggle_expert_mode)
        self.menu.append(self.expert_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item para configurar
        config_item = Gtk.MenuItem(label="Configurações")
        config_item.connect("activate", self.on_config_clicked)
        self.menu.append(config_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item para sair
        quit_item = Gtk.MenuItem(label="Sair")
        quit_item.connect("activate", self.on_quit_clicked)
        self.menu.append(quit_item)
        
        self.menu.show_all()
    
    def on_tray_clicked(self, icon):
        """Handler para clique no ícone de system tray"""
        if hasattr(self.main_window, 'is_active') and self.main_window.is_active():
            self.main_window.hide()
        else:
            self.main_window.show()
            self.main_window.present()
    
    def on_tray_menu(self, icon, button, time):
        """Handler para menu do ícone de system tray"""
        if hasattr(self, 'status_icon'):
            self.menu.popup_at_pointer(None)  # Popup no cursor
    
    def on_toggle_window(self, item):
        """Alternar visibilidade da janela"""
        if hasattr(self.main_window, 'is_active') and self.main_window.is_active():
            self.main_window.hide()
            self.toggle_item.set_label("Mostrar Janela")
        else:
            self.main_window.show()
            self.main_window.present()
            self.toggle_item.set_label("Esconder Janela")
    
    def on_toggle_expert_mode(self, item):
        """Alternar modo especialista"""
        self.main_window.on_expert_mode_toggled(None)
        self.expert_item.set_active(self.main_window.expert_mode)
    
    def on_config_clicked(self, item):
        """Abrir janela de configurações"""
        # Por implementar
        self._show_config_dialog()
    
    def _show_config_dialog(self):
        """Mostrar diálogo de configurações"""
        dialog = Gtk.Dialog(
            title="Configurações - Linux AI Assistant",
            parent=self.main_window,
            flags=0,
            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )
        
        dialog.set_default_size(400, 300)
        
        # Criar conteúdo do diálogo
        content = dialog.get_content_area()
        
        # Notebook para separadores
        notebook = Gtk.Notebook()
        content.add(notebook)
        
        # Separador API
        api_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        api_box.set_border_width(10)
        
        api_label = Gtk.Label(label="<b>Configuração de API</b>")
        api_label.set_use_markup(True)
        api_box.pack_start(api_label, False, False, 0)
        
        # Provedor
        provider_label = Gtk.Label(label="Provedor de IA:")
        api_box.pack_start(provider_label, False, False, 0)
        
        provider_combo = Gtk.ComboBoxText()
        provider_combo.append("openrouter", "OpenRouter")
        provider_combo.append("google_ai_studio", "Google AI Studio")
        provider_combo.append("local_llm", "Modelo Local")
        provider_combo.set_active_id(self.config.get("api.default_provider", "openrouter"))
        api_box.pack_start(provider_combo, False, False, 0)
        
        # API Key
        api_key_label = Gtk.Label(label="API Key:")
        api_box.pack_start(api_key_label, False, False, 0)
        
        api_key_entry = Gtk.Entry()
        api_key_entry.set_visibility(False)
        api_key_entry.set_invisible_char('*')
        api_key_entry.set_placeholder_text("Insira a sua API Key")
        
        # Carregar API key atual
        current_provider = self.config.get("api.default_provider", "openrouter")
        api_key_entry.set_text(self.config.get_api_key(current_provider) or "")
        api_box.pack_start(api_key_entry, False, False, 0)
        
        notebook.append_page(api_box, Gtk.Label(label="API"))
        
        # Separador Aparência
        ui_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ui_box.set_border_width(10)
        
        ui_label = Gtk.Label(label="<b>Configuração de UI</b>")
        ui_label.set_use_markup(True)
        ui_box.pack_start(ui_label, False, False, 0)
        
        # Opacidade
        opacity_label = Gtk.Label(label="Opacidade da janela:")
        ui_box.pack_start(opacity_label, False, False, 0)
        
        opacity_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.1, 1.0, 0.1
        )
        opacity_scale.set_value(self.config.get("app.opacity", 0.9))
        ui_box.pack_start(opacity_scale, False, False, 0)
        
        # Sempre visível
        always_on_top_check = Gtk.CheckButton(label="Sempre visível")
        always_on_top_check.set_active(self.config.get("app.always_on_top", True))
        ui_box.pack_start(always_on_top_check, False, False, 0)
        
        notebook.append_page(ui_box, Gtk.Label(label="Aparência"))
        
        # Mostrar diálogo
        dialog.show_all()
        
        # Guardar configurações ao fechar
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
            self.main_window.set_opacity(opacity)
            
            # Guardar sempre visível
            always_on_top = always_on_top_check.get_active()
            self.config.set("app.always_on_top", always_on_top)
            self.main_window.set_keep_above(always_on_top)
            
            # Guardar configurações
            self.config.save()
        
        dialog.destroy()
    
    def on_quit_clicked(self, item):
        """Sair da aplicação"""
        Gtk.main_quit()
    
    def update_expert_mode(self, enabled: bool):
        """Atualizar estado do modo especialista no menu"""
        if hasattr(self, 'expert_item'):
            self.expert_item.set_active(enabled)
