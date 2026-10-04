import json
import os
import base64
import copy
import atexit
import re
import threading
from pathlib import Path
from typing import Any, Optional, Dict, List
import logging
import sys
import uuid
from dotenv import load_dotenv
from .provider_modes import ASSISTANCE_MODES, validate_local_url, validate_model_name

# Configurar logger
logger = logging.getLogger(__name__)

# Versão única da aplicação (src/_version.py é a fonte; o fallback cobre o
# import como módulo de topo, fora do pacote `src`).
try:
    from ._version import __version__ as _APP_VERSION
except ImportError:  # pragma: no cover - import fora do pacote
    _APP_VERSION = "1.1.0"

# Migrações de configuração versionadas: chave = versão a partir da qual a
# migração se aplica. Executadas por ordem de versão sobre configs antigas
# cujo `app.version` seja inferior. Adicionar novas migrações AQUI e nunca
# alterar retroactivamente as existentes.
_GROQ_OPENAI_BASE = "https://api.groq.com/openai/v1"
_GROQ_LEGACY_BASE = "https://api.groq.com/v1"


def _migrate_groq_openai_base(manager) -> None:
    """Point the untouched Groq default at the OpenAI-compatible base URL.

    A custom base URL is left alone. Only the previous built-in value is
    rewritten, because that value never reached Groq's chat endpoint.
    """
    providers = manager.config.get("api", {})
    if not isinstance(providers, dict):
        return
    providers = providers.get("providers", {})
    groq = providers.get("groq") if isinstance(providers, dict) else None
    if isinstance(groq, dict) and groq.get("base_url") == _GROQ_LEGACY_BASE:
        groq["base_url"] = _GROQ_OPENAI_BASE


MIGRATIONS: Dict[str, Any] = {
    "1.3.1": _migrate_groq_openai_base,
}


def _version_tuple(version: str):
    """'1.2.3' -> (1, 2, 3); partes não numéricas tratadas como 0."""
    parts = []
    for piece in str(version).split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)

# `set()` is called on every `configure-event`/`size-allocate` event (that is,
# every pixel of dragging/resizing). Without debounce, that would mean
# rewriting the entire config.json hundreds of times per second.
SAVE_DEBOUNCE_SECONDS = 0.5

# A theme name is used to build a file name (see _load_theme), so it is
# restricted to the same simple identifier the "Add Theme" dialog accepts.
_THEME_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENCRYPTED_PREFIX = "fernet:v1:"


class CredentialEncryptionError(ValueError):
    """A credential operation failed without exposing credential contents."""


def _encrypted_payload(value: str) -> Optional[bytes]:
    if value.startswith(_ENCRYPTED_PREFIX):
        return value[len(_ENCRYPTED_PREFIX):].encode("utf-8")
    if value.startswith("gAAAA"):
        return value.encode("utf-8")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
        if decoded.startswith(b"gAAAA"):
            return decoded
    except (ValueError, UnicodeError):
        pass
    # Recognise damaged legacy outer base64 as ciphertext as well.
    if value.startswith("Z0FBQU"):
        return b""
    return None


