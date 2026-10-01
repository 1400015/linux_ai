import json
import time
import logging
from typing import Optional, Dict, Any, List, Callable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import RequestException, Timeout, ConnectionError

# Configurar logger
logger = logging.getLogger(__name__)


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
        self._setup_retry_strategy()
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
        """Configure retry strategy"""
        self.retry_decorator = retry(
            stop=stop_after_attempt(self.MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=4, max=10),
            retry=retry_if_exception_type((RequestException, Timeout, ConnectionError)),
            reraise=True
        )
    
    def _load_custom_providers(self):
        """Carregar provedores customizados de plugins"""
        try:
            from pathlib import Path
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
        if provider not in self.token_usage:
            self.token_usage[provider] = {"input": 0, "output": 0, "total": 0}
        self.token_usage[provider]["input"] += input_tokens
        self.token_usage[provider]["output"] += output_tokens
        self.token_usage[provider]["total"] += input_tokens + output_tokens
        logger.debug(f"Token usage - {provider}: input={input_tokens}, output={output_tokens}")
    
    def get_token_usage(self, provider: str = None) -> Dict[str, Any]:
        """Obter uso de tokens"""
        if provider:
            return self.token_usage.get(provider, {"input": 0, "output": 0, "total": 0})
        return self.token_usage
    
    def reset_token_usage(self):
        """Resetar contagem de tokens"""
        self.token_usage = {}
        logger.info("Token usage reset")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10),
           retry=retry_if_exception_type((RequestException, Timeout, ConnectionError)))
    def _make_request(self, url: str, payload: Dict, headers: Dict = None, 
                     timeout: int = None, stream: bool = False):
        """Fazer request with retry e error handling"""
        timeout = timeout or self.DEFAULT_TIMEOUT
        merged_headers = {**self.session.headers, **(headers or {})}
        
        try:
            if stream:
                response = self.session.post(
                    url, json=payload, headers=merged_headers, timeout=timeout, stream=True
                )
            else:
                response = self.session.post(
                    url, json=payload, headers=merged_headers, timeout=timeout
                )
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', self.RATE_LIMIT_WAIT))
                logger.warning(f"Rate limit atingido for {url}. A esperar {retry_after} segundos...")
                time.sleep(retry_after)
                # Retry manually
                return self._make_request(url, payload, headers, timeout, stream)
            
            response.raise_for_status()
            return response
            
        except Timeout as e:
            logger.error(f"Timeout ao conectar a {url}: {e}")
            raise
        except RequestException as e:
            logger.error(f"Erro na request for {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Erro inesperado em request for {url}: {e}")
            raise
    
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
            # Contar tokens de input
            input_text = " ".join([msg.get("content", "") for msg in messages])
            input_tokens = self._count_tokens(input_text)
            
            logger.info(f"Enviando request for {provider} with modelo {model}")
            
            # Call the provider's specific method
            chat_method = getattr(self, f"_chat_{provider}", None)
            if chat_method:
                result = chat_method(messages, model, api_key, base_url, temperature, max_tokens, timeout)
            else:
                logger.error(f"Unsupported provider: {provider}")
                return None
            
            # Contar tokens de output e update uso
            if result:
                output_tokens = self._count_tokens(result)
                self._update_token_usage(provider, input_tokens, output_tokens)
                logger.info(f"Resposta recebida ({output_tokens} tokens)")
            
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
            data = response.json()
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
                "maxOutputTokens": max_tokens,
                "stopSequences": ["\n\n"]
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }
        
        try:
            response = self._make_request(url, payload, timeout=timeout)
            data = response.json()
            
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
    
    def _chat_anthropic(self, messages: List[Dict[str, str]], model: str, api_key: str,
                        base_url: str, temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar with Anthropic Claude API"""
        # Get version da API do modelo
        api_version = "2023-06-01"  # Default for Claude
        if "claude-3" in model:
            api_version = "2024-03-07"
        
        url = f"{base_url}/messages"
        
        # Converter messages for the formato da Anthropic
        anthropic_messages = []
        for msg in messages:
            role = msg["role"]
            # Anthropic uses "user" and "assistant" (not "system")
            if role == "system":
                role = "assistant"  # ou create a message de system separada
            anthropic_messages.append({
                "role": role,
                "content": msg["content"]
            })
        
        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "anthropic_version": api_version
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "x-api-key": api_key
        }
        
        try:
            response = self._make_request(url, payload, headers, timeout)
            data = response.json()
            
            if data.get("type") == "error":
                logger.error(f"Erro da Anthropic: {data.get('error', {}).get('message', 'Unknown')}")
                return None
            
            # Get content da primeira resposta
            if data.get("content"):
                # Se for a lista, pegar o primeiro
                if isinstance(data["content"], list):
                    for content in data["content"]:
                        if content.get("type") == "text":
                            return content.get("text", "")
                else:
                    return data["content"].get("text", "")
            
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
            data = response.json()
            
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
            data = response.json()
            
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
            data = response.json()
            
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
    
    def _chat_local_llm(self, messages: List[Dict[str, str]], model: str, base_url: str,
                        temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
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
            data = response.json()
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
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    content = data["choices"][0].get("delta", {}).get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(f"Erro no stream OpenRouter: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_anthropic(self, messages: List[Dict[str, str]], model: str, api_key: str,
                          base_url: str, temperature: float, max_tokens: int, timeout: int):
        """Stream with Anthropic Claude"""
        api_version = "2023-06-01"
        if "claude-3" in model:
            api_version = "2024-03-07"
        
        url = f"{base_url}/messages"
        
        anthropic_messages = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                role = "assistant"
            anthropic_messages.append({
                "role": role,
                "content": msg["content"]
            })
        
        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "anthropic_version": api_version,
            "stream": True
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "x-api-key": api_key
        }
        
        try:
            response = self._make_request(url, payload, headers, timeout, stream=True)
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if data.get("type") == "message_delta":
                                    if data.get("delta", {}).get("type") == "text_delta":
                                        yield data["delta"]["text"]
                            except json.JSONDecodeError:
                                continue
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
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    content = data["choices"][0].get("delta", {}).get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
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
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    content = data["choices"][0].get("delta", {}).get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
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
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if "text" in data:
                                    yield data["text"]
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(f"Erro no stream Cohere: {e}")
            yield f"Erro no stream: {e}"
    
    def _stream_local_llm(self, messages: List[Dict[str, str]], model: str, base_url: str,
                          temperature: float, max_tokens: int, timeout: int):
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
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    content = data["choices"][0].get("delta", {}).get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(f"Erro no stream do modelo local: {e}")
            yield f"Erro no stream: {e}"
