# Segurança, cancelamento e recuperação

A versão **1.4.1** inclui recuperação gráfica do histórico, operações de
ficheiros em segundo plano e proteção de backups sensíveis, conforme o
[registo de alterações](alteracoes.md). A referência da
build e o estado do checkout identificam o código usado em cada ensaio,
incluindo alterações locais.

## Diagnósticos e ocultação de segredos

Logs, diagnósticos e ensaios usam a mesma implementação de ocultação de texto.
Ela reconhece etiquetas completas como `DB_PASSWORD`, `SECRET_KEY`, `AUTH_TOKEN`
e `PGPASSWORD`, incluindo prefixos e valores entre aspas. Reconhece também
padrões de JWT, tokens Slack e identificadores AWS `AKIA`/`ASIA`. Um identificador
de acesso AWS não é a chave secreta correspondente, mas identifica uma credencial
e é retirado dos relatórios.

Esta ocultação é parcial: formatos desconhecidos podem permanecer, e imagens
mantêm os seus pixels. Rever o diagnóstico, o preview de ensaio e os anexos antes
de partilhar continua a ser necessário. O tratamento de argumentos estruturados
conserva as suas fronteiras; em texto antigo, uma atribuição de segredo sem aspas
pode ocultar conservadoramente o resto da linha.

## Política de comandos de diagnóstico

A presença de um programa em `permissions.allowed_commands` continua a ser
necessária. Há ainda uma política de argumentos: `ip` aceita apenas consultas
enumeradas e filtros previstos, recusando mutações e `netns exec`. `ss -K` e as
variantes de destruição de sockets são recusadas. As opções de `file` para
descompressão, leitura de dispositivos, compilação de magic ou desativação do
sandbox são recusadas. `file --apple` é uma opção de apresentação e permanece
permitida quando os restantes argumentos e o caminho são autorizados.

Acrescentar `bash`, Python ou outro motor de execução conhecido à allowlist não
o autoriza como diagnóstico. A política também recusa formas por caminho que
contornariam a validação pelo nome. Esta política de aplicação não cria uma
sandbox do kernel nem audita um programa personalizado instalado pelo operador.
As autorizações de leitura, edição e ações estruturadas mantêm os seus âmbitos.

## Respostas, streams e cancelamento

O limite local de uma resposta é **131072 caracteres**, independente do
`max_tokens` pedido ao fornecedor. O stream tem limites adicionais:

| Recurso | Limite |
| --- | --- |
| Corpo descomprimido | 2 MiB |
| Linha/frame SSE | 256 KiB |
| Eventos | 8192 |
| Leitura do stream | 300 segundos |

Respostas JSON normais dos transportes integrados também têm um limite de
**2 MiB descomprimidos** e um prazo de leitura. Corpos de erro HTTP só são
mostrados se a leitura terminar dentro de **16 KiB e um segundo**; passam pela
mesma ocultação de segredos antes de reduzir o texto apresentado. Um corpo
incompleto é omitido por inteiro para não expor um fragmento de credencial.
O código HTTP permanece disponível.

Atingir um limite comunica uma resposta incompleta. A interface termina o estado
de espera e não guarda essa resposta como completa. Uma falha de persistência
também é comunicada sem deixar o pedido preso em processamento.

No chat da GUI, o cancelamento é propagado ao cliente, à espera pelos cabeçalhos,
à leitura do corpo e às esperas `Retry-After` e backoff. Pedidos cancelados não
devem voltar a tentar inferência
nem iniciar um fallback como se o cancelamento fosse uma indisponibilidade.
Cancelar a espera local **não garante que o fornecedor deixe de calcular ou
cobrar o pedido**, nem desfaz um comando local que já começou.

Quando uma operação local termina depois de cancelar, o resultado efetivo
mantém-se na conversa que a iniciou. Se esse pedido ainda estiver visível,
a interface indica que a operação terminou após o cancelamento. Não se associa
esse resultado a uma conversa nova nem se apresenta o cancelamento como rollback.

## Leitura HTTP e listagem de modelos

A listagem de modelos permanece uma ação explícita nas definições. Usa leitura
em blocos e descompressão limitada, com prazo partilhado entre páginas; o prazo
predefinido de oito segundos inclui espera por ligação/cabeçalhos e corpo. Mantém
os limites agregados de **2 MiB**, **2000 modelos** e **cinco páginas**, os hosts
HTTPS previstos e a recusa de redirecionamentos. Cohere usa `/v1`, correspondente
ao contrato de inferência implementado.

A descoberta corre num processo isolado. Cancelar ou esgotar o prazo termina
e recolhe esse processo, incluindo quando espera por cabeçalhos ou por dados
gzip que ainda não produzem texto. Um temporizador no próprio trabalhador
também limita a sua duração se o frontend desaparecer. A chave é transmitida
por um pipe privado, nunca pelos argumentos do processo.

