# Roadmap de ações conversacionais

Data: 2 de outubro de 2026.

## Objetivo e primeira entrega

O utilizador deve conseguir pedir uma tarefa, receber as opções que o sistema realmente suporta e continuar a mesma tarefa na conversa: por exemplo, consultar os modos de um monitor, escolher uma resolução apresentada e aplicá-la; ou procurar uma aplicação nos repositórios configurados, selecionar o pacote e instalá-lo.

A primeira entrega estabelece esse fluxo em duas áreas, com utilização na interface GTK e na CLI:

| Área | Âmbito inicial | Limite |
| --- | --- | --- |
| Software | Pesquisa de pacotes em APT e XBPS, apresentação de candidatos, escolha e instalação do pacote selecionado, verificação do resultado. | Usa os repositórios já configurados. Não configura automaticamente qualquer aplicação, não acrescenta repositórios e não executa instaladores encontrados na Web. |
| Monitores | Consulta das saídas e modos anunciados pelo sistema, seleção e alteração da resolução/frequência dos monitores ativos em X11 e Sway. | Saídas ligadas mas desativadas podem aparecer na consulta, mas a sua ativação é recusada. GNOME, KDE, escala, disposição e perfis permanentes têm etapas próprias abaixo. |
| Continuação | Opções e tarefa associadas à conversa que originou o pedido, persistidas durante 15 minutos; respostas posteriores resolvidas contra essas opções. | Importação/exportação omite a tarefa pendente. Texto do histórico e respostas de IA não são autorizações de execução. |
| Recuperação de imagem | Alteração temporária, verificação e janela de 15 segundos para confirmar a nova configuração; reposição quando não for confirmada. | A reposição precisa do backend gráfico ainda disponível. Falha de reposição deve aparecer como falha, sem afirmar que o ecrã recuperou. |

O estado exato da implementação, testes executados e limitações observadas deve acompanhar a entrega. Esta lista define o seu âmbito; não equivale a validação física de todos os monitores, distribuições ou gestores de pacotes.

Os fluxos locais não precisam de uma API de IA. A pesquisa e a instalação dependem dos índices e pacotes disponíveis nos repositórios ou na cache local. Sem resultados ou sem ligação, o programa deve explicar a limitação em vez de inventar um pacote ou uma configuração.

O módulo `src/package_actions.py` utiliza os índices APT/XBPS existentes, sem os atualizar silenciosamente. Apresenta até 12 candidatos com nome, versão, origem e descrição, com orçamento de pesquisa de 40 segundos e limite de saída de 1 MiB por comando. A pesquisa recebe texto literal, não padrões livres. Antes de instalar, volta a consultar a seleção; uma mudança de versão ou origem exige nova pesquisa. A instalação pede a versão exata através de `pkexec`, usa `--no-remove` no APT e verifica o estado instalado e a versão resultante. Os restantes gestores devolvem uma limitação explícita para este fluxo.

`src/display_actions.py` utiliza `xrandr` em X11 e a interface nativa de Sway em Wayland. Não tenta alterar uma sessão Wayland através de XWayland. Só altera saídas ativas, preservando posição, escala e rotação/transformação; em X11 também preserva reflexão, panning e saída principal, e em Sway o estado de alimentação. A alteração é recusada se não for possível preparar um estado anterior completo. A reposição tem um temporizador de 15 segundos independente de GTK, no processo da aplicação, podendo ser antecipada ao fechar ou substituir a tarefa. Não sobrevive a um encerramento forçado como `SIGKILL`; um supervisor externo e recuperação persistente ficam para a etapa 2. Listas longas apresentam até 30 opções; um pedido por resolução/frequência filtra os modos disponíveis.

O coordenador é `ConversationActions`, em `src/conversation_actions.py`. O catálogo inicial tem quatro identificadores: `packages.search`, `packages.install`, `display.list_modes` e `display.apply_mode`. O campo `task` de cada sessão em `HistoryStore` guarda as opções observadas e o tipo de pedido, com validação estrita em `src/task_state.py`; não guarda comandos nem planos executáveis. As opções expiram ao fim de 15 minutos e são reconsultadas antes de uma alteração. Retomar uma conversa permite continuar a escolha dentro desse prazo, sem executar a tarefa durante a abertura da conversa.