class ConfigManager:
    """Application configuration manager with optional encryption and theme support"""

    DEFAULT_CONFIG = {
        "app": {
            "name": "Linux AI Assistant",
            "version": _APP_VERSION,
            "width": 400,
            "height": 500,
            "x_position": 100,
            "y_position": 100,
            "opacity": 0.9,
            "always_on_top": True,
            "auto_start": False,
            "theme": "dark",
            "encryption_enabled": False,
            "display_independent_watchdog": False,
            "dock_mode": "float",
            "dock_edge": "right",
            "button_edge": "right",
            "expert_mode": False,
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
                    "model": "gemini-3.5-flash-lite",
                    "timeout": 30
                },
                "anthropic": {
                    "api_key": "",
                    "base_url": "https://api.anthropic.com/v1",
                    "model": "claude-haiku-4-5-20251001",
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
                    "base_url": _GROQ_OPENAI_BASE,
                    "model": "openai/gpt-oss-20b",
                    "timeout": 60
                },
                "cohere": {
                    "api_key": "",
                    "base_url": "https://api.cohere.ai/v1",
                    "model": "command-r-08-2024",
                    "timeout": 60
                },
                "local_llm": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "llama3.2",
                    "timeout": 120,
                    "backend": "ollama",
                    "strict_local": True
                }
            }
        },
        # auto preserves existing configurations: the selected provider is
        # tried when configured, otherwise the built-in assistant is used.
        "assistance": {
            "mode": "auto"
        },
        "features": {
            "screen_capture": True,
            "ocr_enabled": True,
            "expert_mode": True,
            "file_edit": True,
            "system_info": True
        },
        # Plugins são OPT-IN: um plugin é código arbitrário executado no
        # arranque; só os aqui listados (por nome de ficheiro) são carregados.
        "plugins": {
            "enabled": []
        },
        # Context budget sent to the provider on every request.
        "context": {
            "max_messages": 20,
            "max_chars": 12000
        },
        "permissions": {
            "require_sudo": True,
            # Apenas comandos de diagnóstico "puros". Contrato de segurança:
            # nenhum item desta lista pode executar código arbitrário por
            # flags próprias (ex.: `man -P <prog>` corre o pager via shell) —
            # daí a ausência de `man`/interpretadores/pagers. system_utils.py
            # bloqueia adicionalmente argumentos com caminhos absolutos em
            # TODOS estes comandos e flags concretas (ex.: `dig -f`).
            "allowed_commands": [
                "ls", "cat", "grep", "ps", "df", "du", "free", "uname",
                "whoami", "pwd", "date", "cal", "echo", "which", "whereis",
                "ifconfig", "ip", "netstat", "ss", "ping", "traceroute", "dig",
                "nslookup"
            ],
            # Mínimo-privilégio: sem diretórios editáveis por defeito. O
            # utilizador adiciona os que quiser nas Definições; a lista antiga
            # ("/etc", "/home", ...) mantém-se em configs existentes.
            "allowed_edit_dirs": []
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
            "display_independent_watchdog": bool,
            "dock_mode": str,
            "dock_edge": str,
            "button_edge": str,
            "expert_mode": bool,
            "language": str
        },
        "api": {
            "default_provider": str,
            "providers": dict
        },
        "assistance": {
            "mode": str
        },
        "features": {
            "screen_capture": bool,
            "ocr_enabled": bool,
            "expert_mode": bool,
            "file_edit": bool,
            "system_info": bool
        },
        "plugins": {
            "enabled": list
        },
        "context": {
            "max_messages": int,
            "max_chars": int
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
        self._credential_store = None
        self._save_timer = None
        self._dirty = False
        # Último erro persistente de escrita (None se a última gravação
        # teve sucesso). A UI usa-o para avisar quando alterações/keys não
        # chegaram a disco (disco cheio, permissões...).
        self.last_save_error: Optional[str] = None
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
        """Keep encryption enabled on failure; never replace a lost key."""
        if self.get("app.encryption_enabled", False):
            key_path = Path(self.config_path).parent / ".encryption_key"
            try:
                from cryptography.fernet import Fernet
                if key_path.exists():
                    with open(key_path, 'rb') as f:
                        key = f.read(1024)
                    Fernet(key)
                    self._encryption_key = key
                    logger.info("Encryption key loaded")
                else:
                    providers = self.config.get("api", {}).get("providers", {})
                    if any(isinstance(record, dict) and isinstance(record.get("api_key"), str)
                           and _encrypted_payload(record["api_key"]) is not None
                           for record in providers.values()):
                        raise CredentialEncryptionError("The encryption key is missing; restore it before changing credentials.")
                    key = Fernet.generate_key()
                    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as f:
                        f.write(key)
                        f.flush()
                        os.fsync(f.fileno())
                    self._encryption_key = key
                    logger.info("New encryption key generated")
            except Exception:
                self._encryption_key = None
                logger.error("Encryption key is unavailable; encrypted credential writes are blocked")

    def _encrypt_value(self, value: str) -> str:
        """Encrypt value"""
        if not value or not self.get("app.encryption_enabled", False):
            return value
        if not self._encryption_key:
            raise CredentialEncryptionError("Encryption is enabled but its key is unavailable.")
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return _ENCRYPTED_PREFIX + f.encrypt(value.encode()).decode("ascii")
        except Exception:
            logger.error("API key encryption failed; the stored credential was preserved")
            raise CredentialEncryptionError("API key encryption failed; no plaintext was stored.") from None

    def _decrypt_value(self, value: str) -> Optional[str]:
        """Read tagged and legacy Fernet values; never return invalid ciphertext."""
        payload = _encrypted_payload(value)
        if payload is None:
            return value
        if not self._encryption_key:
            logger.error("Cannot decrypt a stored API key; its encryption key is unavailable")
            return None
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._encryption_key)
            return f.decrypt(payload).decode()
        except Exception:
            logger.error("Cannot decrypt a stored API key; treating it as unset")
            return None

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

    def _deep_merge_defaults(self, defaults: Dict[str, Any], target: Dict[str, Any]) -> bool:
        """Merge recursivo de `defaults` sobre `target` (in-place).

        Só acrescenta o que falta: qualquer valor definido pelo utilizador é
        preservado. Garante que secções profundas (ex.: um provider novo em
        `api.providers` acrescentado numa versão futura, ou uma entrada
        individual apagada à mão) reaparecem com os seus defaults — sem isto,
        `_get_api_config()` devolvia `{}` e o pedido ia para
        "None/chat/completions".
        """
        changed = False
        for key, value in defaults.items():
            if isinstance(value, dict):
                child = target.get(key)
                if not isinstance(child, dict):
                    target[key] = copy.deepcopy(value)
                    changed = True
                else:
                    changed = self._deep_merge_defaults(value, child) or changed
            elif key not in target:
                target[key] = copy.deepcopy(value)
                changed = True
        return changed

    def _migrate(self):
        """Aplica migrações versionadas e marca a versão actual.

        Um config antigo (`app.version` em falta ou inferior a _APP_VERSION)
        é trazido à versão corrente depois de passar por todas as migrações
        intermédias, por ordem.
        """
        try:
            current = str(self.config.get("app", {}).get("version", "0.0.0"))
            for version in sorted(MIGRATIONS, key=_version_tuple):
                if _version_tuple(version) > _version_tuple(current):
                    MIGRATIONS[version](self)
                    logger.info("Migration applied: %s", version)
            if current != _APP_VERSION:
                self.config.setdefault("app", {})["version"] = _APP_VERSION
                self.save()
        except Exception as e:
            logger.error(f"Error migrating configuration: {e}")

    def _validate_config(self):
        """Validate configuration against schema"""
        try:
            changed = False
            # Defaults profundos primeiro (secções/providers inteiros que a
            # validação plana abaixo nunca criaria)...
            if self._deep_merge_defaults(self._get_default_config(), self.config):
                changed = True
            # ...e migrações versionadas para configs de versões antigas.
            self._migrate()
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
            if self.config["assistance"]["mode"] not in ASSISTANCE_MODES:
                # Invalid explicit choices must never enable cloud traffic.
                self.config["assistance"]["mode"] = "offline"
                changed = True
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
        if isinstance(default, (list, dict)):
            # Without this, LINUX_AI_PERMISSIONS_ALLOWED_COMMANDS="ls;cat"
            # returned a string and `set(...)` produced a set of characters
            # (breaking the command allowlist), and LINUX_AI_API_PROVIDERS
            # returned a string that crashed _get_api_config(). Environment
            # variables are strings, so list/dict values must be JSON.
            try:
                parsed = json.loads(env_value)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON for %s in %s; keeping the stored value",
                    key, self._env_name(key),
                )
                return default
            if type(parsed) is not type(default):
                logger.warning(
                    "Type mismatch for %s in %s; keeping the stored value",
                    key, self._env_name(key),
                )
                return default
            return parsed
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

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Like `get()`, but ignores environment-variable overrides.

        The Settings dialog uses this to show the value that is actually
        stored in config.json even when an env var is shadowing it.
        """
        value = self.config
        for k in key.split('.'):
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        if "api_key" in key and isinstance(value, str):
            return self._decrypt_value(value)
        return value

    def get_assistance_mode(self) -> str:
        """Return the effective mode, including environment overrides."""
        mode = self.get("assistance.mode", "auto")
        return mode if mode in ASSISTANCE_MODES else "offline"

    def set_assistance_mode(self, mode: str):
        if mode not in ASSISTANCE_MODES:
            raise ValueError("Assistance mode must be auto, offline, local or remote")
        self.set("assistance.mode", mode)

    def get_local_model_settings(self) -> Dict[str, Any]:
        """Effective local settings, without returning unrelated API keys."""
        prefix = "api.providers.local_llm."
        return {
            "base_url": self.get(prefix + "base_url", "http://localhost:11434/v1"),
            "model": self.get(prefix + "model", "llama3.2"),
            "timeout": self.get(prefix + "timeout", 120),
            "backend": self.get(prefix + "backend", "ollama"),
            "strict_local": self.get(prefix + "strict_local", True),
        }

    def set_local_model_settings(self, base_url: str, model: str,
                                 strict_local: bool = True, backend: str = "ollama"):
        """Validate all inputs before persisting a local configuration."""
        normalized, selected = self._validate_assistance_settings(
            self.get_assistance_mode(), base_url, model, strict_local, backend)
        with self._lock:
            self._set_local_model_settings(normalized, selected, strict_local, backend)

    def _validate_assistance_settings(self, mode: str, base_url: str, model: str,
                                      strict_local: bool, backend: str):
        if mode not in ASSISTANCE_MODES:
            raise ValueError("Assistance mode must be auto, offline, local or remote")
        if backend not in {"ollama", "openai"} or not isinstance(strict_local, bool):
            raise ValueError("Invalid local model settings")
        effective_strict = strict_local or mode == "local"
        normalized = validate_local_url(base_url, effective_strict)
        if backend == "ollama" and not normalized.endswith("/v1"):
            normalized += "/v1"
        selected = validate_model_name(model, effective_strict)
        return normalized, selected

    def _set_local_model_settings(self, normalized: str, selected: str,
                                  strict_local: bool, backend: str):
        """Caller holds _lock and has already validated the complete draft."""
        self.set("api.providers.local_llm.base_url", normalized)
        self.set("api.providers.local_llm.model", selected)
        self.set("api.providers.local_llm.strict_local", strict_local)
        self.set("api.providers.local_llm.backend", backend)

    def set_assistance_settings(self, mode: str, base_url: str, model: str,
                                strict_local: bool = True, backend: str = "ollama"):
        """Validate and update one complete draft without a partial mode change.

        A saved mode can be shadowed by LINUX_AI_ASSISTANCE_MODE. Its effective
        policy still applies, so saving a relaxed auto draft cannot enable an
        external local server while an environment override requires local.
        """
        if mode not in ASSISTANCE_MODES:
            raise ValueError("Assistance mode must be auto, offline, local or remote")
        effective_mode = self.get_assistance_mode() if self._env_name("assistance.mode") in os.environ else mode
        validation_mode = "local" if "local" in (mode, effective_mode) else mode
        normalized, selected = self._validate_assistance_settings(
            validation_mode, base_url, model, strict_local, backend)
        with self._lock:
            self._set_local_model_settings(normalized, selected, strict_local, backend)
            self.set_assistance_mode(mode)

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
            encrypting = "api_key" in key and isinstance(value, str) and self.get("app.encryption_enabled", False)
            if not encrypting and keys[-1] in current and current[keys[-1]] == value:
                return

            # Encrypt API keys if needed
            if encrypting:
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

    def flush(self) -> bool:
        """Write immediately if there are pending changes.

        Devolve True se não havia alterações pendentes ou se a escrita teve
        sucesso; False em caso de falha persistente (ver `last_save_error`).
        """
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            if self._dirty:
                return self.save()
        return True

    def save(self) -> bool:
        """Save configuration to file

        Atomic write (temp file + os.replace) so a crash in the middle of
        writing never leaves a truncated config.json. The file is created
        with mode 0600: it holds API keys. `_dirty` is cleared only after
        the write succeeded, otherwise a failed write would be forgotten.

        Devolve True em caso de sucesso. Falhas persistentes (disco cheio,
        permissões) são registadas em `last_save_error` — não engolidas —
        para que a UI/atexit possam avisar o utilizador.
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
                self.last_save_error = None
                logger.debug(f"Configuration saved to {self.config_path}")
                return True
            except (IOError, OSError, TypeError, ValueError) as e:
                self.last_save_error = str(e)
                logger.error(f"Error saving configuration: {e}")
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                return False

    def reload(self):
        """Reload configuration from file

        Sob lock: `_validate_config` muta `self.config` in-place; sem o lock,
        leitores concorrentes podiam observar um estado parcialmente
        validado.
        """
        with self._lock:
            self._credential_store = None
            self._load_config()
            self._validate_config()
        logger.info("Configuration reloaded")

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a specific provider"""
        override = self.get_api_key_env_override(provider)
        if override is not None:
            return os.environ[override]
        from .credential_store import CredentialStoreError
        try:
            if self.get_api_key_storage(provider) == "secret-service":
                return self._api_key_store().lookup(provider)
        except CredentialStoreError:
            logger.warning("The API key storage is unavailable, locked or invalid for %s", provider)
            return None
        return self.get_config_value(f"api.providers.{provider}.api_key")

    def set_api_key(self, provider: str, api_key: str):
        """Set API key for a provider"""
        if not isinstance(api_key, str):
            raise ValueError("API keys must be text.")
        if self.get_api_key_storage(provider) == "secret-service":
            store = self._api_key_store()
            if api_key:
                store.store(provider, api_key)
            else:
                store.delete(provider)
        else:
            self.set(f"api.providers.{provider}.api_key", api_key)
        logger.info(f"API key updated for {provider}")

    def get_api_key_env_override(self, provider: str) -> Optional[str]:
        """Name of the environment variable overriding `provider`'s key.

        Environment variables take precedence over config.json, so when one
        is set the value typed in Settings has no effect. Returning the name
        lets the UI tell the user instead of silently keeping the old key.
        """
        canonical = self._env_name(f"api.providers.{provider}.api_key")
        if os.environ.get(canonical) is not None:
            return canonical
        legacy_names = {
            "openrouter": "OPENROUTER_API_KEY",
            "google_ai_studio": "GOOGLE_AI_STUDIO_KEY",
        }
        legacy_name = legacy_names.get(provider)
        if legacy_name and os.environ.get(legacy_name):
            return legacy_name
        return None

    def get_stored_api_key(self, provider: str) -> str:
        """The key persisted in config.json, ignoring env overrides."""
        if self.get_api_key_storage(provider) == "secret-service":
            return self._api_key_store().lookup(provider) or ""
        value = self.get_config_value(f"api.providers.{provider}.api_key")
        if value is None and self.config.get('api', {}).get('providers', {}).get(provider, {}).get('api_key'):
            raise CredentialEncryptionError("The stored API key cannot be decrypted.")
        return value or ""

    def get_api_key_storage(self, provider: str) -> str:
        value = self.get_config_value(f"api.providers.{provider}.key_storage", "config")
        if value not in ("config", "secret-service"):
            from .credential_store import CredentialStoreError
            raise CredentialStoreError("invalid")
        return value

    def _api_key_store(self, create_profile: bool = False):
        from .credential_store import CredentialStoreError, SecretServiceCredentialStore
        with self._lock:
            if self._credential_store is not None:
                return self._credential_store
            profile = self.get_config_value("app.credential_profile")
            if not profile and create_profile:
                profile = uuid.uuid4().hex
                self.set("app.credential_profile", profile)
                self.save()
                if self.last_save_error:
                    raise CredentialStoreError("failed")
            if not profile:
                raise CredentialStoreError("unavailable")
            self._credential_store = SecretServiceCredentialStore(profile)
            return self._credential_store

    def unlock_api_key_store(self):
        """Explicit user action; ordinary key lookup never unlocks a collection."""
        self._api_key_store(create_profile=True).unlock()

    def set_api_key_storage(self, provider: str, storage: str):
        """Migrate only the stored key, after verifying the destination."""
        from .credential_store import CredentialStoreError
        if storage not in ("config", "secret-service"):
            raise CredentialStoreError("invalid")
        with self._lock:
            current = self.get_api_key_storage(provider)
            if current == storage:
                return
            record = self.config.get("api", {}).get("providers", {}).get(provider)
            if not isinstance(record, dict):
                raise CredentialStoreError("invalid")
            key = self.get_stored_api_key(provider)
            raw = record.get("api_key", "")
            if current == "config" and raw and not key:
                raise CredentialEncryptionError("The stored key cannot be decrypted; migration was cancelled.")
            if storage == "secret-service":
                store = self._api_key_store(create_profile=True)
                if key:
                    store.store(provider, key)
                else:
                    # Verify service/collection access even without a key to copy.
                    store.lookup(provider)
                encoded = ""
            else:
                encoded = self._encrypt_value(key)
            before = dict(record)
            record["key_storage"] = storage
            record["api_key"] = encoded
            self._schedule_save()
            self.save()
            if self.last_save_error:
                record.clear()
                record.update(before)
                self._schedule_save()
                # Retain the verified vault copy; deleting it could lose the key.
                raise CredentialStoreError("failed")

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
        # The name becomes a file name: a value such as "../../etc/some.json"
        # would otherwise read a JSON file outside the themes directories.
        if not theme_name or not _THEME_NAME_RE.match(str(theme_name)):
            logger.warning(f"Invalid theme name ignored: {theme_name!r}")
            return None
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
        with self._lock:
            prior_flag = self.config["app"]["encryption_enabled"]
            providers = self.config["api"]["providers"]
            decrypted = {}
            for provider, record in providers.items():
                if not isinstance(record, dict) or record.get("key_storage", "config") != "config":
                    continue
                raw = record.get("api_key", "")
                if not isinstance(raw, str):
                    raise CredentialEncryptionError("A stored API key is invalid.")
                value = self._decrypt_value(raw)
                if value is None:
                    raise CredentialEncryptionError("A stored API key cannot be decrypted; encryption was not changed.")
                decrypted[provider] = value
            if enable:
                self.config["app"]["encryption_enabled"] = True
                try:
                    if not self._encryption_key:
                        self._load_encryption_key()
                    if not self._encryption_key:
                        raise CredentialEncryptionError("Encryption could not be enabled; no credential was changed.")
                    prepared = {provider: self._encrypt_value(value) for provider, value in decrypted.items()}
                except Exception:
                    self.config["app"]["encryption_enabled"] = prior_flag
                    raise
            else:
                prepared = decrypted
                self.config["app"]["encryption_enabled"] = False
            for provider, value in prepared.items():
                providers[provider]["api_key"] = value
            self._schedule_save()
        logger.info(f"Encryption {'enabled' if enable else 'disabled'}")