Nos caminhos gerais de chat, o leitor HTTP permite que o chamador regresse após cancelamento ou prazo, mesmo
quando uma leitura fica bloqueada. Há no máximo **oito trabalhadores HTTP** por
processo. Uma chamada bloqueada antes de devolver cabeçalhos pode continuar em
segundo plano, ocupando o seu lugar até terminar; uma resposta tardia é fechada.
O fecho de uma resposta bloqueada também não deve prender o frontend. Este
comportamento limita a espera do chamador, não promete matar uma thread Python
ou terminar computação remota. Chamadas antigas sem evento de cancelamento
mantêm os timeouts HTTP existentes; plugins e adaptadores externos têm de
respeitar também os contratos próprios.

## Conhecimento local degradado

Um módulo YAML inválido, ausente ou ilegível, ou a ausência de PyYAML, desativa
a coleção YAML completa sem impedir o arranque da CLI ou da interface. PyYAML
continua a ser uma dependência exigida pela instalação. O carregador estrito
continua a recusar conteúdo inválido; não se usa uma coleção parcialmente validada.
As referências incorporadas em Python permanecem disponíveis, e os diagnósticos
dependentes de procedimentos ausentes são desativados. As respostas offline
e o contexto do modelo indicam explicitamente esta limitação. Reinstala a
aplicação para recuperar os módulos distribuídos; não se executam reparações
ou conteúdo proveniente do ficheiro inválido.

## Recuperar um histórico indisponível

Leituras e escritas do histórico local aceitam até **16 MiB** e **128 níveis**
de estrutura JSON. Mensagens aceitam até 131072 caracteres; os limites de
100 conversas, 1000 mensagens retidas por conversa e importação de 2 MiB continuam
a aplicar-se. Dados que ultrapassam limites e formatos de versão desconhecida
são preservados, sem conversão automática.

Desde a versão 1.4.1, a interface valida o histórico antes de abrir a
janela principal. Um documento inválido, uma versão desconhecida ou um limite
excedido abre um diálogo com **Fazer backup e iniciar novo histórico** e
**Cancelar**. A recuperação só começa após essa escolha; cancelar conserva o
original e termina o arranque. A validação e a cópia correm fora da thread GTK.

Se uma escrita falhar durante uma sessão, um aviso persistente indica que as
mensagens não estão a ser guardadas. O aviso e a opção **Recuperar histórico**
no menu da aplicação permitem rever e autorizar a recuperação. Novos pedidos de
chat ficam bloqueados até ela terminar com sucesso. Uma escrita ou recuperação
de ficheiro já aprovada tem de terminar antes de recuperar o histórico, para
conservar o resultado na conversa de origem. Mensagens que o escritor
já tinha em espera são conservadas no novo histórico; não se importam os dados
inválidos ou de versão desconhecida do ficheiro preservado.

Na CLI, para preservar o ficheiro completo e começar um histórico novo:

```bash
python -m src.cli history recover --yes
```

O comando pode ser executado mesmo quando o formato impede o arranque normal.
Cria um backup adjacente `history.json.recovered-*`, com permissões `0600`,
contendo os bytes originais, e só depois publica um novo histórico. Mostra o
caminho do backup; conserva-o para revisão ou recuperação manual. Não importa
automaticamente mensagens de uma versão desconhecida. Uma falha anterior à
cópia completa preserva o original; uma falha de durabilidade posterior comunica
o estado incerto e a localização do backup quando disponível.

O backup pode conter informação privada. A recuperação não o envia a serviços
remotos nem o converte em evidência de um ensaio. O lock existente serializa
estas operações com os escritores de histórico da aplicação.

O tratamento existente de JSON sintaticamente inválido continua a conservar
uma cópia `.corrupt-*` antes da substituição automática. Um JSON válido com
estrutura inválida ou versão desconhecida exige a recuperação explícita acima.

## Arquivar e recuperar alterações de ficheiros

O journal ativo aceita até **500 registos** e **2 MiB**. A reserva do registo e
da referência ao backup acontece antes de alterar o alvo; falta de capacidade
recusa a operação antes dessa alteração. Para libertar capacidade:

```bash
python -m src.cli changes archive --yes
python -m src.cli changes list --archived
```

O arquivo move o journal sob o mesmo lock para
`changes.json.archives/<identificador>.json`, numa pasta privada `0700`.
Conserva IDs, estados, hashes e referências aos backups; os backups continuam
adjacentes aos ficheiros alterados. Não é um descarte de dados. Registos
`pending` ou `recovery_pending` exigem revisão antes do arquivo.

Inspeção e recuperação procuram o ID nos registos ativos e arquivados:

Na janela de alterações de ficheiros, **Incluir alterações de ficheiros
arquivadas** permite rever e recuperar também esses registos. Operações com
publicação incerta só passam à confirmação de recuperação depois de verificar
os hashes atuais e o backup.

```bash
python -m src.cli changes show CHANGE_ID
python -m src.cli changes restore CHANGE_ID --yes
```

