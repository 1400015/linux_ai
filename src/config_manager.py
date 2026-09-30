import json
import os
from pathlib import Path
from typing import Any, Optional, Dict


class ConfigManager:
    """Gestor de configuração da aplicação"""
    
    def __init__(self, config_path: str = None):
        """
        Inicializar o gestor de configuração
        
        Args:
            config_path: Caminho para o ficheiro de configuração
        """
        if config_path is None:
            # Caminho default: ~/.config/linux_ai_assistant/config.json
            config_dir = Path.home() / ".config" / "linux_ai_assistant"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = str(config_dir / "config.json")
        
        self.config_path = config_path
        self.config = {}
        self._load_config()
    
    def _load_config(self):
        """Carregar configuração do ficheiro"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            else:
                # Criar configuração default
                self.config = self._get_default_config()
                self.save()
        except (json.JSONDecodeError, IOError) as e:
            print(f"Erro a carregar configuração: {e}")
            self.config = self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Obter configuração default"""
        return {
            "app": {
                "name": "Linux AI Assistant",
                "version": "1.0.0",
                "width": 400,
                "height": 500,
                "x_position": 100,
                "y_position": 100,
                "opacity": 0.9,
                "always_on_top": True,
                "auto_start": False,
                "theme": "dark"
            },
            "api": {
                "default_provider": "openrouter",
                "providers": {
                    "openrouter": {
                        "api_key": "",
                        "base_url": "https://openrouter.ai/api/v1",
                        "model": "google/gemini-flash-1.5",
                        "timeout": 30
                    },
                    "google_ai_studio": {
                        "api_key": "",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta",
                        "model": "gemini-1.5-flash",
                        "timeout": 30
                    },
                    "local_llm": {
                        "base_url": "http://localhost:11434/v1",
                        "model": "llama3.2",
                        "timeout": 60
                    }
                }
            },
            "features": {
                "screen_capture": True,
                "ocr_enabled": True,
                "expert_mode": True,
                "file_edit": True,
                "system_info": True
            },
            "permissions": {
                "require_sudo": True,
                "allowed_commands": [
                    "ls", "cat", "grep", "ps", "top", "df", "du", "free", "uname", "neofetch"
                ],
                "allowed_edit_dirs": ["/etc", "/home", "/usr/local"]
            },
            "ui": {
                "font_family": "Monospace",
                "font_size": 12,
                "background_color": "#1e1e1e",
                "text_color": "#e0e0e0",
                "accent_color": "#4CAF50",
                "border_radius": 10
            }
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Obter valor da configuração usando dot notation
        
        Exemplo:
            config.get("app.width") -> 400
            config.get("api.providers.openrouter.api_key") -> "..."
        """
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """
        Definir valor na configuração usando dot notation
        
        Args:
            key: Chave em dot notation (ex: "app.width")
            value: Valor a definir
        """
        keys = key.split('.')
        current = self.config
        
        for i, k in enumerate(keys[:-1]):
            if k not in current:
                current[k] = {}
            current = current[k]
        
        current[keys[-1]] = value
        self.save()
    
    def save(self):
        """Guardar configuração no ficheiro"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Erro a guardar configuração: {e}")
    
    def reload(self):
        """Recarregar configuração do ficheiro"""
        self._load_config()
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Obter API key para um provedor específico"""
        return self.get(f"api.providers.{provider}.api_key")
    
    def set_api_key(self, provider: str, api_key: str):
        """Definir API key para um provedor"""
        self.set(f"api.providers.{provider}.api_key", api_key)
    
    def get_window_geometry(self) -> Dict[str, int]:
        """Obter geometria da janela"""
        return {
            "width": self.get("app.width", 400),
            "height": self.get("app.height", 500),
            "x": self.get("app.x_position", 100),
            "y": self.get("app.y_position", 100)
        }
    
    def set_window_geometry(self, width: int, height: int, x: int, y: int):
        """Definir geometria da janela"""
        self.set("app.width", width)
        self.set("app.height", height)
        self.set("app.x_position", x)
        self.set("app.y_position", y)
    
    def get_theme_colors(self) -> Dict[str, str]:
        """Obter cores do tema"""
        return {
            "background": self.get("ui.background_color", "#1e1e1e"),
            "text": self.get("ui.text_color", "#e0e0e0"),
            "accent": self.get("ui.accent_color", "#4CAF50")
        }
