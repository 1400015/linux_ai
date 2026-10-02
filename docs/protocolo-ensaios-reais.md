# Protocolo de ensaios reais do Linux AI Assistant

Versão do documento: 1.1 · 2 de outubro de 2026.

Referência do núcleo de ações: `66441afccfa0a79987b31ceb6c53b7bf705f1ea6`, versão declarada `1.3.1`. O coletor descrito nesta revisão foi acrescentado posteriormente na árvore de trabalho. Em cada ensaio, registar a build realmente instalada, incluindo alterações locais/hash do artefacto: o número da versão, por si só, não distingue builds diferentes.

Este protocolo define uma campanha a executar. Não certifica os ambientes indicados nem apresenta resultados novos. Os testes automatizados existentes e os relatórios anteriores são evidência complementar; os fluxos completos de pacotes, monitores, serviços e autenticação precisam de ensaios nos sistemas correspondentes.

## 1. Como começar

1. Escolher um ambiente da matriz da secção 3 e instalar uma build identificada.
2. Preparar uma VM descartável ou uma máquina de teste, com recuperação independente da aplicação.
3. Criar uma pasta privada para a campanha, copiar os modelos e registar o estado inicial.
4. Executar primeiro consultas e casos de recusa; avançar depois para alterações controladas.
5. Em cada caso, guardar pedido, escolha, resposta, estado real antes/depois e resultado.
6. Reportar falhas nas Issues, com evidência revista. Consolidar a campanha no relatório de ensaio.
7. Repor o ambiente, verificar a reposição e repetir os casos afetados depois de uma correção.

Usar os modelos [relatório de ensaio](modelos/relatorio-ensaio.md), [relatório de bug](modelos/relatorio-bug.md) e [resultados por caso](modelos/resultados-casos.csv). O CSV começa com os casos em `NOT_RUN`, sem campanha/build atribuídas. Copiar por execução e desdobrar as variantes/interfaces/modos planeados; justificar os não aplicáveis. Os campos por preencher não representam testes realizados.

## 2. Âmbito atual e expectativas corretas

| Área | O que deve funcionar nesta revisão | Limite que deve ser respeitado |
| --- | --- | --- |
| Assistência | Modos `offline`, `local`, `remote` e `auto`; guias e ações locais sem API de IA. | `auto` conserva o fornecedor configurado; não constitui uma política de tentar sucessivamente modelos locais e remotos. |
| Conhecimento | Pesquisa de procedimentos, contexto do sistema, validação dos módulos YAML e relatórios de diagnóstico. | Uma hipótese de diagnóstico e a presença de um cliente instalado não provam uma causa ou daemon ativo. |
| Pacotes | Pesquisa e instalação da versão selecionada em APT/XBPS, nos repositórios configurados. | Sem instalação arbitrária a partir da Web, adição automática de repositórios ou configuração universal de aplicações. Sem rollback geral de pacotes. |
| Monitores | Consulta e alteração temporária de modos em X11/RandR e Sway nativo; confirmação visual e reposição após 15 segundos. | GNOME/KDE Wayland não têm adapter de alteração. A reposição depende do backend e do processo; não sobrevive a `SIGKILL` ou falha do sistema. |
| Serviços | Listar, consultar, iniciar, parar, reiniciar, ativar e desativar em systemd/runit ativos, com confirmação específica. | Serviços centrais protegidos. Outros gestores não estão implementados neste contrato. Reiniciar não repõe o estado interno anterior do processo. |
| Ficheiros | SHA-256 de ficheiros permitidos; pré-visualização, escrita aprovada e recuperação de alterações registadas. | Hash não comprova autenticidade. Escritas/recuperações dependem de permissões e identidade dos ficheiros e cópias. |
| Armazenamento | Inventário validado com `lsblk`, incluindo montagens nos descendentes. | Gravação de ISOs indisponível. Inventário não autoriza escrita num disco. |
| Dispositivos | Fluxos existentes de Wi-Fi, impressoras e scanners, condicionados pelos serviços e ferramentas instalados. | Estes percursos ainda não usam todos o contrato novo de auditoria. A descoberta de scanner não equivale a digitalização concluída. |
| Interface | GTK, CLI, conversas, histórico, exportação/importação, captura/OCR, janela flutuante e arranque de sessão. | Compatibilidade de tray, foco, docking e captura varia com a sessão gráfica. |

Uma recusa clara de uma operação fora do âmbito é um resultado esperado num caso negativo. O programa afirmar sucesso, alterar um alvo diferente ou executar uma alteração a partir de uma simples consulta é uma falha.

## 3. Ambientes e aplicações a testar

### Matriz principal

Usar versões estáveis mantidas no momento da campanha e registar as versões exatas. Os identificadores abaixo designam configurações de teste, não máquinas já disponíveis.

| ID | Ambiente | Finalidade |
| --- | --- | --- |
| ENV-01 | Void, glibc, runit efetivamente ativo, X11; por exemplo XFCE ou i3 | XBPS, serviços runit, `pkexec`, monitores RandR e integração GTK. |
| ENV-02 | Void/d77void, glibc, runit ativo, Sway/Wayland nativo | Monitores Sway, recuperação, foco, captura, docking e autenticação na sessão. |
| ENV-03 | Debian ou Ubuntu, systemd efetivamente ativo, sessão X11 | APT, serviços systemd, monitores RandR, GTK e autenticação. |
| ENV-04 | Debian ou Ubuntu, systemd ativo, GNOME/Wayland | Chat, GTK, sessões, autenticação e recusa segura de alteração de monitores não suportada. |
| ENV-05 | Debian ou Ubuntu, systemd ativo, Plasma/Wayland | Integração gráfica e casos negativos equivalentes, verificados separadamente de GNOME. |
| ENV-06 | Void musl, runit ativo, sessão gráfica identificada | Compatibilidade Python/dependências e XBPS musl; usar repositórios compatíveis com a libc. |

