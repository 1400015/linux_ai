import json
import copy
import os
import time
import logging
import re
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from requests.exceptions import RequestException, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError

# Configurar logger
logger = logging.getLogger(__name__)

_SECRET_QUERY_RE = re.compile(
    r"([?&](?:key|api_key|apikey|access_token|token|sig|signature|password|auth)=)[^&\s]+",
    re.I,
)


class _RetryAfterRateLimit(RequestException):
    """Sinaliza um 429 que deve ser repetido depois de `Retry-After`.

    Herda de RequestException para que a politica `NETWORK_RETRY` a volte a
    tentar (ate 3 tentativas), em vez de se propagar imediatamente.
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
    response = getattr(exc, "response", None)
    if isinstance(exc, RequestException) and response is not None:
        return response.status_code >= 500
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
    RATE_LIMIT_WAIT = 15  # seconds (defensive cap for Retry-After)
    
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
        self._usage_path = Path.home() / ".config" / "linux_ai_assistant" / "usage.json"
        self._load_token_usage()
        self._load_custom_providers()
    
    def _create_session(self):
        """Create HTTP session with default headers"""
        import requests
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "LinuxAIAssistant/1.0"
        })
        return session
    
    def _load_custom_providers(self):
        """Load custom providers from plugins"""
        try:
            import importlib
            
            plugins_dir = Path(__file__).parent.parent / "plugins"
            if plugins_dir.exists():
                for plugin_file in plugins_dir.glob("*.py"):
                    if plugin_file.name != "__init__.py":
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
        return providers.get(provider, {})
    
    def _get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider"""
        return self.config.get_api_key(provider)
    
    def get_supported_providers(self) -> List[str]:
        """Get list of supported providers"""
        return self.SUPPORTED_PROVIDERS.copy()
    
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
            # Saved while still holding the lock: concurrent mutation during
            # json.dump would raise "dictionary changed size during iteration".
            self._save_token_usage()
        logger.debug(f"Token usage - {provider}: input={input_tokens}, output={output_tokens}")

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
        """Persist usage stats so the CLI can show them (atomic, 0600).

        Caller must hold `_usage_lock`.
        """
        path = self._usage_path
        temp_path = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Snapshot so the dump cannot race with concurrent updates
            snapshot = copy.deepcopy(self.token_usage)
            temp_path = path.with_name(path.name + ".tmp")
            fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception as e:
            logger.warning(f"Could not save token usage: {redact_url(str(e))}")
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def get_token_usage(self, provider: str = None) -> Dict[str, Any]:
        """Get token usage"""
        with self._usage_lock:
            if provider:
                return copy.deepcopy(self.token_usage.get(provider, {"input": 0, "output": 0, "total": 0}))
            return copy.deepcopy(self.token_usage)
    
    def reset_token_usage(self):
        """Reset token count"""
        with self._usage_lock:
            self.token_usage = {}
            self._save_token_usage()
        logger.info("Token usage reset")
    
    @retry(**NETWORK_RETRY)
    def _make_request(self, url: str, payload: Dict, headers: Dict = None,
                     timeout: int = None, stream: bool = False):
        """Make request with retry and error handling"""
        timeout = timeout or self.DEFAULT_TIMEOUT
        merged_headers = {**self.session.headers, **(headers or {})}
        safe_url = redact_url(url)

        response = None
        try:
            if stream:
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
            if response is not None:
                response.close()
            logger.error(f"Request error for {safe_url}: {redact_url(str(e))}")
            raise
        except Exception as e:
            if response is not None:
                response.close()
            logger.error(f"Unexpected error in request for {safe_url}: {redact_url(str(e))}")
            raise

    def _parse_retry_after(self, value: Any) -> Optional[int]:
        """Interpret the Retry-After header (seconds or HTTP date)."""
        if value is None:
            return self.RATE_LIMIT_WAIT
        try:
            seconds = int(str(value).strip())
        except (TypeError, ValueError):
            logger.warning(f"Invalid Retry-After: {value!r}. Using {self.RATE_LIMIT_WAIT}s")
            return self.RATE_LIMIT_WAIT
        # Defensive cap: do not block the thread for hours
        return max(1, min(seconds, self.RATE_LIMIT_WAIT))
    
    @staticmethod
    def _iter_sse_openai_style(response):
        """Iterate an SSE stream in OpenAI style (`data: {...}`)."""
        for line in response.iter_lines():
            if not line:
                continue
            decoded_line = line.decode("utf-8")
            if not decoded_line.startswith("data: "):
                continue
            data_str = decoded_line[6:]
            if data_str == "[DONE]":
                return
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices")
            if choices:
                content = choices[0].get("delta", {}).get("content", "")
                if content:
                    yield content

    @staticmethod
    def _iter_sse_anthropic(response):
        """Iterate an SSE stream from Anthropic."""
        for line in response.iter_lines():
            if not line:
                continue
            decoded_line = line.decode("utf-8")
            if not decoded_line.startswith("data: "):
                continue
            try:
                data = json.loads(decoded_line[6:])
            except json.JSONDecodeError:
                continue
            delta = data.get("delta")
            if data.get("type") == "content_block_delta" and isinstance(delta, dict):
                text = delta.get("text")
                if text:
                    yield text

    @staticmethod
    def _iter_sse_cohere(response):
        """Iterate an SSE stream from Cohere."""
        for line in response.iter_lines():
            if not line:
                continue
            decoded_line = line.decode("utf-8")
            if not decoded_line.startswith("data: "):
                continue
            try:
                data = json.loads(decoded_line[6:])
            except json.JSONDecodeError:
                continue
            if data.get("type") in (None, "content-delta") and "text" in data:
                yield data["text"]

    @staticmethod
    def _iter_sse_google(response):
        """Iterate an SSE stream from Google AI Studio."""
        for line in response.iter_lines():
            if not line:
                continue
            decoded_line = line.decode("utf-8")
            if not decoded_line.startswith("data: "):
                continue
            data_str = decoded_line[6:]
            if data_str == "[DONE]":
                return
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            candidates = data.get("candidates")
            if not candidates:
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                text = part.get("text")
                if text:
                    yield text

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
        
        return True
    
    def chat(self, messages: List[Dict[str, str]], provider: str = None, model: str = None,
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
        
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        # Plugins register `_chat_<name>` even without an api.providers entry
        chat_method = getattr(self, f"_chat_{provider}", None)

        if not api_config and chat_method is None:
            logger.error(f"Unknown provider: {provider}")
            return None
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)
        
        if not api_key and provider != "local_llm" and api_config:
            logger.error(f"API key not configured for {provider}")
            return None
        
        try:
            logger.info(f"Sending request to {provider} with model {model}")

            # Accumulated total before the call, to know whether the provider
            # reported real usage (and avoid double-counting the estimate).
            with self._usage_lock:
                before = self.token_usage.get(provider, {}).get("total", 0)

            # Call the provider's specific method
            if chat_method:
                result = chat_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                logger.error(f"Unsupported provider: {provider}")
                return None

            with self._usage_lock:
                after = self.token_usage.get(provider, {}).get("total", 0)

            if result:
                if after > before:
                    logger.info(f"Response received ({after - before} tokens, API usage)")
                else:
                    # Provider did not report usage: estimate it.
                    input_text = " ".join([msg.get("content", "") for msg in messages])
                    output_tokens = self._count_tokens(result)
                    self._update_token_usage(provider, self._count_tokens(input_text), output_tokens)
                    logger.info(f"Response received (~{output_tokens} tokens, estimate)")

            return result
            
        except Exception as e:
            logger.error(f"Error communicating with {provider}: {redact_url(str(e))}", exc_info=True)
            return None
    
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
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("openrouter", data)
            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing OpenRouter JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from OpenRouter: {redact_url(str(e))}")
            return None
        except Exception as e:
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
                logger.warning("No candidates in Google AI Studio response")
                return ""

            return data["candidates"][0]["content"]["parts"][0]["text"]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Google AI Studio JSON: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Google AI Studio: {e}")
            return None
        except Exception as e:
            logger.error(f"Google AI Studio error: {e}")
            return None
    
    @staticmethod
    def _split_anthropic_messages(messages: List[Dict[str, str]]):
        """Separate the system prompt and convert the remaining roles.

        Anthropic requires `system` as a top-level field and only accepts
        `user`/`assistant` in the `messages` array.
        """
        system_parts = []
        converted = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": content})
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
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("mistral", data)

            if not data.get("choices"):
                logger.warning("No choices in Mistral response")
                return ""

            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Mistral JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Mistral: {redact_url(str(e))}")
            return None
        except Exception as e:
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
            response = self._make_request(url, payload, headers, timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("groq", data)

            if not data.get("choices"):
                logger.warning("No choices in Groq response")
                return ""

            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Groq JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Groq: {redact_url(str(e))}")
            return None
        except Exception as e:
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
                chat_history.append({"role": "ASSISTANT", "message": msg["content"]})
        # The final user turn is the `message`, not part of the history
        if chat_history and chat_history[-1]["role"] == "USER":
            last_user_text = chat_history.pop()["message"]

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

            self._record_usage("cohere", data)

            if not data.get("response"):
                logger.warning("No response from Cohere")
                return ""

            return data["response"]
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
            response = self._make_request(url, payload, timeout=timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("local_llm", data)
            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing local model JSON: {redact_url(str(e))}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from local model: {redact_url(str(e))}")
            return None
        except Exception as e:
            logger.error(f"Local model error: {redact_url(str(e))}")
            return None
    
    def stream_chat(self, messages: List[Dict[str, str]], provider: str = None, model: str = None,
                    temperature: float = 0.7, max_tokens: int = 2000):
        """
        AI response stream (generator).

        Args:
            messages: List of messages.
            provider: Provider to use.
            model: Model to use.
            temperature: Temperature.
            max_tokens: Maximum tokens.

        Yields:
            Chunks of the response as they arrive.
        """
        # Validate input
        if not self._validate_messages(messages):
            yield "Invalid message format"
            return
        
        if not messages:
            yield "Empty message list"
            return
        
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        stream_method = getattr(self, f"_stream_{provider}", None)
        # Plugins may register only a chat function (fallback below)
        chat_method = getattr(self, f"_chat_{provider}", None)

        if not api_config and stream_method is None and chat_method is None:
            yield "Unknown provider"
            return
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)

        # Same fast-fail as chat(): do not send `Authorization: Bearer None`
        if not api_key and provider != "local_llm" and api_config:
            yield f"API key not configured for {provider}"
            return
        
        try:
            # Call the provider's specific stream method
            if stream_method:
                yield from stream_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                # Fallback: make a normal request and yield all content
                response = self.chat(messages, provider, model, temperature, max_tokens)
                if response:
                    yield response
                else:
                    yield f"Error: no response from {provider} (check the logs)"
        except Exception as e:
            logger.error(f"Stream error for {provider}: {redact_url(str(e))}", exc_info=True)
            yield f"Error: {redact_url(str(e))}"
    
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
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_openai_style(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"OpenRouter stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
    
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
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_anthropic(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Anthropic stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
    
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
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_openai_style(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Mistral stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
    
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
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_openai_style(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Groq stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
    
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
                chat_history.append({"role": "ASSISTANT", "message": msg["content"]})
        if chat_history and chat_history[-1]["role"] == "USER":
            last_user_text = chat_history.pop()["message"]
        
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
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_cohere(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Cohere stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
    
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

        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            try:
                yield from self._iter_sse_google(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Google AI Studio stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"

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

        try:
            response = self._make_request(url, payload, timeout=timeout, stream=True)
            try:
                yield from self._iter_sse_openai_style(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Local model stream error: {redact_url(str(e))}")
            yield f"Stream error: {e}"
