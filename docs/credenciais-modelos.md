# Credenciais e seleção de modelos

O separador **Settings → API** permite editar a chave guardada de cada fornecedor, escolher onde a guardar e definir o modelo remoto. Abrir as definições não copia uma chave do ambiente nem consulta listas de modelos pela rede.

## Origem da chave utilizada

A resolução de uma chave segue esta ordem:

| Prioridade | Origem | Comportamento |
| --- | --- | --- |
| 1 | Variável canónica `LINUX_AI_API_PROVIDERS_<FORNECEDOR>_API_KEY` | A presença da variável sobrepõe o armazenamento, incluindo um valor vazio. |
| 2 | Variável legada não vazia | `OPENROUTER_API_KEY` ou `GOOGLE_AI_STUDIO_KEY`, nos fornecedores correspondentes. |
| 3 | Armazenamento escolhido para o fornecedor | `config.json` ou Linux Secret Service. |

Por exemplo, `LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY` definida com valor vazio impede a utilização da chave guardada de OpenRouter. Uma `OPENROUTER_API_KEY` vazia permite consultar o armazenamento. O ficheiro `.env`, junto de `config.json`, é carregado sem substituir variáveis já presentes no processo; esta regra aplica-se a cada nome, e a prioridade canónica continua a aplicar-se entre nomes diferentes.

O campo da chave mostra o valor **guardado**, ignorando a sobreposição do ambiente. O aviso identifica o nome da variável ativa. O fornecedor predefinido continua a ser uma escolha separada da origem da sua credencial.

O template `.env.example` conserva as variáveis de chaves não utilizadas como comentários. Instalações anteriores podem ter copiado atribuições canónicas vazias. O aviso identifica este bloqueio e o botão **Usar chave guardada (remover substituição vazia do .env)** permite remover explicitamente a atribuição. Só fica disponível para uma variável vazia carregada pela aplicação do seu próprio `.env`, se o ficheiro continuar intacto. As restantes linhas são preservadas e a substituição é atómica, com modo `0600`. Variáveis herdadas do terminal ou ficheiros entretanto alterados devem ser corrigidos na sua origem e exigem reiniciar a aplicação.

## Editar, copiar e limpar

Ao mudar de fornecedor, o diálogo conserva o rascunho de cada chave no fornecedor correspondente. **OK** grava os rascunhos alterados; **Reload saved keys** descarta o rascunho do fornecedor visível e volta a ler o valor guardado. Uma falha ao ler um cofre bloqueado ou uma chave cifrada não transforma o campo vazio numa instrução de limpeza automática.

**Copy effective key to selected storage** copia explicitamente o valor efetivo do ambiente para o armazenamento já ativo. Se o seletor aponta para outro armazenamento, é necessário migrar primeiro. O botão fica desativado quando a variável está vazia, para não apagar uma chave guardada ao tentar resolver o bloqueio. Sem este botão, guardar outras definições não persiste a chave do ambiente.

Para limpar uma chave guardada, apaga o conteúdo do campo e grava com **OK**. Com Secret Service ativo, esta ação elimina o item correspondente no cofre e verifica a sua ausência. Uma sobreposição do ambiente continua a ter prioridade até ser removida do processo ou do `.env` e a aplicação ser reiniciada.

Os botões **Copy**, **Move**, **Unlock** e de remoção da substituição vazia efetuam ações quando são premidos. **Cancelar** descarta os rascunhos ainda não gravados; não desfaz essas ações explícitas já concluídas.

## Linux Secret Service

O backend é opcional e usa SecretStorage diretamente, sem escolher backends alternativos de `keyring`. Num checkout com o ambiente Python da aplicação ativo:

```bash
python -m pip install '.[secret-service]'
```

É necessário um serviço compatível com `org.freedesktop.secrets`, ligado ao bus da sessão do utilizador, e uma coleção persistente com o alias `default`. A aplicação não cria uma coleção em falta e não utiliza uma coleção temporária ou outra coleção disponível como substituta. Configura o cofre pelo gestor de credenciais do ambiente gráfico quando esse alias não existe. Sessões sem serviço ou sem bus de sessão, incluindo instalações headless, podem continuar a usar o armazenamento em configuração ou o ambiente.

As leituras nunca pedem desbloqueio. Se o cofre estiver bloqueado, a chave desse backend fica indisponível e não há fallback silencioso para uma antiga cópia no JSON. **Unlock system key store** é a ação explícita que pode abrir o pedido de autenticação do ambiente gráfico. A gravação e a eliminação exigem um cofre já desbloqueado; o serviço pode apresentar pedidos adicionais próprios dessas ações explícitas.