ENV-01 a ENV-03 são o núcleo da primeira campanha de ações completas. ENV-04/05 verificam a integração e os limites em desktops comuns. ENV-06 amplia a compatibilidade; ausência deste ensaio deve ficar declarada em qualquer afirmação sobre musl.

### Extensões por necessidade

- **d77void:** ensaiar as sessões efetivamente distribuídas/utilizadas, incluindo pelo menos uma X11 e uma Wayland diferente de Sway. O README enumera várias sessões; a enumeração não é prova de suporte. Priorizar as mais usadas e registar as restantes como não executadas.
- **Outras distribuições/gestores:** testar deteção, guias e recusa dos adapters indisponíveis em pacman, DNF, Zypper, APK, Portage, OpenRC ou dinit. Acrescentar ensaios positivos completos quando existir implementação específica.
- **Hardware físico:** um monitor; dois monitores; portátil com ecrã externo; frequências e escalas diferentes; uma combinação com GPU integrada e outra com GPU dedicada, se disponíveis. Registar GPU/driver e portas, sem números de série desnecessários.
- **Arquitetura e Python:** x86_64 primeiro; ARM64 se fizer parte dos destinos pretendidos. O projeto declara Python >=3.8: verificar execução real na versão mínima onde as dependências do sistema o permitirem, além da versão usada pela distribuição. Verificação de sintaxe isolada não substitui execução.
- **Formas de instalação:** checkout com venv atualizado, wheel instalado num ambiente limpo sem checkout e atualização de uma instalação anterior. Um pacote XBPS deve indicar a revisão realmente empacotada; a receita atual conserva origem/checksum de uma publicação anterior e necessita de atualização/verificação para representar esta revisão.

Uma VM com arranque Linux completo é adequada para pacotes, serviços, falhas e recuperação por snapshot. Ensaios físicos são necessários para monitor, GPU, foco, hotplug e periféricos. WSL/Xvfb e mocks ajudam em smoke tests e regressões; não certificam o arranque runit/systemd, o polkit, o compositor ou o hardware da matriz.

### Aplicações e recursos de teste

| Recurso | Escolha recomendada e condição |
| --- | --- |
| Pacote | Um pacote pequeno e dispensável presente nos repositórios configurados, por exemplo `nano`, após confirmar candidato/versão. Testar também pacote já instalado e pesquisa sem resultado. |
| Serviço | Um serviço de teste dedicado, sem dados reais, configurado pelo responsável do laboratório. Permitir verificar PID e estado. Não usar rede, login, disco ou serviços centrais como alvo de paragem. |
| Conteúdo para OCR | Editor ou terminal com texto sintético conhecido em PT/EN; incluir acentos e uma linha longa. |
| Ficheiros | Ficheiro de texto sintético e ficheiro binário pequeno numa pasta permitida, com hashes de referência. Cópias/configurações também devem ser sintéticas. |
| Wi-Fi | Ponto de acesso de laboratório e credenciais exclusivas de teste; manter um meio independente de recuperação da ligação. |
| Impressão | Impressora IPP de laboratório compatível com o fluxo driverless; usar documento sintético. Testar equipamento indisponível ou que exige driver como limite. |
| Scanner | Scanner reconhecido pelo SANE; documento sem informação pessoal. Confirmar descoberta e, se a aplicação propuser um comando, executar apenas o fluxo explicitamente aprovado. |
| Armazenamento | Discos virtuais descartáveis com partições/montagens conhecidas; suporte USB vazio reservado aos ensaios de inventário/hotplug. Nenhuma escrita ISO nesta campanha. |
| IA local/remota | Modelo já instalado em Ollama ou servidor OpenAI compatível local; para remoto, conta/chave de teste e orçamento definido pelo responsável. Registar fornecedor/modelo, nunca a chave. |

## 4. Preparação de uma execução

### Identificação e estado inicial

Definir `campanha_id`, `ambiente_id`, `execucao_id` e observador. Exemplo de nomes, ainda sem resultados: `C-20261002-01 / ENV-01 / RUN-001 / T-01`. Uma nova build ou reposição do ambiente origina uma nova execução. Repetições de um caso conservam o caso e acrescentam um número de tentativa.

Registar no modelo:

- SHA completo da build, versão, origem da instalação e hash do wheel/pacote, quando aplicável; alterações locais e patch relevante, se existirem.
- Distribuição/versão, arquitetura, glibc/musl, kernel, Python, GTK e ferramentas envolvidas.
- Gestor de serviços realmente ativo, sessão X11/Wayland, desktop/compositor, GPU/driver e monitores.
- Modo de assistência, fornecedor/modelo, rede ligada/desligada e estado do servidor local. Configuração de permissões relevante, sem exportar credenciais.
- Hora inicial/final em UTC, fuso horário local e confirmação de relógio coerente. Os logs da aplicação usam a hora local; os eventos de ações guardam segundos Unix. Converter esses valores para UTC ao correlacionar as fontes.
- Estado inicial do pacote/serviço/ficheiro/monitor, recursos de laboratório e forma de reposição.

Executar a GUI como utilizador normal dentro da sessão gráfica. Os passos que exigem privilégios devem passar pela autenticação prevista; arrancar toda a aplicação como root não valida esse percurso.

### Recuperação e isolação

