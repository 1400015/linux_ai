import json
import os
import base64
import copy
import atexit
import threading
from pathlib import Path
from typing import Any, Optional, Dict, List
import logging
import sys
from dotenv import load_dotenv

# Configurar logger
logger = logging.getLogger(__name__)

# `set()` is called on every `configure-event`/`size-allocate` event (that is,
# every pixel of dragging/resizing). Without debounce, that would mean
# rewriting the entire config.json hundreds of times per second.
SAVE_DEBOUNCE_SECONDS = 0.5


class ConfigManager:
    """Application configuration manager with optional encryption and theme support"""
    
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
            "encryption_enabled": False,
            "dock_mode": "float",
            "dock_edge": "right",
            "button_edge": "right",
            "language": ""
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
    
    # Schema for validation (simplified)
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
        Initialize the configuration manager
        
        Args:
            config_path: Path to the configuration file
        """
        if config_path is None:
            # Default path: ~/.config/linux_ai_assistant/config.json
            config_dir = Path.home() / ".config" / "linux_ai_assistant"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = str(config_dir / "config.json")
        
        load_dotenv(Path(config_path).parent / ".env", override=False)
        self.config_path = config_path
        self.config = {}
        self._encryption_key = None
        self._save_timer = None
        self._dirty = False
        self._themes_dir = Path(__file__).parent.parent / "themes"
        if not self._themes_dir.is_dir():
            self._themes_dir = Path(sys.prefix) / "share" / "linux-ai-assistant" / "themes"
        self._load_config()
        self._validate_config()
        # Ensure a scheduled change is not lost on exit
        atexit.register(self.flush)
    
    def _load_encryption_key(self):
        """Load or generate encryption key"""
        if self.config.get("app.encryption_enabled", False):
            key_path = Path.home() / ".config" / "linux_ai_assistant" / ".encryption_key"
            if key_path.exists():
                try:
                    with open(key_path, 'rb') as f:
                        self._encryption_key = f.read()
                    logger.info("Encryption key loaded")
                except Exception as e:
                    logger.error(f"Error loading encryption key: {e}")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
            else:
                # Generate new key
                try:
                    from cryptography.fernet import Fernet
                    self._encryption_key = Fernet.generate_key()
                    key_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(key_path, 'wb') as f:
                        f.write(self._encryption_key)
                    logger.info("New encryption key generated")
                except ImportError:
                    logger.warning("cryptography not installed. Encryption disabled.")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
                except Exception as e:
                    logger.error(f"Error generating encryption key: {e}")
                    self.config["app"]["encryption_enabled"] = False
                    self.save()
    
    def _encrypt_value(self, value: str) -> str:
        """Encrypt value"""
        if not self.config.get("app.encryption_enabled", False) or not self._encryption_key:
            return value
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return base64.b64encode(f.encrypt(value.encode())).decode()
        except Exception as e:
            logger.error(f"Error encrypting value: {e}")
            return value
    
    def _decrypt_value(self, value: str) -> str:
        """Decrypt value"""
        if not self.config.get("app.encryption_enabled", False) or not self._encryption_key:
            return value
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return f.decrypt(base64.b64decode(value.encode())).decode()
        except Exception as e:
            logger.warning(f"Error decrypting value: {e}")
            return value
    
    def _load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                logger.info(f"Configuration loaded from {self.config_path}")
            else:
                # Create default configuration
                self.config = self._get_default_config()
                self.save()
                logger.info("Default configuration created")
                self._dirty = False
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading configuration: {e}")
            self.config = self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration

        `copy.deepcopy` is required: a shallow copy would share the internal
        dicts with DEFAULT_CONFIG, causing an API key written in one instance
        to appear in other instances and contaminate the class constant.
        """
        return copy.deepcopy(self.DEFAULT_CONFIG)
    
    def _validate_config(self):
        """Validate configuration against schema"""
        try:
            # Validate basic structure
            for section, schema in self.CONFIG_SCHEMA.items():
                if section not in self.config:
                    self.config[section] = {}
                    logger.warning(f"Section {section} not found. Creating default.")
                
                for key, expected_type in schema.items():
                    if key in self.config[section]:
                        value = self.config[section][key]
                        if not isinstance(value, expected_type):
                            logger.warning(f"Invalid type for {section}.{key}: expected {expected_type}, got {type(value)}")
                            self.config[section][key] = expected_type()
                    else:
                        self.config[section][key] = expected_type()
                        logger.warning(f"Key {section}.{key} not found. Creating default.")
            
            # Load encryption key if needed
            if self.config.get("app.encryption_enabled", False):
                self._load_encryption_key()
            
            self.save()
            self._dirty = False
        except Exception as e:
            logger.error(f"Error validating configuration: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.

        Example:
            config.get("app.width") -> 400
            config.get("api.providers.openrouter.api_key") -> "..."
        """
        # Override by environment variables
        env_var = key.upper().replace(".", "_").replace("-", "_")
        env_value = os.environ.get(f"LINUX_AI_{env_var}")
        if env_value is not None:
            # Convert type if needed
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
        
        # Decrypt API keys if needed
        if "api_key" in key and isinstance(value, str):
            return self._decrypt_value(value)
        
        return value
    
    def set(self, key: str, value: Any):
        """
        Set a configuration value using dot notation.

        Args:
            key: Key in dot notation (e.g. "app.width").
            value: Value to set.
        """
        keys = key.split('.')
        current = self.config
        
        for i, k in enumerate(keys[:-1]):
            if k not in current:
                current[k] = {}
            current = current[k]
        
        # Encrypt API keys if needed
        if "api_key" in key and isinstance(value, str) and self.config.get("app.encryption_enabled", False):
            value = self._encrypt_value(value)
        
        current[keys[-1]] = value
        self._schedule_save()
        logger.debug("Configuration updated: %s", key)

    def _schedule_save(self):
        """Schedule a write (debounce).

        Consecutive calls within SAVE_DEBOUNCE_SECONDS result in a single
        write. `save()` remains available for those who need immediate
        persistence.
        """
        self._dirty = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self.flush)
        timer.daemon = True
        self._save_timer = timer
        timer.start()

    def flush(self):
        """Write immediately if there are pending changes."""
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._dirty:
            self.save()

    def save(self):
        """Save configuration to file

        Atomic write (temp file + os.replace) so a crash in the middle of
        writing never leaves a truncated config.json.
        """
        try:
            self._dirty = False
            target = Path(self.config_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target.with_name(target.name + f".tmp{os.getpid()}")
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target)
            logger.debug(f"Configuration saved to {self.config_path}")
        except (IOError, OSError) as e:
            logger.error(f"Error saving configuration: {e}")
    
    def reload(self):
        """Reload configuration from file"""
        self._load_config()
        self._validate_config()
        logger.info("Configuration reloaded")
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a specific provider"""
        legacy_names = {
            "openrouter": "OPENROUTER_API_KEY",
            "google_ai_studio": "GOOGLE_AI_STUDIO_KEY",
        }
        key = f"api.providers.{provider}.api_key"
        env_name = "LINUX_AI_" + key.upper().replace(".", "_")
        if os.environ.get(env_name):
            return os.environ[env_name]
        legacy_name = legacy_names.get(provider)
        if legacy_name and os.environ.get(legacy_name):
            return os.environ[legacy_name]
        return self.get(key)
    
    def set_api_key(self, provider: str, api_key: str):
        """Set API key for a provider"""
        self.set(f"api.providers.{provider}.api_key", api_key)
        logger.info(f"API key updated for {provider}")
    
    def get_window_geometry(self) -> Dict[str, int]:
        """Get window geometry"""
        return {
            "width": self.get("app.width", 400),
            "height": self.get("app.height", 500),
            "x": self.get("app.x_position", 100),
            "y": self.get("app.y_position", 100)
        }
    
    def set_window_geometry(self, width: int, height: int, x: int, y: int):
        """Set window geometry"""
        self.set("app.width", width)
        self.set("app.height", height)
        self.set("app.x_position", x)
        self.set("app.y_position", y)
        logger.debug(f"Window geometry updated: {width}x{height} @ ({x},{y})")
    
    def get_theme_colors(self) -> Dict[str, str]:
        """Get theme colors"""
        # Check if there are custom themes
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
        """Load theme from file"""
        try:
            # Search in user's custom themes
            user_themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
            user_theme_file = user_themes_dir / f"{theme_name}.json"
            
            if user_theme_file.exists():
                with open(user_theme_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            # Search in application themes
            app_theme_file = self._themes_dir / f"{theme_name}.json"
            if app_theme_file.exists():
                with open(app_theme_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            logger.warning(f"Theme not found: {theme_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error loading theme {theme_name}: {e}")
            return None
    
    def get_available_themes(self) -> List[str]:
        """Get list of available themes"""
        themes = []
        
        # Application themes
        if self._themes_dir.exists():
            for theme_file in self._themes_dir.glob("*.json"):
                themes.append(theme_file.stem)
        
        # User themes
        user_themes_dir = Path.home() / ".config" / "linux_ai_assistant" / "themes"
        if user_themes_dir.exists():
            for theme_file in user_themes_dir.glob("*.json"):
                if theme_file.stem not in themes:
                    themes.append(theme_file.stem)
        
        return sorted(themes)
    
    def get_theme_info(self, theme_name: str) -> Optional[Dict]:
        """Get information about a theme"""
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
        """Enable/disable API key encryption"""
        if enable:
            # Generate key if it does not exist
            if not self._encryption_key:
                self._load_encryption_key()
            self.set("app.encryption_enabled", True)
            # Re-encrypt all API keys
            for provider in self.get("api.providers", {}).keys():
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    self.set(f"api.providers.{provider}.api_key", api_key)
        else:
            # Decrypt all API keys before disabling
            for provider in self.get("api.providers", {}).keys():
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    self.set(f"api.providers.{provider}.api_key", api_key)
            self.set("app.encryption_enabled", False)
        logger.info(f"Encryption {'enabled' if enable else 'disabled'}")