Na CLI, a execução requer um terminal interativo. Pipes e entrada por `--stdin` ou `--input` apresentam propostas em vez de instalar software ou mudar o monitor. `--no-history` não conserva as opções para uma mensagem posterior. Perguntas, hipóteses e recusas não autorizam alterações.

## Contrato de autorização e execução

Um pedido explícito como «instala o VLC» pode autorizar os passos necessários dessa tarefa: consultar os repositórios configurados, identificar o pacote, instalar e verificar. Se existirem candidatos ambíguos, o programa pede uma escolha. Uma consulta como «que pacotes existem?» apresenta informação e não autoriza instalação. Da mesma forma, listar resoluções não autoriza mudar o monitor; a escolha seguinte deve pedir essa alteração ou responder ao pedido de seleção que estiver ativo.

Cada tarefa tem um âmbito limitado: tipo de ação, alvo, parâmetros validados e conversa de origem. Mudar de alvo, acrescentar um repositório, remover pacotes, escrever configurações persistentes ou executar outra tarefa exige um novo pedido adequado a essa mudança. A autenticação de administrador continua a ser feita pelos mecanismos do sistema e pode continuar a pedir a password.

A execução pertence a módulos locais com comandos e parâmetros construídos pelo código. A IA pode ajudar a interpretar o pedido e explicar resultados, mas não fornece comandos de shell executáveis, listas livres de passos ou nomes de executáveis para executar diretamente. Texto de resultados, descrições de pacotes, ficheiros importados e respostas antigas são dados, mesmo quando contêm instruções.

## Etapas e critérios de aceitação

### Etapa 0 — Software e monitores conversacionais

Prioridade: entrega inicial. Dependências: deteção da distribuição/ambiente gráfico, sessões existentes, GTK/CLI e execução local com argumentos separados.

Tarefas:

- Isolar pesquisa/instalação de pacotes e consulta/alteração de monitores em módulos sem dependência de GTK.
- Associar as opções à conversa e interpretar uma escolha por número ou pelo valor efetivamente apresentado.
- Reconsultar o alvo antes de alterar o sistema: pacote, saída gráfica e modo ainda disponíveis.
- Mostrar conclusão apenas depois da verificação. Preservar erro, cancelamento e resultado de recuperação como estados distintos.
- Bloquear tarefas incompatíveis em simultâneo e impedir que uma resposta destinada a outra conversa execute a ação pendente.

Aceitação:

- Pedido, consulta, seleção e execução têm o mesmo resultado na GTK e na CLI, com e sem fornecedor de IA.
- Negação, hipótese, pergunta informativa, opção inválida e mensagem sem contexto não causam uma alteração.
- Um pedido de instalação identifica o pacote nos repositórios e a verificação confirma o estado instalado; uma falha devolve erro também na CLI.
- Selecionar um modo não anunciado, uma saída entretanto desligada ou um backend não suportado é recusado com explicação.
- A mudança de monitor só fica aceite após confirmação dentro da janela de 15 segundos; cancelamento ou expiração inicia reposição e apresenta o resultado.
- A bateria de testes cobre argumentos malformados, saída truncada, ferramentas ausentes, timeouts, falha de instalação, isolamento entre conversas e recuperação. Ensaios físicos são registados separadamente dos testes com respostas simuladas.

### Etapa 1 — Contrato comum para novos módulos

Prioridade: alta, antes de multiplicar funcionalidades. Dependência: estabilização dos dois fluxos iniciais.

Extrair um contrato pequeno a partir dos módulos já utilizados. Cada capacidade declara identidade, parâmetros, pré-condições, necessidades de privilégios, consulta, execução, verificação e recuperação quando exista. O coordenador gere estados como consulta, espera de escolha, execução, espera de confirmação, concluído, falhado e cancelado. Uma ação não passa a estar autorizada apenas porque um modelo lhe atribuiu um tipo.

