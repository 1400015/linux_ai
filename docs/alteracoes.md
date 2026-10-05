# Registo de Alterações — linux_ai

## v1.4.0 — Conversas, imagens, atalho e documentos locais (2026-10-05)

- Exportação Markdown com formato próprio versionado e mensagens enquadradas por comprimento; importação de Markdown/JSON após pré-visualização e confirmação, sempre para uma nova conversa e sem recuperar ações ou diagnósticos pendentes. Markdown antigo sem enquadramento continua recusado.
- Uma imagem PNG/JPEG/WebP revista pode acompanhar a próxima pergunta a um modelo OpenRouter explicitamente compatível. O snapshot normalizado remove metadados, tem limites de bytes e dimensões e não é persistido no histórico. Erros de imagem não acionam fallback para outro fornecedor, OCR ou resposta offline.
- Atalho global opcional, desligado por defeito, para apresentar a janela: X11 via libX11 e Wayland via portal GlobalShortcuts. `linux-ai-assistant --show` permite configurar um binding do próprio desktop e apresenta a instância da mesma sessão através de D-Bus. Não lê texto selecionado nem substitui texto noutras aplicações.
- Índice privado de snapshots de ficheiros UTF-8 escolhidos, com pesquisa lexical local, referências por documento/linhas e atualização ou remoção explícita. Uso de trechos numa pergunta exige escolha por pedido; o envio remoto tem revisão separada. Não adiciona embeddings, importação de pastas, PDF ou serviços de indexação remotos.
- Instruções, formatos, limites e verificações manuais no [guia de conversas, imagens e documentos](conversas-imagens-documentos.md).

### Credenciais, segurança e robustez incluídas na v1.4.0

- Encriptação de chaves sem fallback para texto simples, formato Fernet identificado e leitura compatível dos formatos anteriores. Falhas de desencriptação deixam a credencial indisponível; uma chave de encriptação perdida não é regenerada sobre cifras existentes.
- Definições mostram apenas a chave guardada. Gravar OK só persiste alterações explícitas; copiar uma chave do ambiente ou migrar armazenamento exige o respetivo botão. Variáveis canónicas têm precedência sobre nomes legados, incluindo valores vazios.
- Cofre Linux Secret Service opcional, sem desbloqueio no arranque, sem backend alternativo, com propriedade exata dos itens e migração após verificação da escrita.
- Listagem de modelos remotos apenas a pedido, com endpoints específicos, limites e redirecionamentos recusados. Modelos manuais e configurações existentes são preservados; predefinições revistas para novas configurações.
- Recuperação de ecrã num processo separado disponível como opção experimental, desativada por defeito. Ensaios simulados cobrem morte do frontend; campanhas X11/Sway reais continuam pendentes.
- CI de tipos gradual em quatro módulos, incluindo o cofre opcional, e documentação reorganizada. A receita XBPS continua a usar o snapshot 1.3.3; as alterações posteriores não pertencem a esse snapshot.
- Ocultação de segredos reforçada para etiquetas com prefixos como `DB_PASSWORD`, `SECRET_KEY`, `AUTH_TOKEN` e `PGPASSWORD`, JWT, tokens Slack e identificadores de acesso AWS. Diagnósticos e ensaios continuam a exigir revisão antes de partilha.
- Política dos diagnósticos restringe `ip` a consultas enumeradas, bloqueia destruição de sockets e opções perigosas de `file`, e recusa motores de execução conhecidos mesmo se acrescentados à allowlist.
- Respostas limitadas a 131072 caracteres, streams com limites de bytes, frames, eventos e duração; cancelamento propagado à leitura e às esperas de retry. Resultados de operações locais já executadas permanecem na conversa original; cancelar não desfaz alterações.
- Histórico local limitado a 16 MiB e 128 níveis de estrutura. `history recover --yes` conserva todos os bytes num backup privado antes de iniciar um novo histórico, sem interpretar versões desconhecidas.
- `changes archive --yes` liberta capacidade conservando IDs, hashes e referências aos backups; listagem, inspeção e recuperação encontram também os arquivos. O journal verifica a capacidade antes da escrita e distingue publicação incerta de falha sem alteração.
- Helpers dedicados de ficheiros e ativação runit instaláveis separadamente por administrador, com política polkit própria, caminhos absolutos protegidos e sem fallback para código privilegiado do checkout. Ficheiros de autenticação/autorização e chaves privadas sensíveis são recusados pelo helper.
- Listagem de modelos usa leitura em blocos, descompressão limitada e prazo cancelável que inclui espera por cabeçalhos e corpo. A listagem Cohere restringe-se à API v1 suportada pela inferência.
- Uma falha ao gravar o recibo do watchdog deixa de suprimir o resultado verificado enviado ao frontend. Falha simultânea da comunicação mantém a incerteza. README esclarece a opção experimental; anotações de informação do sistema e variável de validação corrigidas.
- Descoberta remota isolada num processo terminável e recolhido, com temporizador próprio se o frontend desaparecer; credenciais circulam apenas pelo pipe privado. Corrupção de YAML desativa a coleção YAML completa sem bloquear o arranque e comunica conhecimento degradado.
- Respostas JSON dos transportes integrados também têm limite de corpo descomprimido e prazo de leitura. Corpos de erro HTTP limitados passam pela ocultação partilhada; corpos incompletos são omitidos para não expor fragmentos de credenciais.
- Os comandos, requisitos de instalação e limites estão no [guia de segurança e recuperação](correcoes-seguranca-robustez.md).

