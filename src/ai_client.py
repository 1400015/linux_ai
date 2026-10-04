import json
import copy
import time
import atexit
import logging
import math
import re
import threading
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from .storage import JsonWriteCommittedError, update_json
from .stream_events import iter_sse_json
from .provider_modes import (
    ASSISTANCE_MODES, LocalModelError, ProviderStatus, discover_local_models,
    make_local_request, model_is_installed, validate_local_url, validate_model_name,
)
from requests.exceptions import RequestException, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError

try:
    from ._version import __version__ as _APP_VERSION
except ImportError:  # pragma: no cover - import fora do pacote
    _APP_VERSION = "1.0.0"

# Configurar logger
logger = logging.getLogger(__name__)

# usage.json is rewritten (with fsync) on every request. Batching the writes
# keeps the I/O out of the request path; a pending change is always flushed at
# exit, and `reset_token_usage()` still writes immediately.
USAGE_SAVE_DEBOUNCE_SECONDS = 5.0

_SECRET_QUERY_RE = re.compile(
    r"([?&](?:key|api_key|apikey|access_token|token|sig|signature|password|auth)=)[^&\s]+",
    re.I,
)


class AIProviderError(RequestException):
    """Falha ESTRUTURADA de provider (rede, HTTP, config) — não é conteúdo.

    Contrato: erros de comunicação NUNCA viajam como chunks de texto do
    modelo. Herda de RequestException para preservar a política de retry
    (`_is_transient` inspeciona `.response`) e para que a UI/CLI possam
    apanhar a falha por tipo em vez de adivinhar por prefixos de string
    (`_is_api_failure` por startswith perdia erros a meio do stream e
    disparava o fallback offline para respostas legítimas que começassem
    por "Error:").
    """


class ProviderNotConfigured(AIProviderError):
    """Provider sem chave/base_url configurados — permanente, sem retry."""


class _RetryAfterRateLimit(RequestException):
    """Sinaliza um 429 que deve ser repetido depois de `Retry-After`.

    Herda de RequestException para que a política `NETWORK_RETRY` a volte a
    tentar (até 3 tentativas), em vez de se propagar imediatamente.
    `.response` fica a None: `_is_transient` trata-o pelo isinstance abaixo.
    """


def _is_transient(exc: BaseException) -> bool:
    """Retry only failures that can succeed on a later attempt.

    Permanent client errors (401 invalid key, 404 unknown model, 400 bad
    payload) must fail fast instead of burning 3 attempts x backoff.
    """
    if isinstance(exc, _RetryAfterRateLimit):
        return True
    if isinstance(exc, (Timeout, RequestsConnectionError)):
        return True
    if isinstance(exc, RequestException):
        response = getattr(exc, "response", None)
        return response is not None and response.status_code >= 500
    return False


# Politica de retry para falhas de rede/transientes
NETWORK_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)


def redact_url(url: str) -> str:
    """Remove segredos de query strings antes de escrever no log."""
    if not url:
        return url
    return _SECRET_QUERY_RE.sub(r"\1***", url)


