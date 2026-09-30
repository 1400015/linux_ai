import gi
import logging

# Configurar logger
logger = logging.getLogger(__name__)

try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, AppIndicator3
except ImportError:
    # Fallback para sistemas sem AppIndicator3
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gtk, GdkPixbuf


class TrayIcon:
    """Ícone de system tray para a aplicação"""
    
    def __init__(self, app, config_manager, main_window):
        self.app = app
        self.config = config_manager
        self.main_window = main_window
        
        logger.info("Inicializar ícone de system tray")
        
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
            
            logger.info("AppIndicator3 criado com sucesso")
            
        except (ImportError, AttributeError) as e:
            logger.warning(f"AppIndicator3 não disponível: {e}. A usar StatusIcon.")
            # Fallback para Gtk.StatusIcon (funciona na maioria dos sistemas)
            self._create_status_icon()
    
    def _create_status_icon(self):
        """Criar ícone de status (fallback)"""
        try:
            self.status_icon = Gtk.StatusIcon()
            
            # Carregar ícone (usar ícone padrão do sistema)
            icon_theme = Gtk.IconTheme.get_default()
            icon = icon_theme.load_icon("system-run", 48, 0)
            if icon:
                self.status_icon.set_from_pixbuf(icon)
            else:
                # Criar ícone simples
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 48)
                    pixbuf.fill(0x4CAF50FF)  # Verde
                    self.status_icon.set_from_pixbuf(pixbuf)
                except Exception as e:
                    logger.error(f"Erro a criar ícone: {e}")
            
            self.status_icon.set_tooltip_text("Linux AI Assistant")
            self.status_icon.connect("activate", self.on_tray_clicked)
            self.status_icon.connect("popup-menu", self.on_tray_menu)
            
            # Criar menu
            self._create_menu()
            
            logger.info("StatusIcon criado com sucesso")
            
        except Exception as e:
            logger.error(f"Erro a criar StatusIcon: {e}")
            raise
    
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
        
        # Item para histórico
        history_item = Gtk.MenuItem(label="Histórico")
        history_item.connect("activate", self.on_history_clicked)
        self.menu.append(history_item)
        
        # Item para estatísticas
        stats_item = Gtk.MenuItem(label="Estatísticas")
        stats_item.connect("activate", self.on_stats_clicked)
        self.menu.append(stats_item)
        
        # Separador
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Item para sair
        quit_item = Gtk.MenuItem(label="Sair")
        quit_item.connect("activate", self.on_quit_clicked)
        self.menu.append(quit_item)
        
        self.menu.show_all()
        logger.debug("Menu do system tray criado")
    
    def on_tray_clicked(self, icon):
        """Handler para clique no ícone de system tray"""
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
        
        logger.debug("Ícone de system tray clicado")
    
    def on_tray_menu(self, icon, button, time):
        """Handler para menu do ícone de system tray"""
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
        """Alternar modo especialista"""
        self.main_window.on_expert_mode_toggled(None)
        self.expert_item.set_active(self.main_window.expert_mode)
        logger.debug(f"Modo especialista alternado: {self.main_window.expert_mode}")
    
    def on_config_clicked(self, item):
        """Abrir janela de configurações"""
        self.main_window._show_config_dialog()
        logger.debug("Diálogo de configurações aberto")
    
    def on_history_clicked(self, item):
        """Abrir histórico"""
        self.main_window._show_history_dialog()
        logger.debug("Diálogo de histórico aberto")
    
    def on_stats_clicked(self, item):
        """Abrir estatísticas"""
        self.main_window._show_stats_dialog()
        logger.debug("Diálogo de estatísticas aberto")
    
    def on_quit_clicked(self, item):
        """Sair da aplicação"""
        logger.info("Sair através do menu do system tray")
        self.app.quit()
    
    def update_expert_mode(self, enabled: bool):
        """Atualizar estado do modo especialista no menu"""
        if hasattr(self, 'expert_item'):
            self.expert_item.set_active(enabled)
            logger.debug(f"Menu do modo especialista atualizado: {enabled}")
    
    def update_toggle_label(self, visible: bool):
        """Atualizar label do toggle no menu"""
        if hasattr(self, 'toggle_item'):
            self.toggle_item.set_label("Esconder Janela" if visible else "Mostrar Janela")