## v1.3.3 — Persistência de tokens e correções de robustez (2026-10-04)

- Repetir uma gravação de utilização de tokens após uma falha de sincronização já não duplica incrementos publicados: um total de 15 tokens permanece 15, em vez de subir para 30.
- A persistência distingue falhas anteriores à substituição do JSON de erros posteriores à publicação. As primeiras conservam os incrementos e o reset pendentes; as segundas consomem o que já foi publicado, mantêm o aviso de durabilidade incerta e repetem a sincronização lendo os totais atuais sob lock, sem reaplicar incrementos ou resets anteriores.
- 22 regressões cobrem falhas de escrita/sincronização/fecho, publicação seguida de erro ao fechar o lock, clientes e processos concorrentes, resets e preservação do erro original durante a limpeza. O conteúdo publicado não é incluído na mensagem da exceção.
- Cabeçalhos HTTP `Retry-After` com data passam a determinar a espera, respeitando os limites existentes. Falhas na limpeza de temporários de captura preservam o resultado tratado; as anotações de resultados da CLI, callbacks de escrita e argumentos opcionais de IA correspondem aos contratos usados em execução.

## v1.3.2 — Identificação de ensaios e limpeza de processos (2026-10-03)

- Campanhas sem referência explícita observam o commit do checkout que contém o código executado, registam a origem da referência e distinguem alterações locais/estado desconhecido. Instalações sem checkout mantêm `unknown`; referências do operador são preservadas. Relatórios existentes continuam legíveis.
- O líder do processo permanece por recolher até à limpeza do grupo, evitando sinalizar um identificador já libertado e mantendo a terminação dos descendentes após conclusão do líder. Limpeza não verificada e perda da identidade do filho comunicam estado incerto, incluindo saída truncada e erros de captura. Uma recusa de sinalizar apenas processos já terminados conserva a conclusão do helper elevado quando uma observação local limitada encontra a sessão sem membros ativos.
- O protocolo passa a identificar o coletor como integrado e explica o prazo de 120 segundos que inclui autenticação polkit e execução dos helpers de ficheiros, sem confundir atraso de autenticação com defeito do ficheiro.

## Correções de robustez, privacidade e CI (2026-10-03, pós-v1.3.1)

- Leituras textuais de informação do sistema com encoding explícito; /etc/os-release aceita BOM. O fallback sem psutil usa MemAvailable, conservando MemFree apenas quando o kernel não fornece aquele campo.
- Executores de pacotes, serviços, monitores, dispositivos, captura/OCR por binário e helpers de ficheiros usam saída limitada durante a leitura, stdin fechado, prazo e sessão própria. A pesquisa de executáveis usa shutil.which, sem lançar o comando which.
- A terminação do grupo de processos é tentada mesmo quando o processo principal já terminou. Se a elevação impedir a terminação, a espera local continua limitada e a mensagem exige verificar o alvo antes de repetir. Timeout não garante rollback; não é imposto limite de RAM ao processo externo.
- Comandos são registados como argumentos estruturados, com credenciais conhecidas ocultadas, incluindo cookies e autenticação HTTP. Logs antigos com atribuições ou flags de segredo sem aspas ocultam conservadoramente o resto da linha; palavras isoladas como password e token só recebem esse tratamento em linhas de comando identificadas. Diagnóstico e ensaios partilham a mesma implementação, preservando mensagens de erro e métricas de utilização.
- O job GTK instala as dependências completas num venv com acesso aos bindings do sistema e executa toda a suite com Xvfb. Skips inesperados falham o job, incluindo os causados por dependências de GUI ausentes.
- Regressões com dados sintéticos para memória, BOM, segredos com espaços, exportação, limites de saída, cleanup e autenticação simulada. A autenticação polkit e as alterações a recursos físicos continuam sujeitas ao protocolo de ensaios reais.