Reutilizar a deteção de distribuição de `offline_assistant.py`, as sessões de `history_store.py` e os controlos de permissões existentes. Evitar duas implementações do mesmo pedido, uma no assistente offline e outra no fluxo com IA. A rotina que interpreta o pedido deve decidir qual módulo o trata antes de iniciar efeitos.

Aceitação:

- Uma terceira capacidade pode ser acrescentada sem alterar o ciclo principal de execução de GTK e CLI.
- Todos os módulos devolvem resultados estruturados com estado, observações limitadas e verificação; nenhuma interface interpreta uma mensagem de texto como prova de sucesso.
- Há testes de contrato reutilizáveis para todos os módulos: validação, permissões, erro, cancelamento, seleção e isolamento.
- Importar/restaurar uma conversa nunca retoma uma alteração automaticamente. A persistência de tarefas já existente mantém o formato de metadados validados, a reconsulta de alvos e a expiração. Novos módulos usam o mesmo contrato, sem introduzir comandos ou autorizações importadas no estado.

### Etapa 2 — Registo de ações e recuperação apropriada

Prioridade: alta para mudanças persistentes. Dependência: contrato comum da etapa 1.

Acrescentar um registo de ações com tarefa, conversa, alvo, datas, parâmetros sem segredos, estado anterior, resultado e método de verificação. Integrar escritas de configuração com `file_actions.py` e `ChangeJournal`, que já fazem pré-visualização, backups e recuperação por hashes. O registo existente cobre ficheiros; não deve passar a afirmar que recupera todos os efeitos de um comando ou de uma instalação.

Cada módulo define uma compensação específica. Para um monitor, restaurar a configuração anterior; para um ficheiro, recuperar a cópia verificada. Instalar software não implica que desinstalar a aplicação reverta dependências, scripts e dados criados: a interface deve explicar essa diferença e exigir um pedido próprio para remoção.

Aceitação:

- É possível consultar o que foi alterado e a verificação que confirmou o resultado.
- Passwords, tokens e saídas sensíveis não são persistidos no registo.
- Falhas de backup ou de preparação impedem as alterações que dependem desses backups.
- Recuperação revalida o estado atual e não substitui silenciosamente mudanças feitas entretanto por outro programa ou pelo utilizador.
- Uma interrupção do programa fica identificada como resultado desconhecido ou pendente de revisão; o próximo arranque não repete a ação automaticamente.

### Etapa 3 — Cobertura de ambientes e instalação

Prioridade: alta para GNOME/KDE e para as distribuições usadas; média para os restantes gestores. Dependências: etapas 1 e 2.

Monitores:

- Acrescentar backends próprios para GNOME e KDE, com deteção de capacidades e verificação no ambiente correto.
- Acrescentar escala, posição, rotação, ativação de saídas e configuração de vários monitores. Preservar uma saída utilizável e aplicar a mesma confirmação temporária.
- Só depois acrescentar perfis permanentes, por exemplo «portátil», «secretária» e «apresentação», usando a configuração nativa de cada ambiente e backups quando houver ficheiros.

Software:

- Acrescentar DNF, Pacman, Zypper e APK por ordem das distribuições efetivamente pedidas, com pesquisa, resolução de nomes, privilégios e verificação próprios.
- Acrescentar Flatpak como origem explícita. Quando existirem versões nativa e Flatpak, apresentar a diferença e pedir a escolha da origem.
- Melhorar informação sobre tamanho, dependências, versões e alterações previstas quando o gestor puder fornecê-la. Distinguir índice desatualizado, bloqueio do gestor e pacote ausente.
- Tratar adição de repositórios, execução de scripts descarregados e upgrades globais como capacidades separadas, sem os incluir implicitamente numa instalação simples.

Aceitação: cada backend tem exemplos reais e testes com saídas da ferramenta; instalação e mudança gráfica são ensaiadas numa máquina ou sessão compatível antes de se declarar esse ambiente suportado. A ausência de suporte mantém o estado atual e fornece instruções úteis.