A recuperação volta a verificar os caminhos autorizados, a identidade do
diretório e os hashes do alvo e do backup. Um ficheiro alterado entretanto ou
um backup ausente/modificado impede a reposição. O conteúdo que a reposição
substitui também é conservado num backup de recuperação.

Na interface da versão 1.4.1, a preparação dos previews, a
escrita, a listagem do journal e a inspeção/reposição correm em trabalhadores.
A revisão e a confirmação permanecem na thread GTK, que continua a processar
eventos durante a autenticação. O lock da transação do journal mantém-se durante
a operação de ficheiros para conservar a reserva do registo e dos backups.

Cancelar ofertas seguintes ou fechar a janela não desfaz uma escrita já
aprovada e iniciada. Essa operação termina e conserva o resultado efetivo no
journal, incluindo falhas e estados incertos. Um resultado tardio pertence à
conversa que autorizou a escrita.

As falhas distinguem uma operação sem publicação de uma alteração já publicada
com durabilidade incerta. Um erro posterior à substituição do ficheiro não é
prova de que o alvo ficou intacto: conserva-se a referência ao backup e o estado
incerto. Falhas de limpeza são registadas quando conhecidas. Verifica o ficheiro
e os comprovativos antes de repetir ou recuperar uma operação incerta.

## Helpers privilegiados e polkit

As operações privilegiadas de ficheiros e de ativação/desativação de serviços
runit usam duas ações próprias: `org.linux_ai_assistant.files` e
`org.linux_ai_assistant.services`. A política exige autenticação administrativa
da sessão ativa, sem conservar uma autorização reutilizável. A seleção da ação
usa `org.freedesktop.policykit.exec.path`; `pkexec --action-id` não é uma opção
suportada.

A instalação é opcional e separada do instalador normal. Revê e controla a
fonte usada: executar o script por `sudo` autoriza código desse checkout.
Depois instala os helpers:

```bash
sudo bash scripts/install-privileged-helpers.sh
```

O script instala launchers e módulos da biblioteca padrão em
`/usr/libexec/linux-ai-assistant/` e a política em
`/usr/share/polkit-1/actions/org.linux_ai_assistant.policy`. Os ficheiros e os
seus diretórios têm de pertencer a root e não permitir escrita ao grupo/outros.
O cliente usa `/usr/bin/pkexec` e o launcher fixo; este usa `/usr/bin/python3 -IS`
(`-I -S`): isolamento do intérprete e desativação de `site`, sem carregar
`sitecustomize` nem ficheiros `.pth` globais antes das validações.
Verificam-se a instalação, o manifesto de hashes e a ação correspondente antes
da invocação. Não se eleva o Python do venv nem um helper no checkout editável.
Uma instalação ausente, insegura ou incompatível recusa a operação sem fallback
genérico. Reinstala os helpers quando o seu código é atualizado.

Para preparar os ficheiros numa pasta de packaging, sem instalar no sistema:

```bash
DESTDIR=/caminho/absoluto/staging bash scripts/install-privileged-helpers.sh
```

O helper recusa ficheiros de contas, autenticação e autorização, como
`/etc/passwd`, `/etc/shadow`, `/etc/sudoers` e `sudoers.d`, políticas polkit/PAM
e chaves privadas sensíveis. A versão 1.4.1 aplica a mesma recusa
no cliente e no helper a variantes conhecidas de backup desses ficheiros em
`/etc`, como `/etc/shadow-`, `/etc/passwd.bak`, `/etc/sudoers.tmp` e
`/etc/.sudoers.swp`. Estes nomes de backup exigem a mesma administração manual
que os ficheiros originais. Também valida o conteúdo fonte pelo SHA-256 aprovado;
a autenticação não elimina a revalidação do alvo e do backup. O prazo de
**120 segundos** dos helpers de ficheiros inclui autenticação polkit e execução,
na escrita, inspeção e recuperação. Uma caixa de autenticação demorada não
diagnostica um defeito do ficheiro; um timeout não garante ausência de alterações.

A política dedicada cobre estes dois helpers. Os restantes executores mantêm
os seus fluxos autorizados. Operações locais em HOME não exigem esta instalação
de sistema. O manifesto Flatpak continua em **25.08**, sem integração automática
com os helpers do host; esta ronda não publica uma nova receita XBPS.

## Watchdog do ecrã e validação

Uma falha ao gravar o recibo do watchdog independente já não impede comunicar
o estado verificado ao frontend vivo. Se também falhar o pipe, mantém-se a
incerteza e o bloqueio de novas alterações nessa instância. Identidade da sessão,
recuperação e permissões privadas permanecem no [guia do watchdog](recuperacao-ecra-watchdog.md).

O modo independente continua experimental e desativado por defeito. Os testes
automatizados usam ficheiros, credenciais, transportes e ferramentas nativas
fictícios, incluindo processos reais isolados. Não substituem campanhas físicas
X11/Sway, autenticação polkit real ou validação de uma build nativa Void. Consulta
o [protocolo de ensaios reais](protocolo-ensaios-reais.md) antes de ampliar as
afirmações de compatibilidade.