## Base de conhecimento offline por distribuição + multilingue reforçado (2026-10-01, pós-v1.1.0)

### `src/knowledge_base.py` (novo)

- Base de conhecimento LOCAL (offline, sem rede) por família de distribuição,
  construída a partir das wikis/manuais oficiais: Void Handbook, Debian Wiki,
  Ubuntu Server Documentation, Linux Mint, Arch Wiki, Manjaro Wiki, Fedora
  Docs, Red Hat Docs (RHEL/CentOS/Rocky/Alma), openSUSE Wiki, Alpine Wiki e
  Gentoo Wiki, mais um perfil genérico.
- Factos curados por família: gestor de pacotes e comandos, onde ficam os
  repositórios, como a rede é configurada (netplan vs interfaces vs
  NetworkManager vs dhcpcd...), como os logs são recolhidos (journald vs
  syslog vs socklog), hostname, locale, ferramenta de firewall (ufw /
  firewalld / nftables / iptables) com comandos de estado e exemplo, notas do
  gestor de serviços (runit/OpenRC/systemd) e bullets "o que torna esta
  distro diferente".
- Conteúdo em INGLÊS (fonte); apresentação localizada via i18n. Resolução por
  `ID` de `/etc/os-release` primeiro e `ID_LIKE` depois; sistema desconhecido
  sem ancestría reconhecível não inventa factos (perfil genérico só para
  ID vazio/unknown).

### Novos intents do assistente offline

- "onde estão os ficheiros de configuração?" — locais de configuração da
  distribuição (repositórios, rede, logs, hostname, locale, serviços).
- "where are the logs?" — como os logs funcionam NESTA distro + comandos
  (journalctl no systemd; dmesg no runit/OpenRC).
- "show repositories" / "onde ficam os repositórios?" — ficheiros de repos.
- "documentation about X" / "documentação sobre X" — documentação oficial da
  distro + link de pesquisa na wiki com a query.
- "firewall" — agora por distribuição (ufw no Ubuntu/Mint, firewalld no
  Fedora/openSUSE/RHEL, nada pré-instalado no Void/Arch/Alpine com
  iptables/nftables).
- "que distro sou?" — enriquecido com as notas da família + link oficial.
- Todas as respostas da KB terminam com "Reference (Wiki oficial): URL".

### Multilingue

- `SUPPORTED_LANGUAGES` (en, pt, es, fr, de) + `normalize_language()`: língua
  escolhida (config ou sistema) fora da lista -> INGLÊS automaticamente, com
  registo no log; redefinível a qualquer momento.
- Seletor de língua nas Definições > Aparência (nomes nativos: English,
  Português, Español, Français, Deutsch); grava em `app.language`.
- Chaves novas da KB em en/pt/es/fr/de (os restantes templates offline
  continuam en+pt com fallback por chave para inglês).

### Testes

- `tests/test_knowledge.py` (20 testes): consistência da KB (URLs https,
  documentação obrigatória), resolução ID/ID_LIKE (rocky->rhel,
  asahi+ID_LIKE arch->arch, nixos->None), respostas por distro (netplan no
  Ubuntu, /etc/sv no Void, journald no Arch, /var/log/messages no Alpine,
  ufw/firewalld por família) e fallback de língua (it->en, jp->en,
  redefinição para fr).

## v1.1.0 (2026-10-01) — Auditoria completa: segurança, correções e refactoring

Auditoria integral do código (~9.400 linhas) com análise de 4 camadas (UI,
AI/rede, sistema/segurança, testes/packaging) + pesquisa de mercado. Relatório
completo em [docs/analise-2026-10-01.md](analise-2026-10-01.md). Suite de
testes mantida verde (103 pass; 21 falhas só em Windows por `os.geteuid` /
instaladores bash — inalteradas do baseline).

### Segurança (P0)

