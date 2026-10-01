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
                    "model": "google/gemini-2.5-flash",
                    "timeout": 30
                },
                "google_ai_studio": {
                    "api_key": "",
                    "base_url": "https://generativelanguage.googleapis.com/v1",
                    "model": "gemini-2.5-flash",
                    "timeout": 30
                },
                "anthropic": {
                    "api_key": "",
                    "base_url": "https://api.anthropic.com/v1",
                    "model": "claude-3-5-haiku-latest",
                    "timeout": 60
                },
                "mistral": {
                    "api_key": "",
                    "base_url": "https://api.mistral.ai/v1",
                    "model": "mistral-small-latest",
                    "timeout": 60
                },
                "groq": {
                    "api_key": "",
                    "base_url": "https://api.groq.com/v1",
                    "model": "llama-3.1-8b-instant",
                    "timeout": 60
                },
                "cohere": {
                    "api_key": "",
                    "base_url": "https://api.cohere.ai/v1",
                    "model": "command-r",
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
                "ls", "cat", "grep", "ps", "df", "du", "free", "uname", "neofetch",
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
            "encryption_enabled": bool,
            "dock_mode": str,
            "dock_edge": str,
            "button_edge": str,
            "language": str
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
        # set()/save()/flush() may run from the GTK main thread, worker
        # threads and the debounce Timer at the same time.
        self._lock = threading.RLock()
        self._themes_dir = Path(__file__).parent.parent / "themes"
        if not self._themes_dir.is_dir():
            self._themes_dir = Path(sys.prefix) / "share" / "linux-ai-assistant" / "themes"
        self._load_config()
        self._validate_config()
        # Ensure a scheduled change is not lost on exit
        atexit.register(self.flush)
    
    def _load_encryption_key(self):
        """Load or generate encryption key"""
        if self.get("app.encryption_enabled", False):
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
                    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, 'wb') as f:
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
        if not self.get("app.encryption_enabled", False) or not self._encryption_key:
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
        if not self.get("app.encryption_enabled", False) or not self._encryption_key:
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
                    data = json.load(f)
                if not isinstance(data, dict):
                    # json.load accepts lists/strings/numbers; those would
                    # break every later section lookup.
                    raise ValueError(
                        f"Configuration root must be a JSON object, got {type(data).__name__}"
                    )
                self.config = data
                logger.info(f"Configuration loaded from {self.config_path}")
            else:
                # Create default configuration
                self.config = self._get_default_config()
                self.save()
                logger.info("Default configuration created")
        except (json.JSONDecodeError, IOError, OSError, ValueError) as e:
            logger.error(f"Error loading configuration: {e}")
            self.config = self._get_default_config()
            # Persist the repaired defaults. Previously the corrupt file stayed
            # on disk and _validate_config() found nothing to fix (`changed`
            # was False), so every start silently reset the configuration.
            # Keep a backup of the unreadable file for manual recovery.
            try:
                backup_path = Path(self.config_path).with_name(
                    Path(self.config_path).name + ".corrupt"
                )
                if os.path.exists(self.config_path):
                    os.replace(self.config_path, backup_path)
                    logger.warning(f"Corrupt configuration moved to {backup_path}")
            except OSError as backup_error:
                logger.warning(f"Could not back up corrupt configuration: {backup_error}")
            self.save()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration

        `copy.deepcopy` is required: a shallow copy would share the internal
        dicts with DEFAULT_CONFIG, causing an API key written in one instance
        to appear in other instances and contaminate the class constant.
        """
        return copy.deepcopy(self.DEFAULT_CONFIG)
    
    def _default_value(self, section: str, key: str, expected_type):
        """Real default for `section.key` (falls back to the schema type).

        Using `expected_type()` alone produced broken values: `float()` is
        0.0 (invisible window for app.opacity), `str()` is "" (no theme)...
        """
        section_defaults = self.DEFAULT_CONFIG.get(section, {})
        if key in section_defaults:
            return copy.deepcopy(section_defaults[key])
        return expected_type()

    def _validate_config(self):
        """Validate configuration against schema"""
        try:
            changed = False
            # Validate basic structure
            for section, schema in self.CONFIG_SCHEMA.items():
                if section not in self.config or not isinstance(self.config[section], dict):
                    self.config[section] = {}
                    changed = True
                    logger.warning(f"Section {section} not found. Creating default.")
                
                for key, expected_type in schema.items():
                    if key in self.config[section]:
                        value = self.config[section][key]
                        if not isinstance(value, expected_type):
                            logger.warning(f"Invalid type for {section}.{key}: expected {expected_type}, got {type(value)}")
                            self.config[section][key] = self._default_value(section, key, expected_type)
                            changed = True
                    else:
                        self.config[section][key] = self._default_value(section, key, expected_type)
                        changed = True
                        logger.warning(f"Key {section}.{key} not found. Creating default.")
            
            # Load encryption key if needed
            if self.get("app.encryption_enabled", False):
                self._load_encryption_key()
            
            # Only touch the file when validation actually repaired something
            if changed:
                self.save()
        except Exception as e:
            logger.error(f"Error validating configuration: {e}")
    
    @staticmethod
    def _env_name(key: str) -> str:
        """Canonical LINUX_AI_* environment variable name for a config key."""
        return "LINUX_AI_" + key.upper().replace(".", "_").replace("-", "_")

    def _coerce_env_value(self, key: str, env_value: str, default: Any) -> Any:
        """Coerce a string environment override to the right type.

        The caller's `default` is used when given; otherwise the type of the
        value already stored in the configuration is used, so an override of
        e.g. `app.width` stays an int instead of silently becoming a string.
        """
        if default is None:
            current = self.config
            for part in key.split('.'):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    current = None
                    break
            default = current
        if isinstance(default, bool):
            return env_value.lower() in ('true', '1', 't', 'y', 'yes')
        if isinstance(default, int):
            try:
                return int(env_value)
            except ValueError:
                return env_value
        if isinstance(default, float):
            try:
                return float(env_value)
            except ValueError:
                return env_value
        return env_value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.

        Example:
            config.get("app.width") -> 400
            config.get("api.providers.openrouter.api_key") -> "..."
        """
        # Override by environment variables
        env_value = os.environ.get(self._env_name(key))
        if env_value is not None:
            return self._coerce_env_value(key, env_value, default)
        
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
        with self._lock:
            current = self.config

            for i, k in enumerate(keys[:-1]):
                if k not in current:
                    current[k] = {}
                if not isinstance(current[k], dict):
                    raise ValueError(
                        f"Cannot set '{key}': '{'.'.join(keys[:i + 1])}' is not a section"
                    )
                current = current[k]

            # No-op writes are the common case while dragging/resizing the
            # window (configure-event fires per pixel): without this check
            # every call re-armed the debounce Timer, so a single drag could
            # schedule hundreds of writes.
            if keys[-1] in current and current[keys[-1]] == value:
                return

            # Encrypt API keys if needed
            if "api_key" in key and isinstance(value, str) and self.get("app.encryption_enabled", False):
                value = self._encrypt_value(value)
                # `value` above is freshly encrypted (new IV), so the
                # equality shortcut cannot apply to encrypted keys.

            current[keys[-1]] = value
            self._schedule_save()
        logger.debug("Configuration updated: %s", key)

    def _schedule_save(self):
        """Schedule a write (debounce).

        Consecutive calls within SAVE_DEBOUNCE_SECONDS result in a single
        write. `save()` remains available for those who need immediate
        persistence. Caller must hold `self._lock`.
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
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            if self._dirty:
                self.save()

    def save(self):
        """Save configuration to file

        Atomic write (temp file + os.replace) so a crash in the middle of
        writing never leaves a truncated config.json. The file is created
        with mode 0600: it holds API keys. `_dirty` is cleared only after
        the write succeeded, otherwise a failed write would be forgotten.
        """
        temp_path = None
        with self._lock:
            try:
                target = Path(self.config_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(target.parent, 0o700)
                except OSError:
                    pass
                temp_path = target.with_name(target.name + f".tmp{os.getpid()}")
                fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(copy.deepcopy(self.config), f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                self._dirty = False
                logger.debug(f"Configuration saved to {self.config_path}")
            except (IOError, OSError, TypeError, ValueError) as e:
                logger.error(f"Error saving configuration: {e}")
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
    
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
        legacy_name = legacy_names.get(provider)
        if legacy_name and os.environ.get(legacy_name):
            return os.environ[legacy_name]
        # get() already resolves the canonical LINUX_AI_* variable
        return self.get(f"api.providers.{provider}.api_key")
    
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
            # The flag must be on *before* _load_encryption_key() and the
            # re-encryption loop: both are gated on it.
            self.set("app.encryption_enabled", True)
            if not self._encryption_key:
                self._load_encryption_key()
            if not self._encryption_key:
                # cryptography is unavailable/unusable - keep keys in plaintext
                self.set("app.encryption_enabled", False)
                logger.warning("Encryption could not be enabled (missing key)")
                return
            # Re-encrypt every stored API key
            for provider in list(self.get("api.providers", {}).keys()):
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    self.set(f"api.providers.{provider}.api_key", api_key)
        else:
            # Read (and decrypt) the keys while encryption is still enabled...
            decrypted = {}
            for provider in list(self.get("api.providers", {}).keys()):
                api_key = self.get(f"api.providers.{provider}.api_key")
                if api_key:
                    decrypted[provider] = api_key
            # ...then disable the flag so set() stores them in plaintext.
            # (Writing them before flipping the flag would re-encrypt them
            # and leave ciphertext behind a disabled-encryption config.)
            self.set("app.encryption_enabled", False)
            for provider, api_key in decrypted.items():
                self.set(f"api.providers.{provider}.api_key", api_key)
        logger.info(f"Encryption {'enabled' if enable else 'disabled'}")