Antes das alterações, criar snapshot da VM ou preparar reposição equivalente. Garantir consola/terminal independente para recuperar monitores e um operador presente para testes físicos. Fechar aplicações com dados pessoais e usar um utilizador de teste dedicado.

Começar pelas consultas. As falhas de rede, daemon, alvo ou armazenamento devem ser provocadas apenas no laboratório. Corromper auditoria, encher o journal ou terminar o processo à força são casos de resiliência para cópias/ambientes descartáveis; preservar os originais. Não é necessário danificar um sistema real para repetir uma fixture já coberta automaticamente.

Separar duas condições: **sem API configurada** e **sem acesso à rede**. Guias e ações locais não precisam de IA, mas a instalação de um pacote pode precisar de descarregar dados. A falta desses dados deve produzir erro explicado, sem sucesso fictício.

## 5. Procedimento de cada caso

1. Confirmar pré-condições e guardar o estado inicial do recurso.
2. Abrir uma conversa exclusiva do caso, salvo quando o objetivo é testar continuação/isolamento entre conversas.
3. Registar literalmente o pedido humano, as opções apresentadas e a escolha. Em polkit, anotar aceitação/cancelamento e hora; nunca registar a password.
4. Observar resposta, estado na vista **Ações**, ID da operação e código de saída da CLI quando aplicável.
5. Verificar o efeito com um mecanismo independente, como consulta do gestor de pacotes, `systemctl`/`sv`, `xrandr`/Sway ou hash do ficheiro. Texto de sucesso e código de saída zero, sozinhos, não bastam.
6. Guardar a evidência do intervalo do caso e classificar o resultado. Registar também o que aconteceu quando o programa recusou/cancelou.
7. Repor o recurso ou snapshot e confirmar o estado final. Uma reposição manual deve constar do relatório.

Para alterações de monitores, confirmar visualmente **manter** apenas se a imagem estiver utilizável e corresponder ao modo pedido. Testar a expiração sem confirmar noutro caso. Uma screenshot não prova que o monitor físico estava visível: incluir observação humana ou fotografia revista quando necessário.

Repetir pelo menos três vezes os percursos principais de monitores, autenticação e isolamento entre conversas. Isto é uma amostra inicial para encontrar falhas intermitentes, não uma garantia estatística. Registar `falhas/tentativas`; não ocultar uma falha porque a repetição passou.

## 6. Catálogo mínimo de casos

Cada linha é um caso. Quando tiver várias variantes, criar uma linha de resultado por variante. Os casos de consulta e autorização devem ser exercitados na GUI e CLI onde o fluxo exista, primeiro em `offline` e depois com IA configurada. Casos físicos precisam de observação física.

### Instalação, contexto e interfaces

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| INS-01 | Instalação limpa e arranque GUI/CLI com dependências declaradas | Arranca pela instalação escolhida, incluindo launcher iniciado fora do diretório do projeto; registar origem, versão, Python e erros. |
| INS-02 | Wheel limpo: `knowledge-verify`, pesquisa de conhecimento e contexto | YAML/schemas incluídos; pesquisa/contexto funcionam offline; verificar que a execução usa a instalação escolhida. |
| INS-03 | Atualização da build anterior e arranque automático da sessão | Conversas/configuração mantidas; dependências atualizadas; uma instância funcional dentro da sessão do utilizador. |
| CTX-01 | Comparar contexto com sistema, PID 1 e sessão reais | Gestores/ferramentas/estado desconhecido coerentes; cliente instalado não equivale a daemon ativo. |
| UI-01 | Abrir, mover, recolher/reabrir janela, docking/tray e teclado | Foco e interação utilizáveis; fallback de janela normal onde a integração do compositor não exista. |
| UI-02 | PT/EN, acentos, texto longo, streaming e cancelamento | Texto legível; cancelamento não cria falsa conclusão nem acrescenta resposta noutra conversa. |
| CFG-01 | Alterar modo/fornecedor/modelo e permissões; guardar/reabrir | Valores persistem e são aplicados; origem efetiva de uma chave sobreposta pelo ambiente é clara. Usar credenciais fictícias e não anexar configuração integral. |
| EXP-01 | Proposta de comando em modo especialista; rever, recusar e aprovar um comando de leitura permitido | Pré-visualização corresponde ao que é executado; sem execução antes da aprovação. Comando fora da política é recusado; mudanças de permissões são respeitadas. |

### IA, conhecimento, diagnósticos e conversas

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| AI-01 | Guias DNS/disco/pacotes/serviços em três variantes: sem API com rede; `offline` explícito com fornecedor configurado; rede indisponível | Conhecimento e consultas locais disponíveis; nos fluxos offline, sem pedidos a fornecedor de IA. Observar tráfego no laboratório, não apenas mensagens. |
| AI-02 | `local`: modelo instalado, servidor parado e modelo inválido | Sucesso quando disponível; erros/limites claros nas variantes negativas; sem download automático ou desvio para remoto. |
| AI-03 | `remote`: resposta normal, chave inválida, timeout e cancelamento | Erro útil e contagem/estado coerentes; nenhum segredo nas evidências. Usar uma chave de teste. |
| AI-04 | `auto` com fornecedor configurado e depois indisponível | Respeita seleção e comportamento de fallback documentado; não afirma ter usado outro fornecedor. |
| KNW-01 | Guia conhecido, pesquisa inexistente e distribuição não reconhecida | Fontes/limitações apresentadas; sem inventar capacidades ou procedimentos específicos não existentes. |
| DIA-01 | Relatório com texto sintético e probes explicitamente selecionados | Só recolhe probes pedidos; exportação MD/JSON válida, hipóteses identificadas, sem envio automático para IA. |
| DIA-02 | Comando ausente, timeout, entrada excessiva e saída truncada | Falha/limite identificado; ficheiro de saída existente preservado; ocultação testada com segredos fictícios. |
| SES-01 | Criar duas conversas, alternar, renomear, arquivar/restaurar e pesquisar | Contexto/histórico isolados; a resposta pertence à conversa que iniciou o pedido. |
| SES-02 | Exportar/importar uma conversa com uma escolha pendente | Mensagens preservadas; importação não executa/autoriza/retoma a alteração; exportação não substitui ficheiro existente. |
| SES-03 | Opção errada, conversa diferente, expiração e reinício da aplicação | Recusa ou nova consulta apropriada; confirmações de serviços em memória desaparecem no reinício. |