- **`man -P` permitia execução arbitrária de código** — a allowlist só
  validava o primeiro token do comando; `man -P <prog> <pág>` corre `<prog>`
  como pager via shell. `man` removido da allowlist por defeito
  (`config_manager.py`) e adicionada política de flags bloqueadas por comando
  (`_BLOCKED_FLAGS` em `system_utils.py`: `man -P/--pager`, `dig -f`,
  `traceroute -f`).
- **Sandbox de caminhos em TODOS os comandos da allowlist** — antes só
  `_FILE_ARG_COMMANDS` (cat/head/...) validava argumentos com caminhos
  absolutos; `grep . /var/log/auth.log` lia ficheiros fora de
  `allowed_edit_dirs`. Agora qualquer argumento `/...`/`~...` é validado em
  todos os comandos.
- **`allowed_edit_dirs` por defeito vazio** (mínimo privilégio) — novas
  instalações não têm diretórios de sistema editáveis até opt-in; configs
  existentes mantêm a lista.
- **Plugins opt-in** — antes, QUALQUER `.py` em `plugins/` era importado e
  executado no arranque (incluindo o exemplo embarcado). Agora só os
  listados em `plugins.enabled` (nova secção da config, com deep-merge) são
  carregados; aviso se o diretório tiver permissão de escrita para
  grupo/outros.
- **`.env` e diretório de config protegidos nos instaladores** —
  `chmod 700 ~/.config/linux_ai_assistant` + `chmod 600 .env` (as chaves
  API ficavam 0644 num `$HOME` 0755).
- **Log de erros sem `exc_info`** nos caminhos que podiam fugar URLs com
  segredos (traceback do requests inclui a URL original); User-Agent usa a
  versão da app.

### Correções críticas (P0)

- **Renderização streaming sempre errada** — o cálculo de `body_start` com
  `_append_message(_('AI'), "")` ficava 2 caracteres atrás do primeiro
  chunk: o corpo inteiro ficava sem a tag `ai-message` e o realce de código
  deslocado. Novo `ChatView.append_stream_header()` insere só o cabeçalho;
  `close_streamed_message()` taggeia os code spans e fecha com `\n\n` (a
  mensagem seguinte colava-se ao fim da resposta).
- **CI não podia passar num runner limpo** — os testes importam
  `tenacity/requests/dotenv/psutil` mas o workflow não instalava dependências.
  Workflow reescrito: `pip install -r requirements.txt`, matrix Python
  3.8/3.10/3.12 (honra `requires-python>=3.8`), ruff falhável (antes pyflakes
  com `continue-on-error` nunca falhava) e shellcheck dos instaladores.

### AI / rede (P1)

- **Exceções tipadas em vez de erros como texto de chat** — `AIProviderError`
  (herda `RequestException` para preservar o retry de 5xx) e
  `ProviderNotConfigured`. `stream_chat` levanta em vez de `yield "Stream
  error: ..."`; a UI/CLI apanham por tipo. Corrige: erro a meio do stream
  nunca disparava o fallback offline; resposta legítima que começasse por
  "Error:" disparava o fallback. `_is_api_failure`/`_is_error_text`
  removidos.
- **Token usage nunca registado em streaming** — `_record_usage()` só corria
  nos métodos não-streaming; a GUI usa exclusivamente streaming, logo o
  `usage.json` quase não contava nada. Agora: `stream_options:
  {"include_usage": true}` nos providers OpenAI-style, captura de
  `usageMetadata` no Google, usage parcial (`message_start`/`message_delta`)
  no Anthropic, com estimativa de fallback.
- **SSE null-safe** — `"delta": null` (OpenRouter no chunk final) matava o
  stream com `AttributeError`; `.get("delta", {})` não protege contra null.
  Também `errors="replace"` nas linhas SSE (byte inválido não aborta).
- **Corpo do erro HTTP preservado** — o JSON do provider ("invalid model",
  "insufficient credits") era descartado; agora é anexado à exceção (e
  redactado).
- **Retry-After respeitado até 60 s** quando o header é válido (o cap antigo
  de 15 s queimava as 3 tentativas contra um limiar não reposto).
- **Parsing uniforme dos providers** — helpers partilhados
  (`_chat_openai_style`/`_stream_openai_style`) para openrouter/mistral/groq/
  local_llm (dedup ~350 linhas, mesma lógica de usage/erros); guards de
  `choices: []` e `content: null` em todos; Google junta multi-part (antes
  truncava em `parts[0]`) e reporta `promptFeedback.blockReason`.
