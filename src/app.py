#!/usr/bin/env python3
"""
Linux AI Assistant - Aplicação principal

Um assistente de IA permanente para Linux com interface flutuante,
captura de ecrã, modo especialista e mais.
"""

import os
import sys
import signal
from pathlib import Path
import logging

# Configurar logging cedo
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Adicionar src ao path
sys.path.insert(0, str(Path(__file__).parent))

# Importar módulos (que já configuram o seu logging)
from .config_manager import ConfigManager
from .ai_client import AIClient
from .system_utils import SystemUtils
from .main_window import MainWindow
from .tray_icon import TrayIcon


class LinuxAIAssistant:
    """Aplicação principal"""
    
    def __init__(self):
        logger.info("Inicializar Linux AI Assistant")
        
        try:
            self.config = ConfigManager()
            logger.info("Configuração carregada")
            
            self.ai_client = AIClient(self.config)
            logger.info("Cliente de IA inicializado")
            
            self.system_utils = SystemUtils(self.config)
            logger.info("Utilitários do sistema inicializados")
            
            self.main_window = None
            self.tray_icon = None
            
        except Exception as e:
            logger.error(f"Erro ao inicializar aplicação: {e}", exc_info=True)
            raise
    
    def run(self):
        """Iniciar a aplicação"""
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gtk, Gdk
        
        logger.info("Inicializar GTK")
        
        try:
            # Inicializar GTK
            Gtk.init()
            logger.info("GTK inicializado")
            
            # Criar janela principal
            logger.info("A criar janela principal")
            self.main_window = MainWindow(self, self.config, self.ai_client, self.system_utils)
            
            # Criar ícone de system tray
            logger.info("A criar ícone de system tray")
            self.tray_icon = TrayIcon(self, self.config, self.main_window)
            
            # Mostrar janela se auto_start estiver ativo
            if self.config.get("app.auto_start", False):
                self.main_window.show()
                logger.info("Janela mostrada (auto_start ativo)")
            else:
                logger.info("Janela não mostrada (auto_start inativo). Use o ícone de system tray.")
            
            # Iniciar loop principal
            logger.info("A iniciar loop principal do GTK")
            Gtk.main()
            
        except KeyboardInterrupt:
            logger.info("Recebido KeyboardInterrupt. A terminar...")
            self.quit()
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}", exc_info=True)
            self.quit()
    
    def quit(self):
        """Terminar a aplicação"""
        logger.info("A terminar aplicação")
        
        try:
            if self.main_window:
                self.main_window.destroy()
                logger.info("Janela principal destruída")
            
            if self.tray_icon:
                if hasattr(self.tray_icon, 'indicator'):
                    self.tray_icon.indicator.set_status(0)
                    logger.info("AppIndicator desativado")
                elif hasattr(self.tray_icon, 'status_icon'):
                    self.tray_icon.status_icon.set_visible(False)
                    logger.info("StatusIcon desativado")
            
            logger.info("GTK main quit")
            Gtk.main_quit()
            
        except Exception as e:
            logger.error(f"Erro ao terminar aplicação: {e}", exc_info=True)


def main():
    """Ponto de entrada principal"""
    logger.info("Linux AI Assistant - Início")
    
    try:
        app = LinuxAIAssistant()
        
        # Manipular sinais para saír corretamente
        signal.signal(signal.SIGINT, lambda s, f: app.quit())
        signal.signal(signal.SIGTERM, lambda s, f: app.quit())
        logger.info("Handlers de sinal configurados")
        
        app.run()
        
    except Exception as e:
        logger.error(f"Erro fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