Cada item tem atributos exatos de aplicação, perfil e fornecedor. O perfil é um identificador guardado em `app.credential_profile`, criado por uma ação explícita de desbloqueio ou migração. Itens com atributos adicionais ou de outro perfil não são lidos nem alterados. Mais de um item exatamente correspondente produz um erro, em vez de escolher ou eliminar um deles automaticamente.

O cofre reduz a exposição de credenciais em cópias de `config.json` e permite o bloqueio gerido pela sessão. Não impede que um processo comprometido com acesso à mesma sessão leia credenciais de um cofre desbloqueado. As mensagens da aplicação não incluem a chave nem o texto bruto de erros do backend.

O manifesto Flatpak atual não inclui a dependência SecretStorage nem a permissão de bus `org.freedesktop.secrets`. Este backend requer esses dois elementos numa build Flatpak; a sua disponibilidade não é garantida pelo manifesto existente. A encriptação em JSON e o ambiente seguem as respetivas dependências e permissões da build utilizada.

## Mover uma chave entre armazenamentos

Escolhe o destino no seletor e usa **Move stored key to selected storage**. O seletor, por si só, não migra a chave. Guarda ou recarrega um rascunho editado antes de iniciar a migração.

Para mover de configuração para Secret Service:

1. Desbloqueia explicitamente o cofre.
2. Seleciona **Linux Secret Service** e prime **Move stored key to selected storage**.
3. A aplicação copia a chave guardada, verifica a leitura do item criado ou atualizado e só depois grava o marcador `key_storage: secret-service` e esvazia o campo `api_key` no JSON.

A migração ignora a chave efetiva do ambiente. Se a verificação do destino falhar, conserva a chave original. Se a escrita no cofre for verificada mas a gravação da configuração falhar, mantém a seleção anterior e a cópia já verificada no cofre; esta cópia não é apagada como tentativa de rollback. Corrige a falha de gravação e repete a migração.

A operação inversa lê a chave guardada no cofre, grava-a no JSON segundo a política de encriptação e muda o backend para `config`. Conserva a cópia no cofre. Uma eliminação do cofre exige a ação de limpeza com esse backend ativo; não é consequência automática de voltar ao JSON.

## Encriptação opcional do JSON

Sem encriptação, o backend `config` guarda a chave no JSON. A gravação cria o ficheiro com modo `0600` e tenta proteger o diretório com `0700`. A encriptação opcional usa Fernet e uma `.encryption_key` junto do ficheiro de configuração. Uma cópia apenas do JSON deixa de conter a chave API em texto simples; acesso ao JSON e à `.encryption_key` permite decifrá-la.

Instala o extra no ambiente da aplicação:

```bash
python -m pip install '.[encryption]'
```

A API `ConfigManager.enable_encryption()` converte todas as chaves guardadas no backend JSON, ignorando sobreposições do ambiente e itens de Secret Service. Prepara a conversão de todas as chaves antes de alterar os seus valores. Alterar manualmente apenas `app.encryption_enabled` não substitui esta conversão.

Com a aplicação fechada, a conversão pode ser efetuada sem imprimir credenciais:

```bash
python - <<'PY'
from src.config_manager import ConfigManager

config = ConfigManager()
config.enable_encryption(True)
if not config.flush():
    raise SystemExit('Não foi possível gravar a configuração.')
print('Encriptação gravada.')
PY
```

As novas gravações cifradas usam `fernet:v1:<token>`. A leitura mantém compatibilidade com tokens Fernet antigos sem prefixo e com o formato antigo que aplicava uma segunda camada de base64. Dados cifrados inválidos ou uma chave de encriptação indisponível são tratados como credencial indisponível, não como texto a enviar à API.

Uma falha criptográfica bloqueia a gravação da credencial; não grava texto simples como fallback. Se já houver credenciais cifradas e a `.encryption_key` desaparecer, a aplicação não gera uma substituta. Restaura a chave correspondente a partir de uma cópia privada antes de editar, migrar ou desativar a encriptação. Não anexes JSON, `.env` ou `.encryption_key` a relatórios de ensaios.

## Modelos remotos

O campo **Remote model** aceita um identificador manual. **List models** faz um pedido explícito em background ao fornecedor selecionado. Se não houver sobreposição de ambiente, usa a chave escrita no rascunho; a consulta não a guarda. Uma variável de ambiente ativa mantém a sua prioridade. A resposta acrescenta opções ao seletor sem substituir o texto introduzido; a escolha só é guardada em **OK**. Alterar a chave durante a consulta cancela o pedido e descarta o resultado antigo.

Não há consulta de modelos remotos no arranque. O modo offline ou local bloqueia a listagem remota, incluindo o modo efetivo imposto pelo ambiente. A consulta aceita apenas endpoints HTTPS conhecidos dos fornecedores suportados, rejeita redirecionamentos e limita prazo, tamanho, número de modelos e paginação. Usa a autenticação do fornecedor escolhido; não envia uma conversa nem descarrega modelos. Um erro de listagem conserva o identificador manual, e uma lista devolvida pela API não confirma preços, quotas ou acesso da conta a uma resposta de chat.

