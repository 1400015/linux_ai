import gi
import logging

from .i18n import _

# Configurar logger
logger = logging.getLogger(__name__)

try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, AppIndicator3
except ImportError:
    # Fallback for sistemas sem AppIndicator3
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gtk, GdkPixbuf


class TrayIcon:
    """System tray icon for the application"""
    
    def __init__(self, app, config_manager, main_window):
        self.app = app
        self.config = config_manager
        self.main_window = main_window
        
        logger.info("Initialize system tray icon")
        
        self._create_tray_icon()
    
    def _create_tray_icon(self):
        """Create system tray icon"""
        try:
            # Tentar usar AppIndicator3 (Ubuntu)
            self.indicator = AppIndicator3.IndicatorApp.new(
                "linux-ai-assistant",
                "system-run",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self.indicator.set_attention_icon("dialog-information")
            
            # Create menu
            self._create_menu()
            self.indicator.set_menu(self.menu)
            
            logger.info("AppIndicator3 criado with sucesso")
            
        except (ImportError, AttributeError) as e:
            logger.warning(f"AppIndicator3 not available: {e}. Using StatusIcon.")
            # Fallback for Gtk.StatusIcon (funciona na maioria dos sistemas)
            self._create_status_icon()
    
    def _create_status_icon(self):
        """Create status icon (fallback)"""
        try:
            self.status_icon = Gtk.StatusIcon()
            
            # Load icon (use system default icon)
            icon_theme = Gtk.IconTheme.get_default()
            icon = icon_theme.load_icon("system-run", 48, 0)
            if icon:
                self.status_icon.set_from_pixbuf(icon)
            else:
                # Create icon simples
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 48)
                    pixbuf.fill(0x4CAF50FF)  # Verde
                    self.status_icon.set_from_pixbuf(pixbuf)
                except Exception as e:
                    logger.error(f"Error creating icon: {e}")
            
            self.status_icon.set_tooltip_text("Linux AI Assistant")
            self.status_icon.connect("activate", self.on_tray_clicked)
            self.status_icon.connect("popup-menu", self.on_tray_menu)
            
            # Create menu
            self._create_menu()
            
            logger.info("StatusIcon criado with sucesso")
            
        except Exception as e:
            logger.error(f"Erro a create StatusIcon: {e}")
            raise
    
    def _create_menu(self):
        """Create system tray icon menu"""
        self.menu = Gtk.Menu()
        
        # Item for mostrar/esconder janela
        self.toggle_item = Gtk.MenuItem(label=_("Show Window"))
        self.toggle_item.connect("activate", self.on_toggle_window)
        self.menu.append(self.toggle_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item for mode especialista
        self.expert_item = Gtk.CheckMenuItem(label=_("Expert Mode"))
        self.expert_item.connect("toggled", self.on_toggle_expert_mode)
        self.menu.append(self.expert_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item for configurar
        config_item = Gtk.MenuItem(label=_("Settings"))
        config_item.connect("activate", self.on_config_clicked)
        self.menu.append(config_item)
        
        # History item
        history_item = Gtk.MenuItem(label=_("Conversation History"))
        history_item.connect("activate", self.on_history_clicked)
        self.menu.append(history_item)
        
        # Statistics item
        stats_item = Gtk.MenuItem(label=_("Statistics"))
        stats_item.connect("activate", self.on_stats_clicked)
        self.menu.append(stats_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item for sair
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", self.on_quit_clicked)
        self.menu.append(quit_item)
        
        self.menu.show_all()
        logger.debug("Menu do system tray criado")
    
    def on_tray_clicked(self, icon):
        """Handler for system tray icon click"""
        if hasattr(self.main_window, 'get_window') and self.main_window.get_window():
            if self.main_window.get_window().get_property("is-active"):
                self.main_window.hide()
                self.toggle_item.set_label("Mostrar Janela")
            else:
                self.main_window.show()
                self.main_window.present()
                self.toggle_item.set_label("Esconder Janela")
        else:
            self.main_window.show()
            self.main_window.present()
            self.toggle_item.set_label("Esconder Janela")
        
        logger.debug("System tray icon clicked")
    
    def on_tray_menu(self, icon, button, time):
        """Handler for system tray icon menu"""
        if hasattr(self, 'status_icon'):
            self.menu.popup_at_pointer(None)  # Popup no cursor
            logger.debug("Menu do system tray mostrado")
    
    def on_toggle_window(self, item):
        """Alternar visibilidade da janela"""
        if hasattr(self.main_window, 'get_window') and self.main_window.get_window():
            if self.main_window.get_window().get_property("is-active"):
                self.main_window.hide()
                self.toggle_item.set_label("Mostrar Janela")
            else:
                self.main_window.show()
                self.main_window.present()
                self.toggle_item.set_label("Esconder Janela")
        else:
            self.main_window.show()
            self.main_window.present()
            self.toggle_item.set_label("Esconder Janela")
        
        logger.debug("Janela alternada")
    
    def on_toggle_expert_mode(self, item):
        """Alternar mode especialista"""
        self.main_window.on_expert_mode_toggled(None)
        self.expert_item.set_active(self.main_window.expert_mode)
        logger.debug(f"Modo especialista alternado: {self.main_window.expert_mode}")
    
    def on_config_clicked(self, item):
        """Open settings window"""
        self.main_window._show_config_dialog()
        logger.debug("Settings dialog opened")
    
    def on_history_clicked(self, item):
        """Open history"""
        self.main_window._show_history_dialog()
        logger.debug("History dialog opened")
    
    def on_stats_clicked(self, item):
        """Open statistics"""
        self.main_window._show_stats_dialog()
        logger.debug("Statistics dialog opened")
    
    def on_quit_clicked(self, item):
        """Quit the application"""
        logger.info("Quit through the system tray menu")
        self.app.quit()
    
    def update_expert_mode(self, enabled: bool):
        """Atualizar estado do mode especialista no menu"""
        if hasattr(self, 'expert_item'):
            self.expert_item.set_active(enabled)
            logger.debug(f"Menu do mode especialista atualizado: {enabled}")
    
    def update_toggle_label(self, visible: bool):
        """Atualizar label do toggle no menu"""
        if hasattr(self, 'toggle_item'):
            self.toggle_item.set_label("Esconder Janela" if visible else "Mostrar Janela")