class AIClient:
    """Client to interact with several AI APIs"""

    # Constants
    DEFAULT_TIMEOUT = 30
    RATE_LIMIT_WAIT = 15  # seconds (when no valid Retry-After header arrives)
    # Cap quando o header Retry-After é válido: o cap antigo (15 s) cortava
    # o valor real e queimava as 3 tentativas contra um limiar que ainda não
    # tinha reposto. 60 s continua a não bloquear o worker durante horas.
    RATE_LIMIT_WAIT_MAX = 60

    # Providers embutidos: exigem base_url na config (o deep-merge do
    # ConfigManager garante-a; a verificação protege configs corrompidas).
    BUILTIN_PROVIDERS = frozenset([
        "openrouter", "google_ai_studio", "anthropic", "mistral",
        "groq", "cohere", "local_llm",
    ])

    # Supported providers
    SUPPORTED_PROVIDERS = [
        "openrouter",
        "google_ai_studio",
        "anthropic",
        "mistral",
        "groq",
        "cohere",
        "local_llm"
    ]

    def __init__(self, config_manager):
        self.config = config_manager
        self.session = self._create_session()
        self.token_usage = {}
        self._usage_lock = threading.Lock()
        self._usage_timer = None
        self._usage_dirty = False
        self._usage_pending = {}
        self._usage_reset = False
        self._usage_path = Path.home() / ".config" / "linux_ai_assistant" / "usage.json"
        self._load_token_usage()
        # Never lose a debounced write at exit
        atexit.register(self.flush_usage)
        self._load_custom_providers()

    def _create_session(self):
        """Create HTTP session with default headers"""
        import requests
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": f"LinuxAIAssistant/{_APP_VERSION}"
        })
        return session

    def _load_custom_providers(self):
        """Load custom providers from plugins (OPT-IN por config).

        Contrato de confiança: um plugin é código arbitrário executado com
        os privilégios do utilizador no arranque. Por isso só são carregados
        os que o utilizador activou explicitamente em `plugins.enabled`
        (deep-merge garante a chave) — antes, QUALQUER .py em plugins/ era
        importado e executado, incluindo o exemplo embarcado.
        """
        try:
            import importlib

            enabled = self.config.get("plugins.enabled", [])
            if not isinstance(enabled, list) or not enabled:
                return
            enabled_names = {str(name) for name in enabled}

            plugins_dir = Path(__file__).parent.parent / "plugins"
            if plugins_dir.exists():
                try:
                    # Aviso (não bloqueio): o directório é a fronteira de
                    # confiança; escrita por grupo/outros significa que
                    # qualquer processo local pode injectar código.
                    mode = plugins_dir.stat().st_mode
                    if mode & 0o022:
                        logger.warning(
                            "Plugins directory is group/other-writable (%o); "
                            "treat its contents as untrusted", mode & 0o777,
                        )
                except OSError:
                    pass
                for plugin_file in plugins_dir.glob("*.py"):
                    if plugin_file.name == "__init__.py":
                        continue
                    if plugin_file.stem not in enabled_names:
                        continue
                    try:
                        module_name = f"plugins.{plugin_file.stem}"
                        module = importlib.import_module(module_name)
                        if hasattr(module, 'register_provider'):
                            module.register_provider(self)
                            logger.info(f"Plugin loaded: {plugin_file.stem}")
                    except Exception as e:
                        logger.warning(f"Error loading plugin {plugin_file}: {redact_url(str(e))}")
        except Exception as e:
            logger.warning(f"Error loading plugins: {redact_url(str(e))}")

    def register_provider(self, name: str, chat_func: Callable):
        """Register a new custom provider"""
        if name not in self.SUPPORTED_PROVIDERS:
            # Copy-on-write: plugins must not mutate the class-level list
            # shared by every other AIClient instance in the process.
            self.SUPPORTED_PROVIDERS = list(self.SUPPORTED_PROVIDERS) + [name]
        setattr(self, f"_chat_{name}", chat_func)
        logger.info(f"Custom provider registered: {name}")

    def _get_api_config(self, provider: str) -> Dict[str, Any]:
        """Get API configuration for a specific provider"""
        providers = self.config.get("api.providers", {})
        # A malformed value (a hand-edited config.json, or an environment
        # override) must not crash the request with AttributeError.
        if not isinstance(providers, dict):
            logger.warning("api.providers is not an object; ignoring it")
            return {}
        entry = providers.get(provider, {})
        return entry if isinstance(entry, dict) else {}

    def _get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider"""
        return self.config.get_api_key(provider)

    def list_remote_models(self, provider: Optional[str] = None, timeout: float = 8.0) -> List[str]:
        """Explicit discovery only; local/offline policies prohibit remote probes."""
        from .remote_models import ModelDiscoveryError, discover_remote_models
        if self.get_assistance_mode() in ('offline', 'local'):
            raise ModelDiscoveryError('Remote model listing is disabled in offline and local modes.')
        selected = provider or self.config.get('api.default_provider', 'openrouter')
        return discover_remote_models(selected, self._get_api_config(selected), self._get_api_key(selected), timeout=timeout)

    def get_supported_providers(self) -> List[str]:
        """Get list of supported providers"""
        return self.SUPPORTED_PROVIDERS.copy()

    def get_assistance_mode(self) -> str:
        mode = self.config.get("assistance.mode", "auto")
        return mode if mode in ASSISTANCE_MODES else "offline"

    def active_provider(self, provider: Optional[str] = None) -> Optional[str]:
        """Resolve selection without making any network requests."""
        mode = self.get_assistance_mode()
        if mode == "offline":
            return None
        if mode == "local":
            if provider and provider != "local_llm":
                raise ProviderNotConfigured("Remote providers are unavailable in local mode")
            return "local_llm"
        selected = provider or self.config.get("api.default_provider", "openrouter")
        if mode == "remote" and selected == "local_llm":
            raise ProviderNotConfigured("Choose a remote provider for remote mode")
        return selected

    def _local_settings(self) -> Dict[str, Any]:
        api_config = self._get_api_config("local_llm")
        # Individual accessors respect environment overrides for URL/model.
        prefix = "api.providers.local_llm."
        return {
            key: self.config.get(prefix + key, api_config.get(key, default))
            for key, default in (("base_url", ""), ("model", ""),
                                 ("backend", "ollama"), ("strict_local", True))
        }

    def _strict_local(self, settings: Dict[str, Any], mode: Optional[str] = None) -> bool:
        # Explicit local mode always stays on the machine, even if a legacy
        # LAN-server setting allowed remote addresses in automatic mode.
        if mode is not None and mode not in ASSISTANCE_MODES:
            raise LocalModelError("Assistance mode must be auto, offline, local or remote")
        return (mode or self.get_assistance_mode()) == "local" or settings.get("strict_local") is not False

    def _provider_configured(self, provider: str) -> bool:
        api_config = self._get_api_config(provider)
        if provider == "local_llm":
            settings = self._local_settings()
            try:
                strict = self._strict_local(settings)
                validate_local_url(settings["base_url"], strict)
                validate_model_name(settings["model"], strict)
                return settings["backend"] in {"ollama", "openai"}
            except LocalModelError:
                return False
        if not api_config:
            # Plugin provider: sem entrada na config, mas registado em runtime
            return getattr(self, f"_chat_{provider}", None) is not None
        if provider in self.BUILTIN_PROVIDERS and not api_config.get("base_url"):
            # Sem base_url o pedido ia para "None/chat/completions" e falhava
            # sempre, com a UI a pensar que o provider estava pronto.
            return False
        return bool(self._get_api_key(provider))

    def provider_ready(self, provider: Optional[str] = None) -> bool:
        """Legacy configuration check; use provider_status for reachability.

        This method deliberately performs no I/O: a configured URL is not
        evidence that a server is running or the selected model is installed.
        """
        try:
            selected = self.active_provider(provider)
            return bool(selected and self._provider_configured(selected))
        except ProviderNotConfigured:
            return False

    def _probe_local_settings(self, settings: Optional[Dict[str, Any]] = None):
        effective = self._local_settings()
        if settings is not None:
            if not isinstance(settings, dict):
                raise LocalModelError("Invalid local model settings")
            effective.update({key: value for key, value in settings.items() if key in effective})
        return effective

    def _discover_local(self, timeout: float = 3.0, settings: Optional[Dict[str, Any]] = None,
                        mode: Optional[str] = None):
        settings = self._probe_local_settings(settings)
        return discover_local_models(settings["base_url"], settings["backend"],
                                     self._strict_local(settings, mode), timeout)

    def list_local_models(self, timeout: float = 3.0, settings: Optional[Dict[str, Any]] = None,
                          mode: Optional[str] = None) -> List[str]:
        """List already installed models; never pull, generate or contact cloud."""
        try:
            names, _ = self._discover_local(timeout, settings, mode)
            return names
        except (LocalModelError, RequestException) as error:
            raise AIProviderError(redact_url(str(error))) from error

    def test_local_connection(self, timeout: float = 3.0,
                              settings: Optional[Dict[str, Any]] = None,
                              mode: Optional[str] = None) -> ProviderStatus:
        """Check server and installed model without sending a user prompt."""
        mode = self.get_assistance_mode() if mode is None else mode
        try:
            settings = self._probe_local_settings(settings)
            model = settings["model"]
            strict = self._strict_local(settings, mode)
            validate_local_url(settings["base_url"], strict)
            validate_model_name(model, strict)
        except LocalModelError as error:
            return ProviderStatus(mode, "local_llm", "blocked", detail=str(error))
        try:
            names, rejected = self._discover_local(timeout, settings, mode)
        except RequestException as error:
            responded = getattr(error, "response", None) is not None
            return ProviderStatus(mode, "local_llm", "error" if responded else "unreachable",
                                  configured=True, reachable=responded, model=model,
                                  detail=redact_url(str(error)))
        except LocalModelError as error:
            return ProviderStatus(mode, "local_llm", "error", configured=True,
                                  model=model, detail=str(error))
        if model_is_installed(model, rejected, settings["backend"]):
            return ProviderStatus(mode, "local_llm", "blocked", configured=True, reachable=True,
                                  model=model, models=tuple(names), detail="Selected model uses an upstream cloud server")
        installed = model_is_installed(model, names, settings["backend"])
        return ProviderStatus(mode, "local_llm", "ready" if installed else "model_missing",
                              configured=True, reachable=True, model=model, models=tuple(names),
                              detail="" if installed else "Selected model is not installed on this server")

    def provider_status(self, check_connection: bool = False, timeout: float = 3.0,
                        provider: Optional[str] = None) -> ProviderStatus:
        """Report mode, configuration and (only on request) local readiness.

        Cloud connection tests would transmit requests to a third party, so
        remote providers report configuration only, never an invented ready
        status. Normal startup remains free of connection probes.
        """
        mode = self.get_assistance_mode()
        try:
            selected = self.active_provider(provider)
        except ProviderNotConfigured as error:
            return ProviderStatus(mode, provider, "blocked", detail=str(error))
        if selected is None:
            return ProviderStatus(mode, None, "offline", detail="Built-in knowledge and guided diagnostics")
        if selected == "local_llm":
            if check_connection:
                return self.test_local_connection(timeout)
            settings = self._local_settings()
            try:
                strict = self._strict_local(settings)
                validate_local_url(settings["base_url"], strict)
                validate_model_name(settings["model"], strict)
            except LocalModelError as error:
                return ProviderStatus(mode, selected, "blocked", model=str(settings["model"]), detail=str(error))
            configured = self._provider_configured(selected)
            return ProviderStatus(mode, selected, "configured" if configured else "unconfigured",
                                  configured=configured, model=settings["model"],
                                  detail="Connection has not been checked")
        configured = self._provider_configured(selected)
        return ProviderStatus(mode, selected, "configured" if configured else "unconfigured",
                              configured=configured, model=self._get_api_config(selected).get("model", ""),
                              detail="Connection has not been checked")

    def _validate_local_inference(self, base_url: str, model: str):
        """Enforce local policy before any prompt can leave the client."""
        settings = self._local_settings()
        strict = self._strict_local(settings)
        try:
            base_url = validate_local_url(base_url, strict)
            model = validate_model_name(model, strict)
            if self.get_assistance_mode() == "local" and strict:
                # Recheck metadata for every explicit local request: model
                # aliases can change, and cloud aliases need not say :cloud.
                names, rejected = self._discover_local(timeout=3.0)
                if model_is_installed(model, rejected, settings["backend"]):
                    raise LocalModelError("Selected model uses an upstream cloud server")
                if not model_is_installed(model, names, settings["backend"]):
                    raise LocalModelError("Selected model is not installed on this server")
            return base_url, model
        except (LocalModelError, RequestException) as error:
            raise ProviderNotConfigured(redact_url(str(error))) from error

    def _count_tokens(self, text: str) -> int:
        """Estimate number of tokens (simplified)"""
        if not text:
            return 0
        # Estimate: ~4 characters per token on average
        return max(1, len(text) // 4)

    def _update_token_usage(self, provider: str, input_tokens: int, output_tokens: int):
        """Update token count"""
        with self._usage_lock:
            if provider not in self.token_usage:
                self.token_usage[provider] = {"input": 0, "output": 0, "total": 0}
            self.token_usage[provider]["input"] += int(input_tokens)
            self.token_usage[provider]["output"] += int(output_tokens)
            self.token_usage[provider]["total"] += int(input_tokens) + int(output_tokens)
            pending = self._usage_pending.setdefault(provider, {"input": 0, "output": 0, "total": 0})
            pending["input"] += int(input_tokens)
            pending["output"] += int(output_tokens)
            pending["total"] += int(input_tokens) + int(output_tokens)
            self._schedule_usage_save()
        logger.debug(f"Token usage - {provider}: input={input_tokens}, output={output_tokens}")

    def _schedule_usage_save(self):
        """Debounced write of usage.json. Caller must hold `_usage_lock`."""
        self._usage_dirty = True
        if self._usage_timer is not None:
            self._usage_timer.cancel()
        timer = threading.Timer(USAGE_SAVE_DEBOUNCE_SECONDS, self.flush_usage)
        timer.daemon = True
        self._usage_timer = timer
        timer.start()

    def flush_usage(self):
        """Write usage.json now if there are pending changes."""
        with self._usage_lock:
            if self._usage_timer is not None:
                self._usage_timer.cancel()
                self._usage_timer = None
            if self._usage_dirty:
                self._save_token_usage()

    def _record_usage(self, provider: str, data: Dict[str, Any]):
        """Record usage reported by the API itself.

        When the response includes `usage`, use it instead of the estimate.
        """
        if not isinstance(data, dict):
            return
        usage = data.get("usage") or data.get("usageMetadata") or {}
        if not isinstance(usage, dict):
            return

        def _first(*names):
            # `or` would treat a legitimate 0 as "missing"
            for name in names:
                if usage.get(name) is not None:
                    return usage[name]
            return None

        input_tokens = _first("prompt_tokens", "input_tokens", "promptTokenCount")
        output_tokens = _first("completion_tokens", "output_tokens", "candidatesTokenCount")
        if input_tokens is None and output_tokens is None:
            return
        self._update_token_usage(provider, input_tokens or 0, output_tokens or 0)

    def _load_token_usage(self):
        """Load usage stats from previous sessions."""
        path = self._usage_path
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
            # Every entry must look like {"input": n, "output": n, "total": n}:
            # a malformed value would crash .get() later and silently break
            # every chat request.
            clean = {}
            for provider, entry in data.items():
                if isinstance(entry, dict) and all(
                    isinstance(entry.get(field), (int, float))
                    for field in ("input", "output", "total")
                ):
                    clean[provider] = {
                        "input": int(entry["input"]),
                        "output": int(entry["output"]),
                        "total": int(entry["total"]),
                    }
                else:
                    logger.warning(f"Ignoring malformed token usage entry: {provider}")
            self.token_usage = clean
            logger.debug(f"Token usage loaded: {list(clean)}")
        except Exception as e:
            logger.warning(f"Could not load token usage: {redact_url(str(e))}")

    def _save_token_usage(self):
        """Merge only this client's new usage under an inter-process lock."""
        pending = copy.deepcopy(self._usage_pending)
        reset = self._usage_reset
        def merge(previous):
            totals = {} if reset or not isinstance(previous, dict) else previous
            for provider, delta in pending.items():
                entry = totals.get(provider)
                if not isinstance(entry, dict):
                    entry = {}
                clean = {}
                for key in ("input", "output", "total"):
                    try:
                        value = int(entry.get(key, 0))
                    except (TypeError, ValueError, OverflowError):
                        value = 0
                    clean[key] = value + delta[key]
                totals[provider] = clean
            return totals
        try:
            self.token_usage = update_json(self._usage_path, merge, {})
            self._usage_pending.clear()
            self._usage_reset = False
            self._usage_dirty = False
        except JsonWriteCommittedError as error:
            # The replacement already contains these deltas/reset. Retrying
            # them would double-count usage or erase another client's work.
            # Keep a durability retry pending, but merge against the latest
            # disk value on that retry instead of replaying this snapshot.
            self.token_usage = error.value
            self._usage_pending.clear()
            self._usage_reset = False
            self._usage_dirty = True
            logger.warning("Token usage JSON was replaced, but durability is not confirmed: %s",
                           redact_url(str(error)))
        except Exception as error:
            self._usage_dirty = True
            logger.warning("Could not save token usage: %s", redact_url(str(error)))

    def get_token_usage(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """Get token usage"""
        with self._usage_lock:
            if provider:
                return copy.deepcopy(self.token_usage.get(provider, {"input": 0, "output": 0, "total": 0}))
            return copy.deepcopy(self.token_usage)

    def reset_token_usage(self):
        """Reset token count"""
        with self._usage_lock:
            if self._usage_timer is not None:
                self._usage_timer.cancel()
                self._usage_timer = None
            self.token_usage = {}
            self._usage_pending.clear()
            self._usage_reset = True
            self._usage_dirty = False
            self._save_token_usage()
        logger.info("Token usage reset")

    @retry(**NETWORK_RETRY)
    def _make_request(self, url: str, payload: Dict, headers: Optional[Dict] = None,
                     timeout: Optional[int] = None, stream: bool = False):
        """Make request with retry and error handling"""
        timeout = timeout or self.DEFAULT_TIMEOUT
        merged_headers = {**self.session.headers, **(headers or {})}
        safe_url = redact_url(url)

        response = None
        try:
            local_settings = self._local_settings()
            try:
                local_base = validate_local_url(local_settings["base_url"], self._strict_local(local_settings))
            except LocalModelError:
                local_base = ""
            if local_base and url == local_base + "/chat/completions":
                response = make_local_request(url, payload, merged_headers, timeout, stream)
            elif stream:
                response = self.session.post(
                    url, json=payload, headers=merged_headers, timeout=timeout, stream=True
                )
            else:
                response = self.session.post(
                    url, json=payload, headers=merged_headers, timeout=timeout
                )

            # Handle rate limiting: honor Retry-After (capped) and let
            # tenacity retry within NETWORK_RETRY's attempt budget.
            if response.status_code == 429:
                retry_after = self._parse_retry_after(response.headers.get('Retry-After'))
                response.close()
                logger.warning(
                    f"Rate limit hit for {safe_url}. "
                    f"Waiting {retry_after} seconds..."
                )
                time.sleep(retry_after)
                raise _RetryAfterRateLimit()

            response.raise_for_status()
            return response

        except _RetryAfterRateLimit:
            # Let tenacity retry (transient by policy)
            raise
        except Timeout as e:
            logger.error(f"Timeout connecting to {safe_url}: {redact_url(str(e))}")
            raise
        except RequestException as e:
            # requests embeds the full original URL in the exception text,
            # which can carry ?key=<API_KEY> - redact before logging.
            body_snippet = ""
            if response is not None:
                try:
                    # O body do provider (JSON com "invalid model",
                    # "insufficient credits", "key disabled", ...) é o único
                    # sítio onde se diagnostica um 4xx; descartá-lo tornava
                    # os erros impossíveis de perceber.
                    body_snippet = response.text[:500]
                except Exception:
                    pass
                response.close()
            logger.error(
                f"Request error for {safe_url}: {redact_url(str(e))}"
                + (f" | body: {redact_url(body_snippet)}" if body_snippet else "")
            )
            # Falha estruturada com a response anexada: a UI/CLI apanham-na
            # por tipo e `_is_transient` continua a decidir o retry (5xx).
            raise AIProviderError(
                f"{redact_url(str(e))}" + (f" | {redact_url(body_snippet)}" if body_snippet else ""),
                response=response,
            ) from e
        except Exception as e:
            if response is not None:
                response.close()
            logger.error(f"Unexpected error in request for {safe_url}: {redact_url(str(e))}")
            raise AIProviderError(redact_url(str(e))) from e

    def _parse_retry_after(self, value: Any) -> int:
        """Interpret the Retry-After header (seconds or HTTP date)."""
        if value is None:
            return self.RATE_LIMIT_WAIT
        try:
            seconds = int(str(value).strip())
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(str(value).strip())
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                # HTTP dates have second precision. Round up a future delay
                # rather than retrying before that second has arrived.
                seconds = math.ceil(date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError, OSError):
                logger.warning(f"Invalid Retry-After: {value!r}. Using {self.RATE_LIMIT_WAIT}s")
                return self.RATE_LIMIT_WAIT
        # Cap apenas defensivo: um header válido pode pedir mais do que o
        # default de 15 s (antes o cap cortava o valor e as tentativas
        # seguintes falhavam todas contra um limiar não reposto).
        return max(1, min(seconds, self.RATE_LIMIT_WAIT_MAX))

    @staticmethod
    def _iter_sse_openai_style(response, usage_events: Optional[list] = None):
        for data in iter_sse_json(response):
            if data.get("error"):
                raise AIProviderError("Provider stream error: " + redact_url(str(data["error"])))
            if usage_events is not None and isinstance(data.get("usage"), dict):
                usage_events.append(data["usage"])
            choices = data.get("choices") or []
            if choices:
                content = (choices[0].get("delta") or {}).get("content") or ""
                if content:
                    yield content

    @staticmethod
    def _iter_sse_anthropic(response, usage_events: Optional[list] = None):
        for data in iter_sse_json(response):
            if data.get("type") == "error":
                raise AIProviderError("Anthropic stream error: " + redact_url(str(data.get("error"))))
            if usage_events is not None:
                usage = (data.get("message") or {}).get("usage") if data.get("type") == "message_start" else data.get("usage")
                if isinstance(usage, dict):
                    usage_events.append(usage)
            delta = data.get("delta")
            if data.get("type") == "content_block_delta" and isinstance(delta, dict) and delta.get("text"):
                yield delta["text"]

    @staticmethod
    def _iter_sse_cohere(response, usage_events: Optional[list] = None):
        """Cohere v1 is newline-delimited JSON, not the v2 SSE contract."""
        for raw in response.iter_lines():
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].lstrip(" ")
            try:
                data = json.loads(line)
            except json.JSONDecodeError as error:
                raise AIProviderError("Invalid JSON in Cohere stream") from error
            event = data.get("event_type")
            if event == "text-generation":
                if data.get("text"):
                    yield data["text"]
            elif event == "stream-end":
                reply = data.get("response") or {}
                meta = reply.get("meta") or {}
                usage = meta.get("billed_units") or meta.get("tokens")
                if usage_events is not None and isinstance(usage, dict):
                    usage_events.append(usage)
                if data.get("finish_reason") in {"ERROR", "ERROR_TOXIC"}:
                    raise AIProviderError("Cohere stream failed: " + str(data.get("finish_reason")))
            elif event == "error" or data.get("error"):
                raise AIProviderError("Cohere stream error: " + redact_url(str(data.get("error") or data.get("message"))))

    @staticmethod
    def _iter_sse_google(response, usage_events: Optional[list] = None):
        for data in iter_sse_json(response):
            if data.get("error"):
                raise AIProviderError("Google stream error: " + redact_url(str(data["error"])))
            block = (data.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise AIProviderError("Google blocked the prompt: " + str(block))
            if usage_events is not None and isinstance(data.get("usageMetadata"), dict):
                usage_events.append(data["usageMetadata"])
            candidates = data.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                for part in parts:
                    if part.get("text"):
                        yield part["text"]

    def _validate_messages(self, messages: List[Dict[str, str]]) -> bool:
        """Validate message format."""
        if not isinstance(messages, list):
            logger.error("Messages must be a list")
            return False

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                logger.error(f"Message {i} is not a dictionary")
                return False
            if "role" not in msg or "content" not in msg:
                logger.error(f"Message {i} has no 'role' or 'content'")
                return False
            if msg["role"] not in ["user", "assistant", "system"]:
                logger.error(f"Invalid role in message {i}: {msg['role']}")
                return False
            # `content: None` no histórico passava a validação e rebentava
            # MAIS TARDE (ex.: " ".join no pós-processamento), depois de o
            # pedido ter sido respondido com sucesso — a resposta perdia-se.
            if not isinstance(msg["content"], str):
                logger.error(f"Message {i} content must be a string")
                return False

        return True

    def chat(self, messages: List[Dict[str, str]], provider: Optional[str] = None, model: Optional[str] = None,
             temperature: float = 0.7, max_tokens: int = 2000) -> Optional[str]:
        """
        Send messages to the AI API and get a response.

        Args:
            messages: List of messages in the format {"role": "user", "content": "..."}.
            provider: Provider to use (None = default).
            model: Model to use (None = provider default).
            temperature: Temperature for generation.
            max_tokens: Maximum number of tokens.

        Returns:
            AI response, or None on error.
        """

        # Validate input
        if not self._validate_messages(messages):
            logger.error("Invalid message format")
            return None

        if not messages:
            logger.error("Empty message list")
            return None

        try:
            provider = self.active_provider(provider)
            if provider is None:
                raise ProviderNotConfigured("AI requests are disabled in offline mode")
        except ProviderNotConfigured as error:
            logger.info("%s", error)
            return None
        api_config = self._get_api_config(provider)
        # Plugins register `_chat_<name>` even without an api.providers entry
        chat_method = getattr(self, f"_chat_{provider}", None)

        if not api_config and chat_method is None:
            logger.error(f"Unknown provider: {provider}")
            return None

        model = model or (self._local_settings()["model"] if provider == "local_llm" else api_config.get("model"))
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)

        if provider == "local_llm":
            settings = self._local_settings()
            model = model or settings["model"]
            base_url = settings["base_url"]
            try:
                base_url, model = self._validate_local_inference(base_url, model)
            except ProviderNotConfigured as error:
                logger.error("Local model unavailable: %s", error)
                return None

        if not api_key and provider != "local_llm" and api_config:
            logger.error(f"API key not configured for {provider}")
            return None
        # Sem base_url o URL ficava "None/chat/completions" e o pedido
        # falhava sempre com um erro críptico; falhar cedo e claro.
        if not base_url and provider in self.BUILTIN_PROVIDERS and api_config:
            logger.error(f"base_url not configured for {provider}")
            return None

        logger.info(f"Sending request to {provider} with model {model}")

        # Accumulated total before the call, to know whether the provider
        # reported real usage (and avoid double-counting the estimate).
        with self._usage_lock:
            before = self.token_usage.get(provider, {}).get("total", 0)

        try:
            # Call the provider's specific method
            if chat_method:
                result = chat_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                logger.error(f"Unsupported provider: {provider}")
                return None
        except AIProviderError as e:
            logger.error(f"Error communicating with {provider}: {redact_url(str(e))}")
            return None
        except Exception as e:
            # Sem exc_info: o traceback do requests inclui a URL original
            # (plugins podem usar ?key=...) e fugiria para app.log não
            # redactado.
            logger.error(f"Error communicating with {provider}: {redact_url(str(e))}")
            return None

        # Pós-processamento FORA do try: uma exceção aqui não pode descartar
        # uma resposta que o provider já devolveu com sucesso.
        if result:
            with self._usage_lock:
                after = self.token_usage.get(provider, {}).get("total", 0)

            if after > before:
                logger.info(f"Response received ({after - before} tokens, API usage)")
            else:
                try:
                    # Provider did not report usage: estimate it.
                    input_text = " ".join([msg.get("content", "") for msg in messages])
                    output_tokens = self._count_tokens(result)
                    self._update_token_usage(provider, self._count_tokens(input_text), output_tokens)
                    logger.info(f"Response received (~{output_tokens} tokens, estimate)")
                except Exception as e:
                    logger.warning(f"Could not estimate token usage: {e}")

        return result

    # ---- Helpers partilhados do contrato OpenAI-style ----
    # openrouter, mistral, groq e local_llm são o MESMO contrato (endpoint
    # /chat/completions, payload `messages`, resposta choices[0].message).
    # Centralizar garante parsing/usage/erros uniformes: antes, uns
    # validavam `choices: []` e outros não (IndexError no catch genérico),
    # e `content: null` voltava como None tratado como erro de rede.

    def _chat_openai_style(self, provider: str, url: str, payload: Dict,
                           headers: Dict, timeout: int) -> Optional[str]:
        """POST + parse de um provider OpenAI-compatible. Levanta
        AIProviderError em falha; devolve "" quando o provider responde
        sem conteúdo (contrato já usado por mistral/groq)."""
        try:
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage(provider, data)

            choices = data.get("choices")
            if not choices:
                logger.warning(f"No choices in {provider} response")
                return ""
            content = (choices[0].get("message") or {}).get("content")
            if content is None:
                logger.warning(f"Empty content in {provider} response")
                return ""
            return content
        except AIProviderError:
            raise
        except json.JSONDecodeError as e:
            raise AIProviderError(f"Invalid JSON from {provider}: {redact_url(str(e))}") from e
        except Exception as e:
            raise AIProviderError(f"{provider} error: {redact_url(str(e))}") from e

    def _stream_openai_style(self, provider: str, url: str, payload: Dict,
                             headers: Dict, timeout: int, messages: List[Dict]):
        """Streaming generator de um provider OpenAI-compatible.

        Registra usage REAL quando o provider o reporta no chunk final
        (`stream_options: include_usage`), senão estima a partir do texto
        recebido — antes, o streaming (o caminho principal da GUI) nunca
        era contabilizado e as estatísticas/orçamento ficavam mortas.
        """
        payload = dict(payload)
        # Local compatibility is opt-in; remote providers support real usage.
        include_usage = self.config.get("api.providers." + provider + ".stream_include_usage",
                                        provider != "local_llm")
        if include_usage:
            payload["stream_options"] = {"include_usage": True}

        usage_events: List[Dict] = []
        received: List[str] = []
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                for chunk in self._iter_sse_openai_style(response, usage_events):
                    received.append(chunk)
                    yield chunk
            finally:
                response.close()
                self._finish_stream_usage(provider, usage_events, messages,
                                          "".join(received))
        except AIProviderError:
            raise
        except Exception as e:
            raise AIProviderError(
                f"{provider} stream error: {redact_url(str(e))}"
            ) from e

    def _finish_stream_usage(self, provider: str, usage_events: List[Dict],
                             messages: List[Dict], full_text: str):
        """Registar tokens de um stream terminado (real ou estimado)."""
        try:
            if usage_events:
                # Usage fields are cumulative, not increments. Retain input
                # from Anthropic's start and the latest output/Google totals.
                reported = {}
                for event in usage_events:
                    reported.update({key: value for key, value in event.items() if value is not None})
                self._record_usage(provider, {"usage": reported})
            elif full_text:
                input_text = " ".join(
                    m.get("content", "") for m in messages if isinstance(m, dict)
                )
                self._update_token_usage(
                    provider, self._count_tokens(input_text),
                    self._count_tokens(full_text),
                )
        except Exception as e:
            logger.warning(f"Could not record stream usage: {e}")

    def _chat_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                         base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the OpenRouter API"""
        url = f"{base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/1400015/linux_ai",
            "X-Title": "Linux AI Assistant"
        }

        try:
            return self._chat_openai_style("openrouter", url, payload, headers, timeout)
        except AIProviderError as e:
            logger.error(f"OpenRouter error: {redact_url(str(e))}")
            return None

    def _google_safety_settings(self):
        """Google safety settings.

        By default we do NOT send `safetySettings`: the API applies its own
        filters. `disable_safety_filters: true` in config allows disabling
        them explicitly.
        """
        if not self.config.get("api.providers.google_ai_studio.disable_safety_filters", False):
            return []
        logger.warning("Google AI Studio safety filters disabled by configuration")
        return [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

    def _chat_google_ai_studio(self, messages: List[Dict[str, str]], model: str, api_key: str,
                               base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the Google AI Studio API"""
        # The key goes in a header, never in the query string: URLs leak into
        # logs, proxies and exception messages.
        url = f"{base_url}/models/{model}:generateContent"
        headers = {"x-goog-api-key": api_key}

        # Convert messages to Google's format
        google_messages = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            google_messages.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        payload = {
            "contents": google_messages,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            },
            "safetySettings": self._google_safety_settings()
        }

        try:
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("google_ai_studio", data)

            if not data.get("candidates"):
                # Bloqueio de segurança: sem isto o utilizador via uma
                # resposta vazia sem saber que foi um filtro do provider.
                block_reason = (data.get("promptFeedback") or {}).get("blockReason")
                if block_reason:
                    logger.warning(
                        "Google AI Studio blocked the prompt: %s", block_reason
                    )
                    return f"[blocked by safety filters: {block_reason}]"
                logger.warning("No candidates in Google AI Studio response")
                return ""

            # Respostas multi-part: só parts[0] truncava o texto (o caminho
            # streaming junta todas — inconsistentes entre si).
            parts = (data["candidates"][0].get("content") or {}).get("parts") or []
            texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
            return "".join(texts)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Google AI Studio JSON: {e}")
            return None
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Invalid response format from Google AI Studio: {e}")
            return None
        except AIProviderError:
            raise
        except Exception as e:
            logger.error(f"Google AI Studio error: {redact_url(str(e))}")
            return None

    @staticmethod
    def _split_anthropic_messages(messages: List[Dict[str, str]]):
        """Separate the system prompt and convert the remaining roles.

        Anthropic requires `system` as a top-level field, only accepts
        `user`/`assistant` in the `messages` array, messages must ALTERNATE
        (no two consecutive of the same role) and the first must be `user`.
        The UI history gives no such guarantees — a failed/cancelled turn
        can leave two `user` in a row (o pedido morria com 400).
        """
        system_parts = []
        converted = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                if converted and converted[-1]["role"] == role:
                    # Fundir consecutivas do mesmo papel numa só
                    converted[-1]["content"] = f"{converted[-1]['content']}\n\n{content}"
                else:
                    converted.append({"role": role, "content": content})
        # A API rejeita histórico que não comece por `user`
        while converted and converted[0]["role"] != "user":
            converted.pop(0)
        return "\n\n".join(system_parts), converted

    def _chat_anthropic(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the Anthropic Claude API"""
        url = f"{base_url}/messages"
        system_prompt, anthropic_messages = self._split_anthropic_messages(messages)

        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        # `anthropic-version` is a required header; `x-api-key` is the auth.
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        try:
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("anthropic", data)

            if data.get("type") == "error":
                logger.error(f"Anthropic error: {data.get('error', {}).get('message', 'Unknown')}")
                return None

            # Get content from the first reply
            content = data.get("content")
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "".join(texts)
            if isinstance(content, dict):
                return content.get("text", "")
            return ""
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Anthropic JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Anthropic: {redact_url(str(e))}")
            return None
        except Exception as e:
            logger.error(f"Anthropic error: {redact_url(str(e))}")
            return None

    def _chat_mistral(self, messages: List[Dict[str, str]], model: str, api_key: str,
                      base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the Mistral AI API"""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            return self._chat_openai_style("mistral", url, payload, headers, timeout)
        except AIProviderError as e:
            logger.error(f"Mistral error: {redact_url(str(e))}")
            return None

    def _chat_groq(self, messages: List[Dict[str, str]], model: str, api_key: str,
                   base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the Groq API"""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            return self._chat_openai_style("groq", url, payload, headers, timeout)
        except AIProviderError as e:
            logger.error(f"Groq error: {redact_url(str(e))}")
            return None

    def _chat_cohere(self, messages: List[Dict[str, str]], model: str, api_key: str,
                     base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with the Cohere API (v1 /chat contract)."""
        url = f"{base_url}/chat"

        # v1 expects a top-level `message` plus `chat_history` with the
        # role/message pairs - not an OpenAI-style `messages` array.
        chat_history = []
        preamble_parts = []
        last_user_text = ""
        for msg in messages:
            role = msg["role"]
            if role == "system":
                preamble_parts.append(msg["content"])
            elif role == "user":
                chat_history.append({"role": "USER", "message": msg["content"]})
            elif role == "assistant":
                chat_history.append({"role": "CHATBOT", "message": msg["content"]})
        # The final user turn is the `message`, not part of the history
        if chat_history and chat_history[-1]["role"] == "USER":
            last_user_text = chat_history.pop()["message"]

        # v1 responde 400 com `message` vazio (histórico que termina em
        # assistant ou só tem system): falhar cedo e claro.
        if not last_user_text:
            raise AIProviderError(
                "Cohere requires a final user message; history ends without one"
            )

        payload = {
            "model": model,
            "message": last_user_text,
            "chat_history": chat_history,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if preamble_parts:
            payload["preamble"] = "\n\n".join(preamble_parts)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            meta = data.get("meta") or {}
            self._record_usage("cohere", {"usage": meta.get("billed_units") or meta.get("tokens") or {}})
            return data.get("text") or ""
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Cohere JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Cohere: {redact_url(str(e))}")
            return None
        except Exception as e:
            logger.error(f"Cohere error: {redact_url(str(e))}")
            return None

    def _chat_local_llm(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Communicate with a local model (Ollama, etc)"""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        try:
            return self._chat_openai_style("local_llm", url, payload, {}, timeout)
        except AIProviderError as e:
            logger.error(f"Local model error: {redact_url(str(e))}")
            return None

    def stream_chat(self, messages: List[Dict[str, str]], provider: Optional[str] = None, model: Optional[str] = None,
                    temperature: float = 0.7, max_tokens: int = 2000):
        """
        AI response stream (generator).

        Contrato de erros (mudou): falhas NÃO são yieldadas como texto do
        modelo — são levantadas como AIProviderError / ProviderNotConfigured.
        Antes, `yield "Stream error: ..."` obrigava a UI a adivinhar por
        prefixo de string: um erro a meio do stream nunca era detectado (sem
        fallback offline) e uma resposta legítima que começasse por "Error:"
        disparava o fallback.

        Args:
            messages: List of messages.
            provider: Provider to use.
            model: Model to use.
            temperature: Temperature.
            max_tokens: Maximum tokens.

        Yields:
            Chunks of the response as they arrive.

        Raises:
            ProviderNotConfigured: provider sem chave/base_url/desconhecido.
            AIProviderError: falha de rede/HTTP/parsing.
        """
        # Validate input
        if not self._validate_messages(messages):
            raise AIProviderError("Invalid message format")

        if not messages:
            raise AIProviderError("Empty message list")

        provider = self.active_provider(provider)
        if provider is None:
            raise ProviderNotConfigured("AI requests are disabled in offline mode")
        api_config = self._get_api_config(provider)
        stream_method = getattr(self, f"_stream_{provider}", None)
        # Plugins may register only a chat function (fallback below)
        chat_method = getattr(self, f"_chat_{provider}", None)

        if not api_config and stream_method is None and chat_method is None:
            raise ProviderNotConfigured(f"Unknown provider: {provider}")

        model = model or (self._local_settings()["model"] if provider == "local_llm" else api_config.get("model"))
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)

        if provider == "local_llm":
            settings = self._local_settings()
            model = model or settings["model"]
            base_url, model = self._validate_local_inference(settings["base_url"], model)

        # Same fast-fail as chat(): do not send `Authorization: Bearer None`
        if not api_key and provider != "local_llm" and api_config:
            raise ProviderNotConfigured(f"API key not configured for {provider}")
        if not base_url and provider in self.BUILTIN_PROVIDERS and api_config:
            raise ProviderNotConfigured(f"base_url not configured for {provider}")

        try:
            # Call the provider's specific stream method
            if stream_method:
                received_text = False
                for chunk in stream_method(messages, model, api_key, base_url, temperature, max_tokens, timeout):
                    if chunk:
                        received_text = True
                        yield chunk
                if not received_text:
                    raise AIProviderError("Empty response from " + provider)
            else:
                # Fallback: make a normal request and yield all content
                response = self.chat(messages, provider, model, temperature, max_tokens)
                if response:
                    yield response
                else:
                    raise AIProviderError(f"No response from {provider} (check the logs)")
        except AIProviderError:
            raise
        except Exception as e:
            logger.error(f"Stream error for {provider}: {redact_url(str(e))}")
            raise AIProviderError(redact_url(str(e))) from e

    def _stream_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                           base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with OpenRouter."""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/1400015/linux_ai",
            "X-Title": "Linux AI Assistant"
        }
        yield from self._stream_openai_style("openrouter", url, payload,
                                             headers, timeout, messages)

    def _stream_anthropic(self, messages: List[Dict[str, str]], model: str, api_key: str,
                          base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Anthropic Claude."""
        url = f"{base_url}/messages"

        system_prompt, anthropic_messages = self._split_anthropic_messages(messages)

        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True
        }
        if system_prompt:
            payload["system"] = system_prompt

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        usage_events: List[Dict] = []
        received: List[str] = []
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                for chunk in self._iter_sse_anthropic(response, usage_events):
                    received.append(chunk)
                    yield chunk
            finally:
                response.close()
                self._finish_stream_usage("anthropic", usage_events, messages,
                                          "".join(received))
        except AIProviderError:
            raise
        except Exception as e:
            raise AIProviderError(
                f"anthropic stream error: {redact_url(str(e))}"
            ) from e

    def _stream_mistral(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Mistral AI."""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        yield from self._stream_openai_style("mistral", url, payload,
                                             headers, timeout, messages)

    def _stream_groq(self, messages: List[Dict[str, str]], model: str, api_key: str,
                     base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Groq."""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        yield from self._stream_openai_style("groq", url, payload,
                                             headers, timeout, messages)

    def _stream_cohere(self, messages: List[Dict[str, str]], model: str, api_key: str,
                       base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Cohere (v1 contract, same as _chat_cohere)."""
        url = f"{base_url}/chat"

        chat_history = []
        preamble_parts = []
        last_user_text = ""
        for msg in messages:
            role = msg["role"]
            if role == "system":
                preamble_parts.append(msg["content"])
            elif role == "user":
                chat_history.append({"role": "USER", "message": msg["content"]})
            elif role == "assistant":
                chat_history.append({"role": "CHATBOT", "message": msg["content"]})
        if chat_history and chat_history[-1]["role"] == "USER":
            last_user_text = chat_history.pop()["message"]
        # Mesmo guard do caminho não-streaming: v1 responde 400 com
        # `message` vazio.
        if not last_user_text:
            raise AIProviderError(
                "Cohere requires a final user message; history ends without one"
            )

        payload = {
            "model": model,
            "message": last_user_text,
            "chat_history": chat_history,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        if preamble_parts:
            payload["preamble"] = "\n\n".join(preamble_parts)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        usage_events, received = [], []
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                for chunk in self._iter_sse_cohere(response, usage_events):
                    received.append(chunk)
                    yield chunk
            finally:
                response.close()
                self._finish_stream_usage("cohere", usage_events, messages, "".join(received))
        except AIProviderError:
            raise
        except Exception as e:
            raise AIProviderError(
                f"cohere stream error: {redact_url(str(e))}"
            ) from e

    def _stream_google_ai_studio(self, messages: List[Dict[str, str]], model: str, api_key: str,
                                 base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Google AI Studio (streamGenerateContent, SSE)."""
        # `alt=sse` makes the endpoint emit `data: {...}` lines; the API key
        # travels in a header so it never ends up in a logged URL.
        url = f"{base_url}/models/{model}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": api_key}

        google_messages = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            google_messages.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        payload = {
            "contents": google_messages,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            },
            "safetySettings": self._google_safety_settings()
        }

        usage_events: List[Dict] = []
        received: List[str] = []
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                for chunk in self._iter_sse_google(response, usage_events):
                    received.append(chunk)
                    yield chunk
            finally:
                response.close()
                self._finish_stream_usage("google_ai_studio", usage_events,
                                          messages, "".join(received))
        except AIProviderError:
            raise
        except Exception as e:
            raise AIProviderError(
                f"google_ai_studio stream error: {redact_url(str(e))}"
            ) from e

    def _stream_local_llm(self, messages: List[Dict[str, str]], model: str, api_key: str,
                          base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with a local model."""
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        yield from self._stream_openai_style("local_llm", url, payload,
                                             {}, timeout, messages)
