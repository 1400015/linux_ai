# API de Plugins — linux_ai

Este documento descreve o contrato que um plugin deve cumprir para adicionar um
novo fornecedor de IA (provider) ao assistente. É a referência para autores de
plugins; o exemplo completo vive em [`plugins/example_provider.py`](../plugins/example_provider.py).

## ⚠️ Modelo de confiança

Um plugin é **código arbitrário executado com os privilégios do utilizador** no
arranque da aplicação. Por isso o carregamento é **opt-in**:

1. Coloca o ficheiro `.py` em `plugins/` (raiz do projeto ou instalação).
2. Adiciona o nome do ficheiro **sem extensão** a `plugins.enabled` no
   `config.json` (ou `~/.config/linux_ai_assistant/config.json`):

   ```json
   "plugins": {
       "enabled": ["meu_provedor"]
   }
   ```

Sem este passo o ficheiro **não é importado nem executado**. O diretório
`plugins/` é a fronteira de confiança: se estiver gravável por grupo/outros, a
aplicação regista um aviso no log.

## Contrato mínimo

O ficheiro do plugin deve definir uma função `register_provider(ai_client)` que
recebe a instância de `AIClient` e regista o novo fornecedor:

```python
def register_provider(ai_client):
    ai_client.register_provider("meu_provedor", minha_funcao_chat)
    # Opcional: streaming
    ai_client._stream_meu_provedor = minha_funcao_stream
```

### `register_provider(name, chat_func)`

- `name` (str): identificador do fornecedor, em minúsculas, sem espaços
  (ex.: `meu_provedor`). Aparece na lista de fornecedores suportados e pode
  ser selecionado como `api.default_provider`.
- `chat_func` (callable): função de chat **não-streaming**.

A aplicação cria uma cópia da lista de fornecedores por instância
(copy-on-write), pelo que registar um nome não afeta outras instâncias do
`AIClient` no mesmo processo.

### Assinatura da função de chat

A assinatura deve coincidir **exatamente** com a que o `AIClient` invoca:

```python
def meu_chat(messages, model, api_key, base_url, temperature, max_tokens, timeout):
    ...
    return texto_da_resposta_ou_None
```

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `messages` | `List[Dict[str, str]]` | Histórico no formato `{"role": "user"/"system"/"assistant", "content": "..."}` |
| `model` | `str` | Modelo selecionado (vem da config ou do pedido) |
| `api_key` | `Optional[str]` | Chave de API (pode ser `None` se o fornecedor não tiver entrada em `config.json`) |
| `base_url` | `Optional[str]` | URL base (pode ser `None` se não houver entrada na config) |
| `temperature` | `float` | Temperatura de geração (padrão 0.7) |
| `max_tokens` | `int` | Máximo de tokens de saída (padrão 2000) |
| `timeout` | `float` | Timeout em segundos (vem da config do fornecedor, padrão do cliente) |

**Retorno**: `str` com o texto da resposta, ou `None` em caso de erro.

### Assinatura da função de stream (opcional)

Se registares também `_stream_<nome>`, o `AIClient` usa-a em `stream_chat()`
em vez de fazer um pedido normal e devolver o texto de uma vez:

```python
def meu_stream(messages, model, api_key, base_url, temperature, max_tokens, timeout):
    ...
    yield chunk_de_texto
```

- `Yields`: `str` — pedaços de texto à medida que chegam.
- Se não registares `_stream_<nome>`, o `AIClient` faz fallback: chama a função
  de chat normal e devolve (`yield`) a resposta completa de uma vez.
- Um stream que não produza **nenhum** chunk levanta `AIProviderError("Empty
  response from <nome>")` — não deixes o gerador terminar em silêncio.

## Tratamento de erros (obrigatório)

**Nunca** devolvas erros como texto do modelo. O contrato de erros é por
exceção:

| Situação | Exceção a levantar |
|----------|-------------------|
| Falha de rede, HTTP, parsing | `AIProviderError` |
| Fornecedor sem chave/base_url | `ProviderNotConfigured` |
| Pedido cancelado pelo utilizador | `AIRequestCancelled` |
| Resposta excede limites locais | `AIResponseLimitError` |
| Pedido de imagem não suportado | `AIImageRequestError` |

Todas estas classes vivem em `src/ai_client.py` e herdam de
`requests.exceptions.RequestException` (via `AIProviderError`), o que preserva
a política de retry do cliente. Erros de comunicação **nunca** viajam como
chunks de texto — a UI distingue erros de conteúdo por tipo, não por prefixo.