- **Anthropic**: mensagens consecutivas do mesmo papel fundidas e histórico
  garantido a começar por `user` (a API responde 400 sem isto).
- **Cohere**: erro claro quando o histórico não termina em `user` (payload
  `message` vazio dava 400 críptico). Continua na API v1 (migração para v2
  ficou para depois — risco sem testes reais).
- **`provider_ready` sem falsos positivos** — exigido `base_url` para
  providers built-in (uma entrada apagada na config produzia
  `url = "None/chat/completions"` e a UI achava o provider pronto); sem
  chave → `ProviderNotConfigured` cedo.
- **Encriptação**: falha de desencriptação devolve `None` (nunca o
  ciphertext — antes enviava `Authorization: Bearer <ciphertext>`); chaves
  legadas em plaintext continuam a funcionar; `enable_encryption()` atómico
  sob lock.
- **`chat()`**: pós-processamento fora do `try` (uma exceção de estimativa
  não descarta resposta bem-sucedida); `_validate_messages` rejeita
  `content` não-string (rebentava DEPOIS do pedido bem-sucedido).
- **`reload()` da config sob lock**; `save()` regista `last_save_error`
  (falhas de disco deixam de ser engolidas em silêncio).

### Config (P1)

- **Deep-merge de defaults** — secções profundas em falta (provider novo
  numa versão futura, entrada apagada à mão) são repostas com defaults,
  preservando os valores do utilizador. Antes a validação era plana e nunca
  criava sub-entradas de `api.providers`.
- **Migrações versionadas** (`MIGRATIONS`) com `app.version` como marcador —
  a versão era escrita e nunca lida.

### UI (P1)

- **Respostas offline duplicadas em history.json** — `_add_ai_message` e
  `_finalize_response` gravavam o mesmo turno; persistência agora num único
  ponto (`_remember`), que também limita o histórico em memória a 1000
  (igual ao disco).
- **"Thinking..." fantasma** — o placeholder era o único callback sem guarda
  de `request_id`; cancelar e reenviar depressa deixava um placeholder
  permanente. Guarda aplicada (também nos avisos de sistema/erro de pedidos
  antigos, que podiam aparecer a meio de uma conversa nova).
- **Sair pela tray/SIGTERM não perde config** — `config.flush()` em
  `app.quit()` (o timer de debounce é daemon e morria com as alterações
  pendentes); `Gtk.main_quit()` em `finally` (uma exceção a meio de `quit()`
  deixava o processo preso sem janela nem tray).
- **Snapshot do pedido construído na main thread** — a worker deixou de ler
  `conversation_history`/`expert_mode`/config sem sincronização.
- **Streaming coalescido** — chunks acumulados e despejados à UI no máximo
  ~12×/s (antes: um idle por chunk + um por scroll; milhares por resposta
  rápida). Scroll com um único idle pendente (coalescing).
- **Label da tray sincronizado** — `toggle_visibility()` único na
  MainWindow usado por tray/botão flutuante (o `update_toggle_label` nunca
  era chamado e o menu dessincronizava).
- **Chunks de pedidos cancelados** já não são despejados como mensagens
  avulsas (guarda de cancelamento em `_update_ai_message`).

### Dock / janela (P2)

- `apply_float` respeita `app.always_on_top` e volta a `stick()` (antes
  forçava keep_above+unstick, contradizendo o estado flutuante inicial).
- Dock X11 move/ancora a janela ao bordo com o tamanho REAL (o strut só
  reserva; a janela ficava onde estava).
- Layer-shell confirmado com `GtkLayerShell.is_layer_window()` e tentado
  pré-realize em Wayland (a API exige init antes de realize; o método
  reportava sucesso mesmo sem efeito).
- `size-allocate` deixou de duplicar a escrita de width/height
  (`configure-event` já cobre tudo).

### Sistema / ficheiros (P1)

- **Captura Wayland reparada** — `grim -o <ficheiro>` usava a flag errada
  (`-o` é o monitor, não a saída): agora `grim <ficheiro>`.
  `capture_active_window()` em Wayland usava `slurp`, que NÃO produz
  imagens (é seletor de geometria): agora obtém a geometria da janela
  focada via `swaymsg` e captura com `grim -g`.