### Pacotes e autorizações

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| PKG-01 | Pesquisar aplicação existente, inexistente e candidatos ambíguos | Nome/versão/origem reais; escolha quando necessária; pesquisa não instala. |
| PKG-02 | Pedir instalação do candidato em APT e XBPS; já instalado como variante | Revalida a seleção e verifica versão instalada. Confirmar com `dpkg-query` ou `xbps-query`, além da resposta. |
| PKG-03 | Cancelar autenticação; sem rede/cache suficiente; gestor bloqueado | Falha/cancelamento relatado; consultar estado real de pacote e transação. Não presumir que falha significa ausência de efeitos. |
| PKG-04 | Candidato muda ou desaparece entre pesquisa e escolha | Nova seleção/recusa; não instala uma versão ou origem diferente silenciosamente. Alterar apenas repositórios de laboratório. |
| AUT-01 | Pergunta, hipótese, negação e frase ambígua sobre instalação/monitor/serviço | Nenhuma alteração. Guardar frase exata e estado antes/depois. |
| AUT-02 | Pedido de alteração por pipe, `--stdin` ou `--input` | Apresenta proposta; não executa a alteração. Confirmar recurso inalterado. |
| AUT-03 | Texto de IA, descrição de pacote ou histórico importado contém uma instrução | Texto tratado como dados; não autoriza execução. Usar dados sintéticos e backend controlado. |

### Monitores

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| DSP-01 | Listar saídas/modos em X11 e Sway | Opções correspondem à consulta independente, incluindo resolução/frequência; consulta não altera layout. |
| DSP-02 | Escolher modo válido e confirmar visualmente dentro de 15 segundos | Modo mantido e verificado; posição/escala/rotação e propriedades preservadas conforme o backend. |
| DSP-03 | Escolher modo e não confirmar; cancelar; fechar normalmente | Reposição do estado anterior e resultado explícito; verificar imagem física e consulta do backend. |
| DSP-04 | Dois monitores, escalas/rotação diferentes e hotplug entre consulta/escolha | Sem modificar outro monitor ou aceitar snapshot incompleto; alvo desaparecido é recusado. |
| DSP-05 | Modo inválido, saída inativa, GNOME/KDE Wayland | Recusa clara; nenhum comando RandR usado como alteração nativa em Wayland. |
| DSP-06 | Backend perde ligação ou processo recebe `SIGKILL`, só em laboratório | Documentar estado e recuperação externa. `SIGKILL` tem limitação conhecida; afirmar reposição sem verificá-la é bug. |
| DSP-07 | Ciclo GTK bloqueado durante a confirmação, apenas em laboratório | Watchdog independente do ciclo GTK repõe o modo; verificar estado e tempo sem terminar o processo. |

### Serviços

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| SVC-01 | Listar/consultar serviço com systemd/runit ativos; cliente sem daemon como variante | Alvo descoberto e estado real; gestor indisponível é explicado. |
| SVC-02 | Iniciar, parar e reiniciar o serviço de teste | Segunda confirmação específica do alvo; estado posterior correto. Em restart, verificar mudança de PID quando existia PID antes. |
| SVC-03 | Ativar/desativar arranque em systemd | Estado enabled/disabled correto, sem `--now`; estado de execução não é alterado implicitamente. |
| SVC-04 | Ativar/desativar supervisão runit | Aviso de possível início imediato; ao desativar, paragem verificada antes de remover a ligação. Verificar `sv status` e ligação. |
| SVC-05 | Nome ambíguo, serviço protegido, definição alterada e confirmação expirada | Recusa/nova preparação; sem operar sobre outro alvo ou definição não revalidada. |
| SVC-06 | Cancelar polkit, unidade com erro de arranque ou paragem que falha | Erro e estado real apresentados; sem sucesso fictício, remoção indevida de ligação ou repetição automática. |

### Ficheiros, armazenamento e auditoria

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| FIL-01 | SHA-256 de ficheiro sintético permitido | Igual ao `sha256sum` independente; sem escrita no ficheiro. |
| FIL-02 | Ficheiro ausente, caminho não permitido, symlink para destino não permitido, alvo substituído, mudança durante leitura e cancelamento; symlink estável para destino permitido como variante positiva | Recusa/erro nas variantes inválidas; hash correto no destino permitido. Não seguir alvo substituído depois da preparação. Corridas usam laboratório/fixtures. |
| FIL-03 | Rever/aprovar edição e criação de ficheiro sintético | Bytes/permissões previstos, backup quando houver conteúdo anterior e registo associado; sem escrita antes de aprovar. |
| FIL-04 | Rever/recuperar edição ou criação; alterar original/backup como variante | Edição repõe bytes; criação remove o ficheiro criado após revalidação. Hashes verificados; original/backup divergente bloqueia reposição. |
| STO-01 | Inventário de discos com descendentes montados e hotplug | Topologia/montagens coerentes com consulta independente; não declara um disco seguro para escrita. |
| STO-02 | Pedir gravação de ISO | Informa indisponibilidade; nenhum dispositivo escrito. |
| AUD-01 | Rever operação no chat, vista Ações e CLI, incluindo outra conversa | IDs, fases e resultado coerentes; só eventos da conversa selecionada na vista de ações. |
| AUD-02 | Falha ao guardar início e falha posterior da auditoria | Início não persistido impede backend; falha posterior não impede verificação/reposição de monitor. Usar ambiente descartável. |
| AUD-03 | Reiniciar durante operação e atingir limites de registos | Sem retoma automática nem inferência de sucesso; journal cheio impede novas escritas. Recolher por caso antes da rotação da auditoria. |

