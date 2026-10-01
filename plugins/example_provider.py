"""
Exemplo de plugin para adicionar um novo provedor de IA

Este plugin demonstra como adicionar um novo provedor customizado.
Para usar este plugin:
1. Renomeie este ficheiro para o nome do seu provedor
2. Implemente a função de chat
3. Atualize o config.json com as configurações do provedor
"""

import json
import logging

logger = logging.getLogger(__name__)


def register_provider(ai_client):
    """
    Registrar um novo provedor customizado
    
    Args:
        ai_client: Instância do AIClient
    """
    
    def my_custom_provider_chat(messages, model, api_key, base_url, temperature, max_tokens, timeout):
        """
        Implementação de chat para o provedor customizado
        
        A assinatura deve coincidir exatamente com a que o AIClient invoca:
        (messages, model, api_key, base_url, temperature, max_tokens, timeout)
        
        Args:
            messages: Lista de mensagens
            model: Modelo a usar
            api_key: API key (None se o provedor não tem entrada em config.json)
            base_url: URL base (None se o provedor não tem entrada em config.json)
            temperature: Temperatura
            max_tokens: Máximo de tokens
            timeout: Timeout em segundos
            
        Returns:
            Resposta da IA ou None
        """
        # Exemplo: Usar uma API REST customizada
        url = f"{base_url}/chat" if base_url else "https://api.meu-provedor.com/v1/chat"
        
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
            import requests
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            
            # Extrair resposta (ajustar conforme o formato da API)
            return data.get("response", "")
            
        except Exception as e:
            logger.error(f"Erro no provedor customizado: {e}")
            return None
    
    def my_custom_provider_stream(messages, model, api_key, base_url, temperature, max_tokens, timeout):
        """
        Implementação de stream para o provedor customizado
        
        Args:
            messages: Lista de mensagens
            model: Modelo a usar
            api_key: API key
            base_url: URL base
            temperature: Temperatura
            max_tokens: Máximo de tokens
            timeout: Timeout em segundos
            
        Yields:
            Pedacos da resposta
        """
        # Exemplo: Stream com uma API customizada
        url = f"{base_url}/chat"
        
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
            import requests
            with requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]
                            if data_str != "[DONE]":
                                try:
                                    data = json.loads(data_str)
                                    content = data.get("content", "")
                                    if content:
                                        yield content
                                except json.JSONDecodeError:
                                    continue
        except Exception as e:
            logger.error(f"Erro no stream do provedor customizado: {e}")
            yield f"Erro: {e}"
    
    # Registrar o provedor
    ai_client.register_provider("my_custom_provider", my_custom_provider_chat)
    
    # Registrar método de stream (opcional): o AIClient procura
    # `_stream_<nome_do_provedor>` em stream_chat()
    ai_client._stream_my_custom_provider = my_custom_provider_stream
    
    logger.info("Provedor customizado registado: my_custom_provider")


# Esta função será chamada automaticamente pelo AIClient
# quando o plugin for carregado
