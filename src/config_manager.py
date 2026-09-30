import json
import os
import base64
from pathlib import Path
from typing import Any, Optional, Dict
import logging

# Configurar logger
logger = logging.getLogger(__name__)


class ConfigManager:
    """Gestor de configuração da aplicação com encriptação opcional e suporte a temas"""
    
    DEFAULT_CONFIG = {
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
            "theme": "dark",
            "encryption_enabled": False
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
                    "base_url": "https://generativelanguage.googleapis.com/v1",
                    "model": "gemini-1.5-flash",
                    "timeout": 30
                },
                "anthropic": {
                    "api_key": "",
                    "base_url": "https://api.anthropic.com/v1",
                    "model": "claude-3-haiku",
                    "timeout": 60
                },
                "mistral": {
                    "api_key": "",
                    "base_url": "https://api.mistral.ai/v1",
                    "model": "mistral-small",
                    "timeout": 60
                },
                "groq": {
                    "api_key": "",
                    "base_url": "https://api.groq.com/v1",
                    "model": "llama3-8b-8192",
                    "timeout": 60
                },
                "cohere": {
                    "api_key": "",
                    "base_url": "https://api.cohere.ai/v1",
                    "model": "command",
                    "timeout": 60
                },
                "local_llm": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "llama3.2",
                    "timeout": 120
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
                "ls", "cat", "grep", "ps", "top", "df", "du", "free", "uname", "neofetch",
                "whoami", "pwd", "date", "cal", "echo", "man", "which", "whereis"
            ],
            "allowed_edit_dirs": ["/etc", "/home", "/usr/local", "/opt"]
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
    
    # Schema para validação (simplificado)
    CONFIG_SCHEMA = {
        "app": {
            "width": int,
            "height": int,
            "x_position": int,
            "y_position": int,
            "opacity": float,
            "always_on_top": bool,
            "auto_start": bool,
            "theme": str,
            "encryption_enabled": bool
        },
        "api": {
            "default_provider": str,
            "providers": dict
        },
        "features": {
            "screen_capture": bool,
            "ocr_enabled": bool,
            "expert_mode": bool,
            "file_edit": bool,
            "system_info": bool
        },
        "permissions": {
            "require_sudo": bool,
            "allowed_commands": list,
            "allowed_edit_dirs": list
        },
        "ui": {
            "font_family": str,
            "font_size": int,
            "background_color": str,
            "text_color": str,
            "accent_color": str,
            "border_radius": int
        }
    }
    
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
        self._encryption_key = None
        self._themes_dir = Path(__file__).parent.parent / "themes"
        self._load_config()
        self._validate_config()
    
    def _load_encryption_key(self):
        """Carregar ou gerar chave de encriptação"""
        if self.config.get("app.encryption_enabled", False):
            key_path = Path.home() / ".config" / "linux_ai_assistant" / ".encryption_key"
            if key_path.exists():
                try:
                    with open(key_path, 'rb') as f:
                        self._encryption_key = f.read()
                    logger.info("Chave de encriptação carregada")
                except Exception as e:
                    logger.error(f"Erro a carregar chave de encriptação: {e}")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
            else:
                # Gerar nova chave
                try:
                    from cryptography.fernet import Fernet
                    self._encryption_key = Fernet.generate_key()
                    key_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(key_path, 'wb') as f:
                        f.write(self._encryption_key)
                    logger.info("Nova chave de encriptação gerada")
                except ImportError:
                    logger.warning("cryptography não instalado. Encriptação desativada.")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
                except Exception as e:
                    logger.error(f"Erro a gerar chave de encriptação: {e}")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
    
    def _encrypt_value(self, value: str) -> str:
        """Encriptar valor"""
        if not self.config.get("app.encryption_enabled", False) or not self._encryption_key:
            return value
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return base64.b64encode(f.encrypt(value.encode())).decode()
        except Exception as e:
            logger.error(f"Erro a encriptar valor: {e}")
            return value
    
    def _decrypt_value(self, value: str) -> str:
        """Desencriptar valor"""
        if not self.config.get("app.encryption_enabled", False) or not self._encryption_key:
            return value
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return f.decrypt(base64.b64decode(value.encode())).decode()
        except Exception as e:
            logger.warning(f"Erro a desencriptar valor: {e}")
            return value
    
    def _load_config(self):
        """Carregar configuração do ficheiro"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                logger.info(f"Configuração carregada de {self.config_path}")
            else:
                # Criar configuração default
                self.config = self._get_default_config()
                self.save()
                logger.info("Configuração default criada")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Erro a carregar configuração: {e}")
            self.config = self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Obter configuração default"""
        return self.DEFAULT_CONFIG.copy()
    
    def _validate_config(self):
        """Validar configuração contra schema"""
        try:
            # Validar estrutura básica
            for section, schema in self.CONFIG_SCHEMA.items():
                if section not in self.config:
                    self.config[section] = {}
                    logger.warning(f"Secção {section} não encontada. A criar default.")
                
                for key, expected_type in schema.items():
                    if key in self.config[section]:
                        value = self.config[section][key]
                        if not isinstance(value, expected_type):
                            logger.warning(f"Tipo inválido para {section}.{key}: esperado {expected_type}, obtido {type(value)}")
                            self.config[section][key] = expected_type()
                    else:
                        self.config[section][key] = expected_type()
                        logger.warning(f"Chave {section}.{key} não encontrada. A criar default.")
            
            # Carregar chave de encriptação se necessária
            if self.config.get("app.encryption_enabled", False):
                self._load_encryption_key()
            
            self.save()
        except Exception as e:
            logger.error(f"Erro na validação da configuração: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Obter valor da configuração usando dot notation
        
        Exemplo:
            config.get("app.width") -> 400
            config.get("api.providers.openrouter.api_key") -> "..."
        """
        # Override por variáveis de ambiente
        env_var = key.upper().replace(".", "_").replace("-", "_")
        env_value = os.environ.get(f"LINUX_AI_{env_var}")
        if env_value is not None:
            # Converter tipo se necessário
            if isinstance(default, bool):
                return env_value.lower() in ('true', '1', 't', 'y', 'yes')
            elif isinstance(default, int):
                try:
                    return int(env_value)
                except ValueError:
                    pass
            elif isinstance(default, float):
                try:
                    return float(env_value)
                except ValueError:
                    pass
            return env_value
        
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        # Desencriptar API keys se necessário
        if "api_key" in key and isinstance(value, str):
            return self._decrypt_value(value)
        
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
        
        # Encriptar API keys se necessário
        if "api_key" in key and isinstance(value, str) and self.config.get("app.encryption_enabled", False):
            value = self._encrypt_value(value)
        
        current[keys[-1]] = value
        self.save()
        logger.debug(f"Configuração atualizada: {key} = {value}")
    
    def save(self):
        """Guardar configuração no ficheiro"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            logger.info(f"Configuração guardada em {self.config_path}")
        except IOError as e:
            logger.error(f"Erro a guardar configuração: {e}")
    
    def reload(self):
        """Recarregar configuração do ficheiro"""
        self._load_config()
        self._validate_config()
        logger.info("Configuração recarregada")
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Obter API key para um provedor específico"""
        return self.get(f"api.providers.{provider}.api_key")
    
    def set_api_key(self, provider: str, api_key: str):
        """Definir API key para um provedor"""
        self.set(f"api.providers.{provider}.api_key", api_key)
        logger.info(f"API key atualizada para {provider}")
    
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
        logger.debug(f"Geometria da janela atualizada: {width}x{height} @ ({x},{y})")
    
    def get_theme_colors(self) -> Dict[str, str]:
        """Obter cores do tema"""
        # Verificar se há tema customizado
        theme_name = self.get("app.theme", "dark")
        custom_theme = self._load_theme(theme_name)
        
        if custom_theme:
            return {
                "background": custom_theme.get("colors", {}).get("background", "#1e1e1e"),
                "text": custom_theme.get("colors", {}).get("text", "#e0e0e0"),
                "accent": custom_theme.get("colors", {}).get("accent", "#4CAF50"),
                "secondary": custom_theme.get("colors", {}).get("secondary", "#2d2d2d"),
                "tertiary": custom_theme.get("colors", {}).get("tertiary", "#252525")
            }
        else:
            return {
                "background": self.get("ui.background_color", "#1e1e1e"),
                "text": self.get("ui.text_color", "#e0e0e0"),
                "accent": self.get("ui.accent_color", "#4CAF50"),
                "secondary": "#2d2d2d",
                "tertiary": "#252525"
            }
    
    def _load_theme(self, theme_name: str) -> Optional[Dict]:
        """Carregar tema do ficheiro"""
        try:
            # Procurar em temas customizados do utilizador
            user_themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            user_theme_file = user_themes_dir / f"{theme_name}.json"
            
            if user_theme_file.exists():
                with open(user_theme_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            # Procurar em temas da aplicação
            app_theme_file = self._themes_dir / f"{theme_name}.json"
            if app_theme_file.exists():
                with open(app_theme_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            logger.warning(f"Tema não encontrado: {theme_name}")
            return None
            
        except Exception as e:
            logger.error(f"Erro a carregar tema {theme_name}: {e}")
            return None
    
    def get_available_themes(self) -> List[str]:
        """Obter lista de temas disponíveis"""
        themes = []
        
        # Temas da aplicação
        if self._themes_dir.exists():
            for theme_file in self._themes_dir.glob("*.json"):
                themes.append(theme_file.stem)
        
        # Temas do utilizador
        user_themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
        if user_themes_dir.exists():
            for theme_file in user_themes_dir.glob("*.json"):
                if theme_file.stem not in themes:
                    themes.append(theme_file.stem)
        
        return sorted(themes)
    
    def get_theme_info(self, theme_name: str) -> Optional[Dict]:
        """Obter informação sobre um tema"""
        theme = self._load_theme(theme_name)
        if theme:
            return {
                "name": theme.get("name", theme_name),
                "description": theme.get("description", ""),
                "colors": theme.get("colors", {}),
                "ui": theme.get("ui", {})
            }
        return None
    
    def enable_encryption(self, enable: bool = True):
        """Ativar/desativar encriptação de API keys"""
        if enable:
            # Gerar chave se não existir
            if not self._encryption_key:
                self._load_encryption_key()
            self.set("app.encryption_enabled", True)
            # Re-encriptar todas as API keys
            for provider in self.get("api.providers", {}).keys():
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    self.set(f"api.providers.{provider}.api_key", api_key)
        else:
            # Desencriptar todas as API keys antes de desativar
            for provider in self.get("api.providers", {}).keys():
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    self.set(f"api.providers.{provider}.api_key", api_key)
            self.set("app.encryption_enabled", False)
        logger.info(f"Encriptação {'ativada' if enable else 'desativada'}")