```python
from src.ai_client import AIProviderError

try:
    ...
except requests.RequestException as e:
    raise AIProviderError(str(e)) from e
```

### Regras de logging e privacidade

- **Nunca** registes no log URLs com query strings que possam conter chaves
  (`?key=...`, `?api_key=...`). Usa `redact_url()`:

  ```python
  from src.ai_client import redact_url
  logger.error("Falha no fornecedor: %s", redact_url(str(e)))
  ```

- Não imprimas corpos de resposta de erro que possam conter dados do
  utilizador. O cliente limita e redige corpos de erro; o plugin deve fazer o
  mesmo nos seus próprios logs.

- Não uses `logger.exception()` com `exc_info=True` em exceções de rede: o
  traceback do `requests` inclui a URL original.

## Configuração do fornecedor

Um plugin pode funcionar de duas formas:

### 1. Sem entrada em `config.json` (autónomo)

O plugin usa `base_url`/`api_key` hardcoded ou de variáveis de ambiente. O
`AIClient` deteta o fornecedor como pronto porque existe `_chat_<nome>`
registado (ver `_provider_configured`).

### 2. Com entrada em `config.json` (recomendado)

Adiciona uma entrada em `api.providers` para que o utilizador possa configurar
chave, URL, modelo e timeout pelo diálogo **Settings → API**:

```json
"api": {
    "providers": {
        "meu_provedor": {
            "api_key": "",
            "base_url": "https://api.meu-provedor.com/v1",
            "model": "meu-modelo-v1",
            "timeout": 60
        }
    }
}
```

Neste caso `api_key`, `base_url`, `model` e `timeout` são passados à tua função
já preenchidos. O diálogo de settings só mostra fornecedores em
`SUPPORTED_PROVIDERS` — o teu plugin fica visível automaticamente após
`register_provider()`.

## Pedidos de imagem (opcional)

Se o teu fornecedor suporta imagens, regista também:

```python
ai_client._chat_image_meu_provedor = minha_funcao_imagem
```

A função recebe `(messages, images, model, api_key, base_url, temperature,
max_tokens, timeout)` e deve devolver `str` ou levantar `AIImageRequestError`
se não suportar imagens. **Nunca** faças fallback para texto/OCR em pedidos de
imagem — levanta a exceção para que a UI a apresente claramente.

## API do `AIClient` útil para plugins

| Método | Descrição |
|--------|-----------|
| `register_provider(name, chat_func)` | Regista o fornecedor e a função de chat |
| `get_supported_providers()` | Lista os nomes de fornecedores (inclui os do plugin) |
| `provider_ready(provider)` | `True` se o fornecedor está configurado (sem I/O) |
| `provider_status(provider, check_connection=False)` | Estado estruturado (`ProviderStatus`) |
| `active_provider(provider)` | Resolve o fornecedor ativo sem rede |
| `get_token_usage(provider)` | Tokens consumidos por fornecedor |
| `flush_usage()` | Persiste o ficheiro de usage |

O plugin recebe a instância completa, pelo que pode consultar ou até estender
estes métodos.

## Checklist de publicação

- [ ] Ficheiro em `plugins/<nome>.py` com `register_provider(ai_client)`.
- [ ] Nome do plugin adicionado a `plugins.enabled` na config do utilizador.
- [ ] Função de chat com a assinatura exata de 7 parâmetros.
- [ ] Erros levantados como exceções `AIProviderError` (nunca como texto).
- [ ] Stream opcional registado como `_stream_<nome>` (ou omisso para fallback).
- [ ] Logs sem URLs com segredos (`redact_url()`).
- [ ] Entrada opcional em `api.providers` para configuração pelo utilizador.
- [ ] Sem `print()` — usa `logging.getLogger(__name__)`.

## Exemplo mínimo

```python
"""Plugin mínimo: fornecedor 'exemplo' que ecoa a última mensagem."""
import logging

logger = logging.getLogger(__name__)


def register_provider(ai_client):
    def chat(messages, model, api_key, base_url, temperature, max_tokens, timeout):
        if not messages:
            return None
        return "Eco: " + messages[-1].get("content", "")

    ai_client.register_provider("exemplo", chat)
    logger.info("Plugin 'exemplo' registado")
```

Depois de adicionar `"exemplo"` a `plugins.enabled`, o fornecedor aparece nas
settings e pode ser selecionado como predefinido.