### Etapa 4 — Configurações de aplicações e funções do sistema

Prioridade: média; escolher uma capacidade de cada vez com base no uso. Dependências: etapas 1 e 2; etapa 3 quando a capacidade depender de um backend novo.

| Capacidade | Primeiro incremento útil | Verificação / recuperação |
| --- | --- | --- |
| Aplicações | Perfis para aplicações concretas: criar configuração inicial, escolher opções conhecidas e identificar necessidade de reiniciar. | Validar formato e versão; preview e backup dos ficheiros; testar carregamento quando a aplicação disponibilizar essa operação. |
| Serviços | Consultar estado, ativar/desativar e iniciar/parar um serviço identificado, com adaptação systemd/runit. | Reconsultar estado; guardar estado anterior; distinguir habilitado de ativo e respeitar dependências. |
| Rede | Evoluir o fluxo Wi-Fi existente para perfis, DNS e ligações conhecidas. | Verificar ligação/rota; prever recuperação para não deixar o utilizador sem acesso, especialmente numa sessão remota. |
| Impressoras e scanners | Evoluir os fluxos existentes para escolher predefinições, testar fila e diagnosticar dispositivos. | Reconsultar fila/dispositivo; não imprimir nem digitalizar só para verificar sem um pedido que inclua essa operação. |
| Áudio | Listar entradas/saídas, escolher dispositivo predefinido e ajustar volume. | Reconsultar dispositivo/nível; guardar a seleção anterior e limitar valores. |
| Energia | Consultar e selecionar perfis suportados pelo serviço instalado. | Verificar perfil; guardar anterior; não alterar suspensão ou desligar automaticamente como parte da escolha de perfil. |
| Armazenamento | Consultar e diagnosticar primeiro; montagem e alterações ficam em capacidades separadas. | Identificar inequivocamente o dispositivo; operações destrutivas exigem pedidos próprios e não pertencem ao fluxo de configuração genérico. |

«Configurar a aplicação ABC» só é executável se existir um perfil compatível ou se o utilizador fornecer opções que um módulo reconheça. Para aplicações sem perfil, o programa pode explicar passos e indicar documentação; não deve inventar uma configuração e aplicá-la como se tivesse sido validada.

### Etapa 5 — Interpretação por IA e extensões

Prioridade: posterior. Dependências: contrato comum estável e resultados das etapas anteriores.

Permitir que um fornecedor de IA sugira apenas intenções e parâmetros de um esquema conhecido. O coordenador valida esses dados contra o catálogo, o estado local, as permissões e o pedido humano que deu origem à tarefa. Uma resposta de IA inválida deve cair numa explicação ou pergunta de esclarecimento, mantendo os fluxos offline utilizáveis.

Se houver módulos de terceiros, as capacidades de execução precisam de adesão explícita e limites próprios; o mecanismo de fornecedores de IA existente não concede automaticamente permissão para instalar extensões que executam ações do sistema.

Aceitação: o mesmo conjunto de testes de autorização passa com pedidos determinísticos e interpretações de IA; instruções incluídas em resultados de pesquisa ou descrições não se tornam ações; um módulo desconhecido não ganha acesso ao executor.

## Ordem recomendada e decisão de avanço

1. Entregar e ensaiar software APT/XBPS e monitores X11/Sway, incluindo continuação e reposição temporária.
2. Consolidar o contrato comum e acrescentar registo de ações, usando a recuperação de ficheiros já disponível.
3. Priorizar GNOME/KDE e os gestores das distribuições efetivamente utilizadas.
4. Acrescentar perfis de aplicações e depois serviços, áudio, energia e extensões de rede/dispositivos, uma capacidade verificada de cada vez.
5. Só depois ampliar a interpretação por IA e a instalação de módulos de terceiros.

Não há prazo implícito neste roadmap. O avanço depende dos critérios de aceitação, do ambiente disponível para testes e das necessidades do utilizador. Uma etapa pode ser dividida por backend ou capacidade sem anunciar suporte às partes ainda não ensaiadas.
