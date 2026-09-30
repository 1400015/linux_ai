#!/usr/bin/env python3
"""
Linux AI Assistant - Aplicação principal

Um assistente de IA permanente para Linux com integração de APIs,
captura de ecrã, modo especialista e mais.
"""

import os
import sys
import signal
from pathlib import Path

# Adicionar src ao path
sys.path.insert(0, str(Path(__file__).parent))

from .config_manager import ConfigManager
from .ai_client import AIClient
from .system_utils import SystemUtils
from .main_window import MainWindow
from .tray_icon import TrayIcon


class LinuxAIAssistant:
    """Aplicação principal"""
    
    def __init__(self):
        self.config = ConfigManager()
        self.ai_client = AIClient(self.config)
        self.system_utils = SystemUtils(self.config)
        
        self.main_window = None
        self.tray_icon = None
    
    def run(self):
        """Iniciar a aplicação"""
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gtk, Gdk
        
        # Inicializar GTK
        Gtk.init()
        
        # Criar janela principal
        self.main_window = MainWindow(self, self.config, self.ai_client, self.system_utils)
        
        # Criar ícone de system tray
        self.tray_icon = TrayIcon(self, self.config, self.main_window)
        
        # Mostrar janela se auto_start estiver ativo
        if self.config.get("app.auto_start", False):
            self.main_window.show()
        
        # Iniciar loop principal
        try:
            Gtk.main()
        except KeyboardInterrupt:
            self.quit()
    
    def quit(self):
        """Terminar a aplicação"""
        if self.main_window:
            self.main_window.destroy()
        
        if self.tray_icon:
            if hasattr(self.tray_icon, 'indicator'):
                self.tray_icon.indicator.set_status(0)
            elif hasattr(self.tray_icon, 'status_icon'):
                self.tray_icon.status_icon.set_visible(False)
        
        Gtk.main_quit()


def main():
    """Ponto de entrada principal"""
    app = LinuxAIAssistant()
    
    # Manipular sinais para saír corretamente
    signal.signal(signal.SIGINT, lambda s, f: app.quit())
    signal.signal(signal.SIGTERM, lambda s, f: app.quit())
    
    app.run()


if __name__ == "__main__":
    main()
