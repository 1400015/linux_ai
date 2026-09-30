import json
import time
import requests
from typing import Optional, Dict, Any
from .config_manager import ConfigManager


class AIClient:
    """Cliente para interagir com várias APIs de IA"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "LinuxAIAssistant/1.0"
        })
    
    def _get_api_config(self, provider: str) -> Dict[str, Any]:
        """Obter configuração da API para um provedor específico"""
        providers = self.config.get("api.providers", {})
        return providers.get(provider, {})
    
    def _get_api_key(self, provider: str) -> Optional[str]:
        """Obter API key para um provedor"""
        api_config = self._get_api_config(provider)
        return api_config.get("api_key")
    
    def chat(self, messages: list, provider: str = None, model: str = None, 
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
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        
        if not api_config:
            print(f"Provedor desconhecido: {provider}")
            return None
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", 30)
        
        if not api_key and provider != "local_llm":
            print(f"API key não configurada para {provider}")
            return None
        
        try:
            if provider == "openrouter":
                return self._chat_openrouter(messages, model, api_key, temperature, max_tokens, timeout)
            elif provider == "google_ai_studio":
                return self._chat_google_ai_studio(messages, model, api_key, temperature, max_tokens, timeout)
            elif provider == "local_llm":
                return self._chat_local_llm(messages, model, base_url, temperature, max_tokens, timeout)
            else:
                print(f"Provedor não suportado: {provider}")
                return None
        except Exception as e:
            print(f"Erro ao comunicar com {provider}: {e}")
            return None
    
    def _chat_openrouter(self, messages: list, model: str, api_key: str, 
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
            "HTTP-Referer": "https://github.com/yourusername/linux-ai-assistant",
            "X-Title": "Linux AI Assistant"
        }
        
        response = self.session.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
    
    def _chat_google_ai_studio(self, messages: list, model: str, api_key: str,
                               temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar com Google AI Studio API"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        
        # Converter mensagens para o formato da Google
        google_messages = []
        for msg in messages:
            google_messages.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [{"text": msg["content"]}]
            })
        
        payload = {
            "contents": google_messages,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        
        response = self.session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    
    def _chat_local_llm(self, messages: list, model: str, base_url: str,
                        temperature: float, max_tokens: int, timeout: int) -> Optional[str]:
        """Comunicar com modelo local (Ollama, etc)"""
        url = f"{base_url}/chat/completions"
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        response = self.session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
    
    def stream_chat(self, messages: list, provider: str = None, model: str = None,
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
        provider = provider or self.config.get("api.default_provider", "openrouter")
        api_config = self._get_api_config(provider)
        
        if not api_config:
            yield "Provedor desconhecido"
            return
        
        model = model or api_config.get("model")
        api_key = self._get_api_key(provider)
        base_url = api_config.get("base_url")
        timeout = api_config.get("timeout", 30)
        
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
            yield f"Erro: {e}"
    
    def _stream_openrouter(self, messages: list, model: str, api_key: str,
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
            "HTTP-Referer": "https://github.com/yourusername/linux-ai-assistant",
            "X-Title": "Linux AI Assistant"
        }
        
        with self.session.post(url, json=payload, headers=headers, 
                              timeout=timeout, stream=True) as response:
            response.raise_for_status()
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
    
    def _stream_local_llm(self, messages: list, model: str, base_url: str,
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
        
        with self.session.post(url, json=payload, timeout=timeout, stream=True) as response:
            response.raise_for_status()
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
