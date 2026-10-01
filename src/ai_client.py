import json
import time
import logging
import re
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import RequestException, Timeout, ConnectionError

# Configurar logger
logger = logging.getLogger(__name__)

# Politica de retry para falhas de rede/transientes
NETWORK_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((RequestException, Timeout, ConnectionError)),
    reraise=True,
)

_SECRET_QUERY_RE = re.compile(r"([?&](?:key|api_key|access_token|token)=)[^&\s]+", re.I)


class _RetryAfterRateLimit(RequestException):
    """Sinaliza um 429 que deve ser repetido depois de `Retry-After`.

    Herda de RequestException para que a politica `NETWORK_RETRY` a volte a
    tentar (ate 3 tentativas), em vez de se propagar imediatamente.
    """


def redact_url(url: str) -> str:
    """Remove segredos de query strings antes de escrever no log."""
    if not url:
        return url
    return _SECRET_QUERY_RE.sub(r"\1***", url)


class AIClient:
    """Client to interact with several AI APIs"""
    
    # Constantes
    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3
    RATE_LIMIT_WAIT = 60  # segundos
    
    # Provedores suportados
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
    
    def _setup_retry_strategy(self):
        """Deprecated: a politica de retry vive em NETWORK_RETRY."""
        return None

    def _load_custom_providers(self):
        """Carregar provedores customizados de plugins"""
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
                                logger.info(f"Plugin carregado: {plugin_file.stem}")
                        except Exception as e:
                            logger.warning(f"Erro a load plugin {plugin_file}: {e}")
        except Exception as e:
            logger.warning(f"Erro a load plugins: {e}")
    
    def register_provider(self, name: str, chat_func: Callable):
        """Registrar a new provider customizado"""
        if name not in self.SUPPORTED_PROVIDERS:
            self.SUPPORTED_PROVIDERS.append(name)
        setattr(self, f"_chat_{name}", chat_func)
        logger.info(f"Provedor customizado registado: {name}")
    
    def _get_api_config(self, provider: str) -> Dict[str, Any]:
        """Get API configuration for a specific provider"""
        providers = self.config.get("api.providers", {})
        return providers.get(provider, {})
    
    def _get_api_key(self, provider: str) -> Optional[str]:
        """Obter API key for a provedor"""
        return self.config.get_api_key(provider)
    
    def get_supported_providers(self) -> List[str]:
        """Obter list de provedores suportados"""
        return self.SUPPORTED_PROVIDERS.copy()
    
    def _count_tokens(self, text: str) -> int:
        """Estimate number of tokens (simplified)"""
        if not text:
            return 0
        # Estimate: ~4 characters per token on average
        return max(1, len(text) // 4)
    
    def _update_token_usage(self, provider: str, input_tokens: int, output_tokens: int):
        """Atualizar contagem de tokens"""
        with self._usage_lock:
            if provider not in self.token_usage:
                self.token_usage[provider] = {"input": 0, "output": 0, "total": 0}
            self.token_usage[provider]["input"] += int(input_tokens)
            self.token_usage[provider]["output"] += int(output_tokens)
            self.token_usage[provider]["total"] += int(input_tokens) + int(output_tokens)
        self._save_token_usage()
        logger.debug(f"Token usage - {provider}: input={input_tokens}, output={output_tokens}")

    def _record_usage(self, provider: str, data: Dict[str, Any]):
        """Registar o consumo reportado pela propria API.

        Quando a resposta traz `usage`, usa-o em vez da estimativa.
        """
        if not isinstance(data, dict):
            return
        usage = data.get("usage") or data.get("usageMetadata") or {}
        if not isinstance(usage, dict):
            return
        input_tokens = (
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokenCount")
        )
        output_tokens = (
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("candidatesTokenCount")
        )
        if input_tokens is None and output_tokens is None:
            return
        self._update_token_usage(provider, input_tokens or 0, output_tokens or 0)

    def _usage_path_safe(self):
        try:
            return self._usage_path
        except Exception:
            return None

    def _load_token_usage(self):
        """Carregar estatisticas de uso de sessoes anteriores."""
        path = self._usage_path_safe()
        if not path or not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.token_usage = data
                logger.debug(f"Token usage loaded: {list(data)}")
        except Exception as e:
            logger.warning(f"Could not load token usage: {e}")

    def _save_token_usage(self):
        """Persistir estatisticas de uso para que a CLI as consiga mostrar."""
        path = self._usage_path_safe()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.token_usage, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save token usage: {e}")

    def get_token_usage(self, provider: str = None) -> Dict[str, Any]:
        """Obter uso de tokens"""
        if provider:
            return self.token_usage.get(provider, {"input": 0, "output": 0, "total": 0})
        return self.token_usage
    
    def reset_token_usage(self):
        """Resetar contagem de tokens"""
        with self._usage_lock:
            self.token_usage = {}
        self._save_token_usage()
        logger.info("Token usage reset")
    
    @retry(**NETWORK_RETRY)
    def _make_request(self, url: str, payload: Dict, headers: Dict = None, 
                     timeout: int = None, stream: bool = False):
        """Fazer request with retry e error handling"""
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
            
            # Handle rate limiting com um numero limitado de tentativas
            if response.status_code == 429:
                retry_after = self._parse_retry_after(response.headers.get('Retry-After'))
                response.close()
                if retry_after is None:
                    raise RequestException(
                        f"Rate limit excedido ({safe_url}) e sem Retry-After utilizavel"
                    )
                logger.warning(
                    f"Rate limit atingido for {safe_url}. "
                    f"A esperar {retry_after} segundos..."
                )
                time.sleep(retry_after)
                raise _RetryAfterRateLimit()
            
            response.raise_for_status()
            return response
            
        except _RetryAfterRateLimit:
            # Deixa o tenacity repetir (ate MAX_RATE_LIMIT_RETRIES)
            raise
        except Timeout as e:
            logger.error(f"Timeout ao conectar a {safe_url}: {e}")
            raise
        except RequestException as e:
            logger.error(f"Erro na request for {safe_url}: {e}")
            raise
        except Exception as e:
            if response is not None:
                response.close()
            logger.error(f"Erro inesperado em request for {safe_url}: {e}")
            raise

    def _parse_retry_after(self, value: Any) -> Optional[int]:
        """Interpretar o header Retry-After (segundos ou data HTTP)."""
        if value is None:
            return self.RATE_LIMIT_WAIT
        try:
            seconds = int(str(value).strip())
        except (TypeError, ValueError):
            logger.warning(f"Retry-After invalido: {value!r}. A usar {self.RATE_LIMIT_WAIT}s")
            return self.RATE_LIMIT_WAIT
        # Teto defensivo: nao bloquear a thread por horas
        return max(1, min(seconds, self.RATE_LIMIT_WAIT))
    
    @staticmethod
    def _iter_sse_openai_style(response, provider: str):
        """Iterar um stream SSE estilo OpenAI (`data: {...}`)."""
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
        """Iterar um stream SSE da Anthropic."""
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
        """Iterar um stream SSE da Cohere."""
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

    def _validate_messages(self, messages: List[Dict[str, str]]) -> bool:
        """Validar formato das mensagens"""
        if not isinstance(messages, list):
            logger.error("Messages deve ser a lista")
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
        Enviar messages for a API de IA e get resposta
        
        Args:
            messages: Lista de messages no formato {"role": "user", "content": "..."}
            provider: Provider a usar (None = default)
            model: Modelo a usar (None = default do provedor)
            temperature: Temperature for generation
            max_tokens: Maximum number of tokens
            
        Returns:
            Resposta da IA ou None em caso de erro
        """
        # Validar input
        if not self._validate_messages(messages):
            logger.error("Invalid message format")
            return None
        
        if not messages:
            logger.error("Lista de messages vazia")
            return None
        
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        
        if not api_config:
            logger.error(f"Provedor desconhecido: {provider}")
            return None
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)
        
        if not api_key and provider != "local_llm":
            logger.error(f"API key not configured for {provider}")
            return None
        
        try:
            logger.info(f"Enviando request for {provider} with modelo {model}")

            # Total acumulado antes da chamada, para saber se o provider
            # reportou uso real (e evitar contar a estimativa em duplicado).
            with self._usage_lock:
                before = self.token_usage.get(provider, {}).get("total", 0)

            # Call the provider's specific method
            chat_method = getattr(self, f"_chat_{provider}", None)
            if chat_method:
                result = chat_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                logger.error(f"Unsupported provider: {provider}")
                return None

            with self._usage_lock:
                after = self.token_usage.get(provider, {}).get("total", 0)

            if result:
                if after > before:
                    logger.info(f"Resposta recebida ({after - before} tokens, uso da API)")
                else:
                    # Provider nao reportou uso: estimar.
                    input_text = " ".join([msg.get("content", "") for msg in messages])
                    output_tokens = self._count_tokens(result)
                    self._update_token_usage(provider, self._count_tokens(input_text), output_tokens)
                    logger.info(f"Resposta recebida (~{output_tokens} tokens, estimativa)")

            return result
            
        except Exception as e:
            logger.error(f"Erro ao comunicar with {provider}: {e}", exc_info=True)
            return None
    
    def _chat_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                         base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with OpenRouter API"""
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
            logger.error(f"Erro a parsear JSON da OpenRouter: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from OpenRouter: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na OpenRouter: {e}")
            return None
    
    def _google_safety_settings(self):
        """Definicoes de seguranca da Google.

        Por omissao NAO enviamos `safetySettings`: a API aplica os seus
        proprios filtros. `disable_safety_filters: true` no config permite
        desliga-los explicitamente.
        """
        if not self.config.get("api.providers.google_ai_studio.disable_safety_filters", False):
            return []
        logger.warning("Filtros de seguranca da Google AI Studio desativados por configuracao")
        return [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

    def _chat_google_ai_studio(self, messages: List[Dict[str, str]], model: str, api_key: str,
                               base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with Google AI Studio API"""
        url = f"{base_url}/models/{model}:generateContent?key={api_key}"
        
        # Converter messages for the formato da Google
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
            response = self._make_request(url, payload, timeout=timeout)
            try:
                data = response.json()
            finally:
                response.close()

            self._record_usage("google_ai_studio", data)

            if not data.get("candidates"):
                logger.warning("Nenhum candidato na response da Google AI Studio")
                return ""

            return data["candidates"][0]["content"]["parts"][0]["text"]
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON da Google AI Studio: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Google AI Studio: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na Google AI Studio: {e}")
            return None
    
    @staticmethod
    def _split_anthropic_messages(messages: List[Dict[str, str]]):
        """Separar o prompt de sistema e converter os restantes papeis.

        A Anthropic exige `system` como campo de topo e apenas aceita
        `user`/`assistant` no array `messages`.
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
        """Comunicar with Anthropic Claude API"""
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

        # `anthropic-version` e um header obrigatorio; `x-api-key` e a autenticacao.
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
                logger.error(f"Erro da Anthropic: {data.get('error', {}).get('message', 'Unknown')}")
                return None

            # Get content da primeira resposta
            content = data.get("content")
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "".join(texts)
            if isinstance(content, dict):
                return content.get("text", "")
            if content:
                return ""
            return ""
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON da Anthropic: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Anthropic: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na Anthropic: {e}")
            return None
    
    def _chat_mistral(self, messages: List[Dict[str, str]], model: str, api_key: str,
                      base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with Mistral AI API"""
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
                logger.warning("Nenhuma escolha na response da Mistral")
                return ""

            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON da Mistral: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Mistral: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na Mistral: {e}")
            return None
    
    def _chat_groq(self, messages: List[Dict[str, str]], model: str, api_key: str,
                   base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with Groq API"""
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
                logger.warning("Nenhuma escolha na response do Groq")
                return ""

            return data["choices"][0]["message"]["content"]
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON do Groq: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Groq: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro no Groq: {e}")
            return None
    
    def _chat_cohere(self, messages: List[Dict[str, str]], model: str, api_key: str,
                     base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with Cohere API"""
        url = f"{base_url}/chat"
        
        # Converter messages for the formato da Cohere
        cohere_messages = []
        for msg in messages:
            role = msg["role"]
            # A Cohere usa "USER", "ASSISTANT", "SYSTEM"
            role = role.upper()
            cohere_messages.append({
                "role": role,
                "message": msg["content"]
            })
        
        payload = {
            "model": model,
            "messages": cohere_messages,
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

            self._record_usage("cohere", data)

            if not data.get("response"):
                logger.warning("Nenhuma response da Cohere")
                return ""

            return data["response"]
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON da Cohere: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from Cohere: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na Cohere: {e}")
            return None
    
    def _chat_local_llm(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with modelo local (Ollama, etc)"""
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
            logger.error(f"Erro a parsear JSON do modelo local: {e}")
            return None
        except KeyError as e:
            logger.error(f"Invalid response format from local model: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro no modelo local: {e}")
            return None
    
    def stream_chat(self, messages: List[Dict[str, str]], provider: str = None, model: str = None,
                    temperature: float = 0.7, max_tokens: int = 2000):
        """
        Stream de response da IA (gerador)
        
        Args:
            messages: Lista de mensagens
            provider: Provider a usar
            model: Modelo a usar
            temperature: Temperatura
            max_tokens: Maximum tokens
            
        Yields:
            Chunks of the response as they arrive
        """
        # Validar input
        if not self._validate_messages(messages):
            yield "Invalid message format"
            return
        
        if not messages:
            yield "Lista de messages vazia"
            return
        
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        
        if not api_config:
            yield "Provedor desconhecido"
            return
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", self.DEFAULT_TIMEOUT)
        
        try:
            # Call the provider's specific stream method
            stream_method = getattr(self, f"_stream_{provider}", None)
            if stream_method:
                yield from stream_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                # Fallback: make a normal request and yield all content
                response = self.chat(messages, provider, model, temperature, max_tokens)
                if response:
                    yield response
        except Exception as e:
            logger.error(f"Erro no stream for {provider}: {e}", exc_info=True)
            yield f"Erro: {e}"
    
    def _stream_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                           base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with OpenRouter"""
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
                yield from self._iter_sse_openai_style(response, "openrouter")
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Erro no stream OpenRouter: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_anthropic(self, messages: List[Dict[str, str]], model: str, api_key: str,
                          base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Anthropic Claude"""
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
            logger.error(f"Erro no stream Anthropic: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_mistral(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Mistral AI"""
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
                yield from self._iter_sse_openai_style(response, "mistral")
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Erro no stream Mistral: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_groq(self, messages: List[Dict[str, str]], model: str, api_key: str,
                      base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Groq"""
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
                yield from self._iter_sse_openai_style(response, "groq")
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Erro no stream Groq: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_cohere(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Cohere"""
        url = f"{base_url}/chat"
        
        cohere_messages = []
        for msg in messages:
            role = msg["role"].upper()
            cohere_messages.append({
                "role": role,
                "message": msg["content"]
            })
        
        payload = {
            "model": model,
            "messages": cohere_messages,
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
                yield from self._iter_sse_cohere(response)
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Erro no stream Cohere: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_local_llm(self, messages: List[Dict[str, str]], model: str, api_key: str,
                          base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with modelo local"""
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
                yield from self._iter_sse_openai_style(response, "local_llm")
            finally:
                response.close()
        except Exception as e:
            logger.error(f"Erro no stream do modelo local: {e}")
            yield f"Erro no stream: {e}"