Os valores predefinidos para configurações novas ou campos em falta são:

| Fornecedor | Modelo predefinido |
| --- | --- |
| OpenRouter | `google/gemini-2.5-flash` |
| Google AI Studio direto | `gemini-3.5-flash-lite` |
| Anthropic | `claude-haiku-4-5-20251001` |
| Mistral | `mistral-small-latest` |
| Groq | `openai/gpt-oss-20b` |
| Cohere | `command-r-08-2024` |

Escolhas existentes são preservadas. Os IDs do Google direto e de OpenRouter pertencem a catálogos independentes; um modelo disponível num deles pode não existir no outro. A disponibilidade deve ser confirmada na conta utilizada. Esta atualização não constitui um ensaio de cada modelo com credenciais reais.

## Verificação manual

Usa credenciais fictícias nas verificações locais e uma conta de ensaio apenas quando um pedido real ao fornecedor for necessário. O auxiliar seguinte mostra presença e origem, nunca os valores, e não consulta o cofre:

```bash
python - <<'PY'
import json
import os
from pathlib import Path
from dotenv import load_dotenv

path = Path.home() / '.config' / 'linux_ai_assistant' / 'config.json'
load_dotenv(path.parent / '.env', override=False)
data = json.loads(path.read_text(encoding='utf-8'))
legacy = {'openrouter': 'OPENROUTER_API_KEY', 'google_ai_studio': 'GOOGLE_AI_STUDIO_KEY'}
print('default_provider:', data['api']['default_provider'])
for provider, record in data['api']['providers'].items():
    canonical = 'LINUX_AI_API_PROVIDERS_{}_API_KEY'.format(provider.upper())
    old_name = legacy.get(provider)
    variable = canonical if canonical in os.environ else (
        old_name if old_name and os.environ.get(old_name) else None
    )
    storage = record.get('key_storage', 'config')
    print({
        'provider': provider,
        'storage': storage,
        'origin': variable or storage,
        'environment_key_present': bool(os.environ.get(variable)) if variable else False,
        'json_key_present': bool(record.get('api_key')),
    })
PY
```

`json_key_present` indica apenas um campo não vazio, que pode conter texto cifrado; não confirma que seja decifrável. Um item de Secret Service não aparece nesse campo. Uma origem canónica com `environment_key_present: False` indica a sobreposição vazia, não a utilização automática da chave guardada.

Verifica estes casos em **Menu → Settings → API**:

1. Duas chaves guardadas distintas aparecem nos fornecedores correspondentes. Editar uma, trocar de fornecedor e voltar conserva o rascunho no fornecedor correto. **OK** guarda só as chaves editadas; cancelar sem usar botões explícitos conserva os valores anteriores.
2. Uma variável de ambiente ativa produz o aviso, mas o campo continua a mostrar a chave guardada. Guardar outras definições não copia a variável. O botão **Copy** copia apenas quando solicitado para o backend ativo. Confirma também canónica vazia, legada vazia e canónica com prioridade sobre legada.
3. Um cofre bloqueado não apresenta pedidos ao abrir o diálogo ou resolver uma chave. **Unlock** pode apresentar autenticação; uma recusa conserva as credenciais. A migração usa a chave guardada, verifica o destino e retira a chave do JSON apenas após sucesso.
4. Falhas de escrita/verificação, chave Fernet ausente e dados cifrados danificados mantêm a credencial anterior ou comunicam indisponibilidade; não produzem uma cópia em texto simples. Simula falhas com os testes, evitando destruir a configuração de utilização diária.
5. **List models** só contacta o fornecedor após o clique. Usa a chave escrita no diálogo sem a guardar, mantendo a prioridade de uma variável de ambiente ativa. Conserva um ID manual e só grava o modelo escolhido com **OK**. Falhas e alterações da chave, fornecedor ou modo durante a consulta não aplicam uma resposta antiga. O modo local/offline bloqueia o pedido. Erros HTTP de autenticação, permissões, quota e serviço são apresentados sem copiar chaves nem respostas brutas do fornecedor.

Os testes automáticos usam cofres falsos, configurações temporárias e respostas HTTP sintéticas:

```bash
python -m unittest tests.test_credential_store tests.test_credential_config tests.test_credential_encryption tests.test_remote_models -v
```

Os testes da interface precisam de GTK e de uma sessão gráfica ou Xvfb. A suite GTK do CI rejeita skips inesperados; ensaios com um cofre do ambiente gráfico ou com uma API real continuam a exigir verificação local e revisão das evidências segundo o [protocolo de ensaios](protocolo-ensaios-reais.md).