- **Escrita de ficheiros** (`file_actions`) — mode/owner do original
  preservados (antes tudo ficava 0600: scripts perdiam `+x`); caminho
  privilegiado atómico (`cp → chown/chmod --reference → mv`) em vez de
  `pkexec cp` que truncava o destino e ficava root:root 0600 (editar
  `/etc/hosts` partia a resolução de nomes); **backup `.bak` com timestamp**
  antes de qualquer edição; **revalidação por hash SHA-256** entre o diff
  mostrado e a escrita (fecha o TOCTOU); diff com nota explícita quando
  truncado a 1 MB (o utilizador autorizava um diff enganador); `$HOME`
  resolvido com realpath em `is_privileged_path`.
- **CLI** — exit codes reais (0/1) por comando (antes TODAS as falhas saíam
  com 0); `system exec` aceita argv em lista (argumentos com espaços
  partiam-se no re-split do shlex); `expert` tem fallback offline como o
  `chat`; `_save_history` funde com o ficheiro em disco (o last-writer-wins
  apagava entradas escritas pela GUI).
- **Comandos** — output limitado a ~1 MB com marcador (o contexto da IA não
  enche de `grep -r /`), `errors="replace"` no decode, pytesseract com
  `timeout=30`, temporários de captura limpos em falha, `search_files` com
  teto de 30 s, `which` com timeout via `shutil.which`.

### Testes / i18n (P1)

- **i18n completo** — `"Hide Window"` e `"Path not allowed: {path}"` estão
  fora do catálogo (toggle da tray sempre em inglês); mensagens de captura
  de ecrã/erros hardcoded traduzidas; `"API Key:"` adicionado a es/fr/de.
  Paridade pt/es/fr/de verificada.
- **`conftest.py`** único de sys.path; `test_installation.py` já funciona
  com pytest directo; `unittest.main()` movido para o fim de
  test_regressions.py (`python tests/...` saltava 2 classes em silêncio);
  teste de redação atualizado (linhas com `redact_url` são a mitigação, não
  violação).

### Packaging (P1)

- **Flatpak** — `exclude` → `skip` (a primeira não é válida para fontes
  `dir`); chaves de topo fora do schema removidas; `.desktop` instalado em
  `/app/share/applications` (o flatpak instalava-se sem entrada no menu).
  NOTA: a sandbox continua sem `scrot/grim/tesseract/pkexec` — captura, OCR
  e comandos privilegiados exigem resolução futura (portais) ou instalação
  nativa.
- **setup.py/pyproject** — `install_requires` duplicado removido (fonte
  única no pyproject; o template XBPS constrói via PEP 517).
- **Versão única** — `src/_version.py` (1.1.0) lida dinamicamente pelo
  pyproject; `src/__init__`, config e User-Agent alinhados. Antes espalhada
  por 5+ sítios.
- **`import src` já não cria `app.log`** — file logging adiado para
  `setup_file_logging()` (chamado em app.main/cli.main), ficheiro 0600.

### Refactorings estruturais

- **`src/chat_view.py` (ChatView)** — buffer, tags, offsets, placeholder e
  scroll isolados num widget próprio; invariante único "quem insere texto é
  quem calcula offsets". O `main_window.py` mantém só as decisões.
- **`src/history_store.py` (HistoryStore)** — fila FIFO + writer thread de
  history.json GTK-free e testável; API única `append(role, content)`.
- **Helpers OpenAI-style no `ai_client`** — dedup dos 4 providers que falavam
  o mesmo contrato; parsing/usage/erros uniformes.
- `main_window.py` ficou ~350 linhas mais curto e sem lógica de buffer.

### Não implementado nesta ronda (fase seguinte)

- **ChatController** (máquina de estados de pedidos num objeto próprio) e
  **extração dos diálogos** para módulo próprio — a estrutura de guards
  actual está correcta e testada; são movimentações puras, baixo risco,
  feitas melhor com a suite a correr em Linux.
- Cancelamento propagação até ao client (o `session.post` inicial e o sleep
  do 429 não são interrompíveis; a assinatura dos `_stream_*` é fixada por
  teste).
- Migração Cohere para API v2/OpenAI-compat.
- Flatpak: tesseract/scrot/portais dentro da sandbox.
- Funcionalidades de produto da análise de mercado (MCP, RAG local, visão
  multimodal, atalho global de texto selecionado, pesquisa/export de
  histórico, servidor API local) — roadmap em
  [docs/analise-2026-10-01.md](analise-2026-10-01.md).
