import json
import time
import logging
from typing import Optional, Dict, Any, List
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import RequestException, Timeout, ConnectionError

# Configurar logger
logger = logging.getLogger(__name__)


class AIClient:
    """Cliente para interagir com várias APIs de IA"""
    
    # Constantes
    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3
    RATE_LIMIT_WAIT = 60  # segundos
    
    def __init__(self, config_manager):
        self.config = config_manager
        self.session = self._create_session()
        self.token_usage = {}
        self._setup_retry_strategy()
    
    def _create_session(self):
        """Criar sessão HTTP com headers padrão"""
        session = type('Session', (), {})()  # Placeholder
        import requests
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "LinuxAIAssistant/1.0"
        })
        return session
    
    def _setup_retry_strategy(self):
        """Configurar estratégia de retry"""
        self.retry_decorator = retry(
            stop=stop_after_attempt(self.MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=4, max=10),
            retry=retry_if_exception_type((RequestException, Timeout, ConnectionError)),
            reraise=True
        )
    
    def _get_api_config(self, provider: str) -> Dict[str, Any]:
        """Obter configuração da API para um provedor específico"""
        providers = self.config.get("api.providers", {})
        return providers.get(provider, {})
    
    def _get_api_key(self, provider: str) -> Optional[str]:
        """Obter API key para um provedor"""
        api_config = self._get_api_config(provider)
        return api_config.get("api_key")
    
    def _count_tokens(self, text: str) -> int:
        """Estimar número de tokens (simplificado)"""
        if not text:
            return 0
        # Estimativa: ~4 caracteres por token em média
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
        """Fazer request com retry e error handling"""
        import requests
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
                logger.warning(f"Rate limit atingido para {url}. A esperar {retry_after} segundos...")
                time.sleep(retry_after)
                # Retry manually
                return self._make_request(url, payload, headers, timeout, stream)
            
            response.raise_for_status()
            return response
            
        except Timeout as e:
            logger.error(f"Timeout ao conectar a {url}: {e}")
            raise
        except RequestException as e:
            logger.error(f"Erro na request para {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Erro inesperado em request para {url}: {e}")
            raise
    
    def _validate_messages(self, messages: List[Dict[str, str]]) -> bool:
        """Validar formato das mensagens"""
        if not isinstance(messages, list):
            logger.error("Messages deve ser uma lista")
            return False
        
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                logger.error(f"Mensagem {i} não é um dicionário")
                return False
            if "role" not in msg or "content" not in msg:
                logger.error(f"Mensagem {i} não tem 'role' ou 'content'")
                return False
            if msg["role"] not in ["user", "assistant", "system"]:
                logger.error(f"Role inválido na mensagem {i}: {msg['role']}")
                return False
        
        return True
    
    def chat(self, messages: List[Dict[str, str]], provider: str = None, model: str = None,
             temperature: float = 0.7, max_tokens: int = 2000) -> Optional[str]:
        """
        Enviar mensagens para a API de IA e obter resposta
        
        Args:
            messages: Lista de mensagens no formato {"role": "user", "content": "..."}
            provider: Provedor a usar (None = default)
            model: Modelo a usar (None = default do provedor)
            temperature: Temperatura para geração
            max_tokens: Número máximo de tokens
            
        Returns:
            Resposta da IA ou None em caso de erro
        """
        # Validar input
        if not self._validate_messages(messages):
            logger.error("Formato de mensagens inválido")
            return None
        
        if not messages:
            logger.error("Lista de mensagens vazia")
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
            logger.error(f"API key não configurada para {provider}")
            return None
        
        try:
            # Contar tokens de input
            input_text = " ".join([msg.get("content", "") for msg in messages])
            input_tokens = self._count_tokens(input_text)
            
            logger.info(f"Enviando request para {provider} com modelo {model}")
            
            if provider == "openrouter":
                result = self._chat_openrouter(messages, model, api_key, temperature, max_tokens, timeout)
            elif provider == "google_ai_studio":
                result = self._chat_google_ai_studio(messages, model, api_key, temperature, max_tokens, timeout)
            elif provider == "local_llm":
                result = self._chat_local_llm(messages, model, base_url, temperature, max_tokens, timeout)
            else:
                logger.error(f"Provedor não suportado: {provider}")
                return None
            
            # Contar tokens de output e atualizar uso
            if result:
                output_tokens = self._count_tokens(result)
                self._update_token_usage(provider, input_tokens, output_tokens)
                logger.info(f"Resposta recebida ({output_tokens} tokens)")
            
            return result
            
        except Exception as e:
            logger.error(f"Erro ao comunicar com {provider}: {e}", exc_info=True)
            return None
    
    def _chat_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                         temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar com OpenRouter API"""
        url = "https://openrouter.ai/api/v1/chat/completions"
        
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
            logger.error(f"Formato de resposta inválido da OpenRouter: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na OpenRouter: {e}")
            return None
    
    def _chat_google_ai_studio(self, messages: List[Dict[str, str]], model: str, api_key: str,
                               temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar com Google AI Studio API"""
        url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={api_key}"
        
        # Converter mensagens para o formato da Google
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
                logger.warning("Nenhum candidato na resposta da Google AI Studio")
                return ""
            
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except json.JSONDecodeError as e:
            logger.error(f"Erro a parsear JSON da Google AI Studio: {e}")
            return None
        except KeyError as e:
            logger.error(f"Formato de resposta inválido da Google AI Studio: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro na Google AI Studio: {e}")
            return None
    
    def _chat_local_llm(self, messages: List[Dict[str, str]], model: str, base_url: str,
                        temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar com modelo local (Ollama, etc)"""
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
            logger.error(f"Formato de resposta inválido do modelo local: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro no modelo local: {e}")
            return None
    
    def stream_chat(self, messages: List[Dict[str, str]], provider: str = None, model: str = None,
                    temperature: float = 0.7, max_tokens: int = 2000):
        """
        Stream de resposta da IA (gerador)
        
        Args:
            messages: Lista de mensagens
            provider: Provedor a usar
            model: Modelo a usar
            temperature: Temperatura
            max_tokens: Máximo de tokens
            
        Yields:
            Pedacos da resposta à medida que chegam
        """
        # Validar input
        if not self._validate_messages(messages):
            yield "Formato de mensagens inválido"
            return
        
        if not messages:
            yield "Lista de mensagens vazia"
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
            if provider == "openrouter":
                yield from self._stream_openrouter(messages, model, api_key, temperature, max_tokens, timeout)
            elif provider == "local_llm":
                yield from self._stream_local_llm(messages, model, base_url, temperature, max_tokens, timeout)
            else:
                # Para Google AI Studio, fazer request normal e yield todo o conteúdo
                response = self.chat(messages, provider, model, temperature, max_tokens)
                if response:
                    yield response
        except Exception as e:
            logger.error(f"Erro no stream para {provider}: {e}", exc_info=True)
            yield f"Erro: {e}"
    
    def _stream_openrouter(self, messages: List[Dict[str, str]], model: str, api_key: str,
                           temperature: float, max_tokens: int, timeout: int):
        """Stream com OpenRouter"""
        url = "https://openrouter.ai/api/v1/chat/completions"
        
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
    
    def _stream_local_llm(self, messages: List[Dict[str, str]], model: str, base_url: str,
                          temperature: float, max_tokens: int, timeout: int):
        """Stream com modelo local"""
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