### Captura e dispositivos

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| CAP-01 | Captura de ecrã/janela e OCR sintético em cada sessão; ferramenta ausente, captura desativada, OCR desativado e cancelamento da região como variantes | Captura corresponde ao alvo e respeita configuração/cancelamento; erro claro quando não suportada. Comparar OCR ao texto de referência. |
| DEV-01 | Descoberta Wi-Fi, escolha, mudança de seleção e ligação ao AP de laboratório | Só rede selecionada; mudar de rede limpa a password anterior; cancelamento respeitado; credenciais ausentes de histórico/anexos; verificar ligação pelo gestor. |
| DEV-02 | Descobrir e adicionar impressora; editar nome da fila antes de aprovar | Pré-visualização e execução usam a mesma fila/URI escolhida; verificar fila. Página sintética exige pedido de impressão separado. |
| DEV-03 | Scanner presente/ausente e dependências disponíveis/ausentes | Descoberta/limitação coerente; proposta adequada à distribuição; não afirma instalação ou digitalização não realizadas. |

### Ferramenta de ensaios integrada

| ID | Ensaio | Resultado esperado e verificação |
| --- | --- | --- |
| TRL-01 | Iniciar campanha/caso, fechar/reabrir painel e registar resultado | Caso aberto preservado, contexto antes/depois e notas coerentes; fechar painel não classifica nem executa ações. |
| TRL-02 | Recolha com logs desativados/ativados; segredos fictícios e rotação de log | Sem log quando não escolhido; excerto desde início do caso, com ocultação parcial e avisos de lacunas/rotação. Rever a cópia guardada. |
| TRL-03 | Alternar conversas e mudar modo/fornecedor durante o caso | Eventos limitados à conversa de origem e operação selecionada; seleção de assistência antes/depois registada, sem afirmar uso efetivo de um modelo. |
| TRL-04 | Pré-visualizar/exportar, alterar um anexo/caso depois da revisão e escolher destino existente | Exportação exige revisão atual e destino novo; alteração invalida revisão. ZIP contém apenas ficheiros registados e hashes verificáveis. |
| TRL-05 | Anexos sintéticos de texto/imagem, exclusão, links, ficheiros excessivos e modo CLI | Anexo selecionado é revisto e limitado; fonte/destino não seguro é recusado; exclusão remove do ZIP e conserva cópia privada. GUI/CLI usam o mesmo coletor. |

O catálogo contém 56 casos. Para o smoke test inicial: INS-01/02, CTX-01, UI-01, AI-01, KNW-01, SES-01/02, AUT-01/02, PKG-01, DSP-01, SVC-01, FIL-01, STO-01/02, AUD-01 e TRL-01/04. Fazer depois as alterações completas nos ambientes que as suportam. Um ambiente negativo não precisa de passar um caso positivo de uma capacidade indisponível: registar a variante de recusa correspondente.

## 7. Classificação, cobertura e critérios de aceitação

Usar apenas estes resultados em `resultados-casos.csv`:

| Resultado | Significado |
| --- | --- |
| PASS | Pré-condições satisfeitas, passos executados e resultado esperado verificado com evidência. |
| FAIL | Divergência observada e documentada, mesmo que uma repetição tenha passado. |
| BLOCKED | Caso aplicável impedido por uma pré-condição; indicar causa e responsável pela resolução. |
| NOT_RUN | Caso aplicável ainda não executado. |
| N/A | Caso não aplicável à configuração; explicar o motivo. |

Apresentar contagens por ambiente, build, interface e modo. Calcular taxa de passagem sobre casos efetivamente executados (`PASS / (PASS + FAIL)`) e cobertura separadamente sobre variantes aplicáveis planeadas, incluindo `BLOCKED` e `NOT_RUN`. Uma taxa de passagem alta com pouca cobertura não valida a campanha. Repetições são tentativas, não novos casos que aumentam cobertura.

Para aceitar uma build dentro do âmbito declarado:

- Ensaios aplicáveis do núcleo concluídos na matriz acordada, sem falhas abertas que provoquem alteração não autorizada, alvo errado, perda de dados ou exposição de segredos.
- Instalação/versão de pacote, estado de serviço e monitor confirmado/reposto verificados independentemente.
- Casos negativos, cancelamento, isolamento e importação sem retoma aprovados.
- Integração gráfica e evidência física presentes para os ambientes/hardware anunciados.
- Bugs corrigidos repetidos na nova revisão, juntamente com regressões próximas; limitações e casos bloqueados/não executados listados.

Falha de recuperação, alteração inesperada ou exposição de dados exige interromper as alterações na área afetada, conservar evidência e recuperar por um meio independente. O responsável decide quando retomar. Não contornar confirmações/permissões para obter um PASS.

## 8. Onde guardar e reportar os resultados

### Originais locais

Guardar fora do checkout Git, numa pasta privada do utilizador de teste. Exemplo Linux:

```text
~/linux-ai-ensaios/C-20261002-01/
  ENV-01/RUN-001/
    relatorio-ensaio.md
    resultados-casos.csv
    contexto.txt
    evidencias/PKG-02/tentativa-01/
      pedido-resposta.txt
      antes.txt
      depois.txt
      acao.txt
      app-excerto.log
    partilha/
      relatorio-ensaio.md
      resultados-casos.csv
      evidencias-revistas.zip
```

Diretórios privados (`0700`) e ficheiros privados (`0600`) são a referência Linux. Se usar armazenamento partilhado, escolher explicitamente quem pode ler. Preservar os originais até concluir a análise/reteste; definir no relatório responsável e data de revisão da retenção. Não sincronizar originais com serviços cloud por defeito.

### Registo partilhado

O [repositório 1400015/linux_ai](https://github.com/1400015/linux_ai) é público e tem Issues ativas, confirmado em 2 de outubro de 2026.

- **Campanha:** criar uma Issue `[Ensaio] CAMPANHA — build — ambientes`, com resumo, matriz executada, limitações e relatório/CSV revistos. Ligar cada bug à campanha e execução. Esta é a referência partilhada para a campanha.
- **Bug:** usar uma Issue por comportamento defeituoso em [Issues](https://github.com/1400015/linux_ai/issues), depois de procurar duplicados. Se já existir, acrescentar ambiente, revisão e reprodução ao mesmo bug.
- **Relatórios estáveis:** após revisão, integrar por PR um resumo leve em `docs/ensaios/<campanha>/`, com links para Issues e anexos. Esta pasta é uma convenção proposta; não contém resultados nesta entrega. Logs/vídeos brutos não devem integrar o histórico Git.
- **Funcionalidade nova/limite conhecido:** registar como proposta ou limitação; explicar impacto e cenário que justificam a evolução. A falha de uma capacidade anunciada deve ser reportada como bug.

Colar o resumo em Markdown na Issue e anexar CSV/JSON/TXT/LOG/PNG ou ZIP revistos. Os anexos são enviados ao selecioná-los na interface; num repositório público ficam acessíveis sem autenticação. Rever antes de os arrastar. Os formatos e limites devem seguir a [documentação de anexos do GitHub](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files).

Se houver uma vulnerabilidade ou evidência que contenha segredos, usar o canal privado de segurança disponibilizado pelo mantenedor. Se **Report a vulnerability** estiver ativo, usar esse formulário; a ativação não foi confirmada neste protocolo. Se não existir canal, pedir apenas um contacto privado, sem publicar segredo ou demonstração explorável. Seguir a [orientação do GitHub para reporte privado](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).

## 9. Como reportar um bug

Usar o [modelo de bug](modelos/relatorio-bug.md). Um título útil seria: `[Bug][Sway] Modo não reposto após expirar confirmação — build <SHA curto>`. É um exemplo de título, não uma falha observada.

O relatório deve permitir reconstruir a situação:

1. Revisão/build e ambiente, caso/variante, conversa/operação e intervalo temporal.
2. Pré-condições e estado inicial; indicar se há instalação limpa, atualização ou configuração modificada.
3. Passos mínimos numerados, incluindo texto exato do pedido e escolha, confirmação/cancelamento e tempo relevante.
4. Resultado esperado e resultado observado, com estado real verificado; transcrever o erro completo relevante.
5. Frequência (`1/3`, por exemplo), última revisão conhecida sem falha e primeira conhecida com falha, se disponíveis.
6. Impacto, recuperação necessária e lista de anexos com descrição. Se não houver stack trace/log, explicar o que foi possível recolher.

Não substituir a observação por um diagnóstico: escrever «o serviço continuou ativo segundo a consulta posterior» antes de propor a causa. Indicar se houve intervenção manual ou alteração externa. Ocultar nomes pessoais consistentemente, conservando os nomes técnicos e a relação entre os alvos necessários para reprodução.

### Gravidade e tratamento

| Gravidade | Impacto observado |
| --- | --- |
| S0 — crítica | Alteração sem autorização/fora do alvo, perda de dados ou exposição de segredos. |
| S1 — alta | Fluxo principal inutilizável, recuperação de monitor falha ou autenticação/isolamento permite comportamento incorreto com impacto elevado. |
| S2 — média | Funcionalidade falha, mas há recuperação ou alternativa utilizável, sem efeitos indevidos. |
| S3 — baixa | Problema visual, tradução ou usabilidade com efeito limitado. |

Gravidade descreve impacto; prioridade e ordem de correção são decididas pelo mantenedor. A triagem deve confirmar reprodução/duplicação, identificar ambientes afetados e fixar o critério de reteste. Fechar como corrigido depois de verificar a revisão da correção no cenário original; se for inconclusivo, manter essa indicação.

## 10. Evidências e ficheiros a anexar

### Conjunto mínimo para qualquer falha

- Relatório de bug preenchido e linha do CSV correspondente.
- Contexto resumido e revisão efetiva da build.
- Pedido/opções/resposta do caso, revistos; IDs e intervalo temporal.
- Excerto do log ou traceback do intervalo, se existir; código de saída quando relevante.
- Estado real antes/depois. Screenshot ou vídeo apenas quando acrescentar informação necessária.

### Anexos adicionais por área

| Área | Evidência útil | Dados a evitar |
| --- | --- | --- |
| Pacotes | Candidato/versão/origem antes da seleção, resultado da transação, consulta da versão instalada e erro do gestor. | Credenciais de repositório, lista completa de pacotes sem necessidade e ficheiros de autenticação. |
| Monitores | Consultas `xrandr --query` ou `swaymsg -t get_outputs` antes/depois, resolução/frequência/escala/layout, horas de aplicação/expiração/reposição e observação visual. | Desktop pessoal, EDID/números de série desnecessários; uma captura virtual não basta para validar imagem física. |
| Serviços | Gestor/PID 1, nome exato, estados/PIDs antes/depois, ativação e erro específico. Definição/drop-in do serviço de teste se necessário. | Logs globais, configurações reais completas ou definições com credenciais. |
| Ficheiros | ID do journal, metadados relevantes, hashes/diff sintético e permissões; erro de escrita/recuperação. | Ficheiro pessoal/configuração integral ou backup bruto. Backups reais ficam locais. |
| Armazenamento | Inventário `lsblk` relevante, montagens/parentesco e sequência de hotplug. | UUIDs, etiquetas/números de série pessoais quando não necessários; imagens de discos. |
| IA/conversa | Modo, fornecedor/modelo, sequência mínima de mensagens sintéticas, erro/timeout e resultado da mudança de conversa. | API keys, `.env`, URL com credenciais, histórico integral ou prompts pessoais. |
| Wi-Fi/impressora/scanner | Estado da ferramenta/daemon, equipamento de teste, seleção, fila e erro, antes/depois. | Password Wi-Fi, SSID/endereços pessoais, conteúdo digitalizado ou documentos impressos reais. |
| Captura/UI | Screenshot recortada, compositor/GTK, sequência de foco/teclado e vídeo curto opcional. | Ecrã completo com outras aplicações, diálogos de autenticação, áudio ou OCR de dados pessoais. |

### Fontes existentes e respetivos limites

| Fonte local por defeito | Conteúdo e cuidado |
| --- | --- |
| `~/.cache/linux_ai_assistant/app.log` e `.1` a `.3` | Log DEBUG, rotação de 1 MiB com três backups. Selecionar o intervalo e rever erros/caminhos; não assumir ocultação completa de todos os módulos. |
| `~/.config/linux_ai_assistant/actions.json` | Auditoria das nove capacidades novas: últimos 512 eventos globais, máximo 4 MiB. Filtrar conversa/operação e recolher logo após o caso para evitar perda por rotação. Não inclui stdout/stderr nem é um histórico completo de tudo o que o programa faz. |
| `~/.config/linux_ai_assistant/changes.json` | Journal de escritas aprovadas, até 500 registos/2 MiB; metadados/hashes e caminhos das cópias, sem conteúdos. Extrair apenas o registo relevante. |
| `~/.config/linux_ai_assistant/history.json` | Conversas e metadados locais. Preferir exportação de uma conversa sintética e revisão manual; não anexar a base completa. |
| Exportação de diagnóstico MD/JSON | Sondagens selecionadas, observações, hipóteses e fontes com ocultação parcial. Limites de entrada/observações podem truncar evidência; assinalar e guardar localmente o original. |

Caminhos podem variar em instâncias com armazenamento personalizado. IDs ajudam a correlacionar eventos; não são autorizações. Uma fase pendente/inacabada exige consulta do sistema, não prova ausência de efeitos.

### Recolha manual com comandos já disponíveis

Exemplos para um terminal Linux com o Python da instalação ensaiada. Executar dentro da pasta privada da execução quando o pacote estiver instalado nesse Python, e substituir os identificadores. Num checkout não instalado, executar a partir do checkout e usar destinos absolutos na pasta privada. Não executar os placeholders literalmente. `system context` descreve o ambiente; registar a revisão da build separadamente.

```bash
# Observação textual: o stdout da CLI também pode conter mensagens de log.
python -m src.cli system context > contexto.txt 2>&1
python -m src.cli knowledge-verify > conhecimento-validacao.txt 2>&1

# Identificar/selecionar a conversa correta antes de consultar a auditoria.
python -m src.cli sessions list
python -m src.cli sessions use ID_REAL_DA_CONVERSA
python -m src.cli actions list > acoes-lista.txt 2>&1
python -m src.cli actions show ID_REAL_DA_OPERACAO > acao.txt 2>&1

# Só o excerto previamente selecionado/revisto; sem sondagens implícitas.
python -m src.cli diagnose 'Sintoma observado no caso' --input app-excerto.log --format json --output diagnostico.json

# Apenas a conversa de teste relevante, depois de rever o seu conteúdo.
python -m src.cli sessions export --session ID_REAL_DA_CONVERSA --format markdown --output conversa.md

# Imagem local para evidência, com nome novo, depois de escolher o alvo.
python -m src.cli capture --window --output captura-nova.png
```

`diagnose --output` e `sessions export --output` criam exports privados e recusam destinos existentes. Não atribuir esta garantia ao comando de captura: escolher um nome novo numa pasta privada e verificar permissões. Ao executar `capture --window` a partir do terminal, o alvo pode ser o próprio terminal; confirmar foco/conteúdo. Em Wayland, disponibilidade e seleção dependem do compositor e ferramentas como grim/slurp; não presumir captura universal em GNOME/KDE.

Não chamar `contexto.txt` ou `acao.txt` de JSON estruturado: podem conter logs antes do conteúdo. O coletor integrado usa objetos internos para produzir JSON limpo.

O botão de captura/OCR da GUI e o atalho Ctrl+S apagam o PNG temporário e acrescentam até 2000 caracteres OCR à conversa. Esse texto pode entrar no contexto de um pedido posterior ao fornecedor de IA. Para anexar uma imagem de evidência, usar uma captura local separada, revista, sem a colocar na conversa.

### Revisão antes da partilha

Conservar original e cópia de partilha separadamente. Remover chaves/tokens/passwords, cabeçalhos de autorização, cookies, chaves privadas, contactos, caminhos pessoais e conteúdo alheio ao ensaio. Usar pseudónimos consistentes para máquinas/redes/alvos; manter os valores técnicos essenciais em recursos sintéticos.

Rever também screenshots, frames de vídeo, OCR, nomes dos ficheiros e conteúdo do ZIP. Ocultação automática é auxiliar e parcial. Listar no relatório o que foi removido, o que ficou truncado e o que não foi recolhido. Se forem necessários dados sensíveis para análise, acordar primeiro um canal privado e acesso restrito.

## 11. Ferramenta para capturar e guardar os ensaios

### Recomendação

**Sim: uma ferramenta pequena e opcional de sessão de ensaio tem utilidade imediata.** O principal ganho é relacionar caso, ambiente, revisão, operação, estado antes/depois e evidências num pacote consistente. A gravação contínua do desktop acrescenta volume, pode captar credenciais e não prova por si só o estado real do sistema.

O primeiro coletor está implementado no botão **Ensaios / Debug** e no comando `trials`, com backend independente da interface. Reutiliza o contexto observado, eventos de ações e metadados do journal, com logs opcionais e anexos escolhidos. Diagnósticos e capturas existentes podem ser anexados manualmente. A recolha automática de probes adicionais, captura de ecrã e vídeo fica para incrementos posteriores. Consultar o [guia da ferramenta](ferramenta-ensaios.md) para os comandos e o funcionamento implementados.

### Contrato e evolução do MVP

1. **Abrir sessão de ensaio:** escolher pasta privada, build, ambiente, modo e casos; guardar manifesto versionado e contexto com origem/evidência.
2. **Marcar início/fim de caso:** operador indica ID/variante e anota pedido, escolha e observações; associar conversa/operação e relógio UTC/tempo decorrido.
3. **Recolher o mínimo:** eventos da operação, excerto temporal de logs, registo relevante do journal e probes de leitura escolhidos. Não gravar autorizações/tokens nem executar comandos retirados de logs ou texto livre.
4. **Guardar antes/depois:** snapshots limitados por área, através de funções/probes conhecidos; mostrar falhas e dados truncados. A própria recolha não instala, altera serviços ou modifica monitores.
5. **Adicionar evidência visual explicitamente:** screenshot selecionada/recortada; operador pode pausar e confirmar cada recolha. Começar por anexos manuais, aproveitando as ferramentas disponíveis onde funcionem.
6. **Classificar e exportar:** gerar relatório Markdown, CSV de resultados, JSON de metadados/eventos e ZIP da cópia revista, com manifesto de ficheiros e hashes SHA-256. Hashes verificam integridade do pacote, não certificam a veracidade do ensaio.
7. **Rever antes de partilhar:** pré-visualizar ficheiros/dados ocultados, retirar anexos e conservar original privado. A exportação permanece local; upload e criação de Issues são ações explícitas separadas.

A ferramenta funciona offline, como utilizador normal e sem API de IA. Usa diretórios/ficheiros privados, limites fixos nesta versão, destinos novos e escrita atómica. Não segue symlinks para recolher dados arbitrários nem guarda histórico de shell, teclas globais, áudio ou desktop permanentemente. Não preserva autorizações de execução; os registos não podem retomar ações. A ocultação de segredos continua parcial e requer revisão. Não existe importação de campanhas no MVP.

### Etapas recomendadas e aceitação

| Etapa | Entrega | Critério de aceitação |
| --- | --- | --- |
| 1 — já utilizável | Este protocolo, modelos, recolha manual e primeiras campanhas | Um operador consegue reportar/reproduzir um caso com IDs, build e evidência mínima. |
| 2 — MVP implementado | Coletor, painel GTK/CLI, marcação de casos, evidência filtrada, anexos manuais e exportação revista | Testes de isolamento, privacidade, revisão, paths/limites e exportação passam; a primeira campanha nos ambientes reais continua necessária. |
| 3 — após necessidades reais | Snapshots específicos de recursos e captura selecionada com integração específica/portal quando disponível | Operador escolhe o alvo e confirma o ficheiro; funcionalidade falha claramente sem suporte, mantendo o ensaio manual utilizável. |
| 4 — se os bugs o justificarem | Vídeo curto opcional, sem áudio, com pausa e limites | Testes de foco/monitor ganham informação reproduzível; autenticação e dados pessoais ficam excluídos; revisão e remoção antes de exportar. |

Antes de distribuir o coletor, ensaiar com segredos fictícios, paths malformados, symlinks, disco cheio, cancelamento, permissões insuficientes, relógio alterado e sessões concorrentes. Confirmar que não modifica os registos originais, não envia tráfego inesperado e não confunde um registo importado com autorização de ação. Uma primeira campanha manual permite ajustar estes requisitos antes de automatizar a recolha.

## 12. Relação com os outros documentos

- [Infraestrutura de ações e conhecimento](infraestrutura-acoes-conhecimento.md): contrato, auditoria, capacidades e limitações atuais.
- [Roadmap de ações conversacionais](roadmap-acoes-conversacionais.md): evolução de pacotes/monitores e critérios por etapa.
- [Diagnóstico e recuperação](segunda-fase-2026-10-02.md): exports, ocultação parcial, journal e reposição de ficheiros.
- [Ferramenta de ensaios](ferramenta-ensaios.md): utilização do painel/CLI, recolha, limites, revisão e ZIP.

Rever este protocolo quando se acrescentar um adapter, mudar a autorização/recuperação ou alargar uma afirmação de compatibilidade. Cada capacidade nova deve chegar com casos positivos, negativos, cancelamento, verificação independente, recuperação aplicável e evidência de pelo menos um ambiente real identificado.
