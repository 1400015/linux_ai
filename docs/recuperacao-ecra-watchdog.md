# Recuperação do ecrã com um processo separado — experimental

O comportamento predefinido continua a usar o temporizador no processo do
assistente. O temporizador recupera uma alteração não confirmada sem depender
do GTK, mas termina se esse processo receber `SIGKILL`. A opção experimental
`app.display_independent_watchdog: true` passa a operação para um processo
separado. Não está ativada por predefinição: faltam campanhas reais de mudança
de modo e recuperação em X11 e Sway.

## Autoridade e ordem das operações

O processo separado executa **a aplicação e a recuperação**. Deixar a aplicação
no assistente permitiria que um comando atrasado voltasse a alterar o ecrã
depois da recuperação. O trabalhador reutiliza o `DisplayService`, o seu
temporizador armado antes da primeira alteração, o bloqueio que serializa
aplicação/confirmação/recuperação e as verificações do estado observado. Um
comando nativo tem o prazo existente de oito segundos; recuperar vários
monitores pode demorar mais do que o prazo de confirmação.

Dois pipes anónimos, herdados explicitamente pelo trabalhador, transportam
registos JSON de até 4096 bytes. Não existe socket de controlo publicado nem
comando de shell. O pedido contém um modo enumerado e validado, um prazo de
1–120 segundos e um identificador aleatório. O trabalhador descobre novamente
o estado nativo e responde `ready`, sem alterar o ecrã. Só a confirmação desta
preparação pelo assistente permite aplicar o modo. A confirmação final exige
o identificador dessa operação e respeita o prazo monotónico no trabalhador.
Fechar o pipe, terminar o assistente ou deixar o prazo expirar pede recuperação.
Uma resposta de descoberta tardia não prolonga o prazo de confirmação.

O trabalhador não recebe snapshots executáveis do assistente: conserva em
memória o estado que descobriu. Só usa os comandos XRandR/Sway já construídos
pelo serviço; recusa mais de oito saídas, 256 modos ou posições fora dos limites.
Não lê nem aplica snapshots antigos ao arrancar. Um runner Python personalizado
no assistente é recusado neste caminho, salvo a fábrica explícita usada em testes.

## Identidade da sessão e limites

Em Sway, `SWAYSOCK` tem de designar um socket absoluto, sem symlink, pertencente
ao utilizador. Em X11, o protótipo aceita apenas um `DISPLAY` local e exige
`XAUTHORITY` explícito, ficheiro regular privado do utilizador. Antes de cada
comando, verifica novamente dispositivo, inode, proprietário e `ctime` do
socket e, em X11, do ficheiro de autenticação. Cookies nunca são lidos para logs
ou mensagens. Se o compositor/socket ou o ficheiro mudar, a recuperação falha
de forma explícita em vez de enviar o estado antigo para outra sessão. A
verificação é uma observação antes do comando: não elimina uma substituição
entre essa observação e a ligação efetuada pela ferramenta nativa.

`start_new_session=True` separa o trabalhador do PID e grupo do assistente.
Isto permite sobreviver ao `SIGKILL` desse PID. **Não garante sobrevivência à
eliminação do cgroup inteiro**, à morte do próprio trabalhador, ao encerramento
do sistema ou à perda do servidor gráfico. A política do gestor de sessões e
do serviço que lançou a aplicação deve ser verificada numa campanha real.
As ferramentas nativas também têm de cumprir o retorno síncrono esperado:
processos externos, alterações concorrentes do utilizador e comandos que
continuem a escrever após terminarem não estão resolvidos por este protótipo.

Se a comunicação falhar depois de arrancar o trabalhador, o assistente fecha
o pipe, conserva a incerteza e bloqueia novas alterações nessa instância. Não
mata o trabalhador para satisfazer um prazo do frontend. O operador deve
verificar a recuperação antes de reiniciar a aplicação.

## Auditoria e resultado depois da morte do assistente

Com o assistente vivo, o handle publica `confirmed`, `reverted` ou
`recovery_failed` no audit sink já autorizado. O trabalhador não tem autoridade
para editar esse diário. Depois de `SIGKILL`, o diário do assistente pode
permanecer pendente; não se deve interpretá-lo como prova de recuperação.

Cada operação usa um diretório temporário `linux-ai-display-*`, modo `0700`,
aberto com `O_DIRECTORY|O_NOFOLLOW`. O trabalhador herda esse descritor e pode
criar exclusivamente `outcome.json`, modo `0600`, com o identificador e estado
final. O ficheiro não contém o snapshot, nomes de sockets ou credenciais e não
é uma autorização para repetir a operação. O assistente remove-o após o
encerramento normal. Se o assistente morrer, pode ficar em `/tmp` para revisão
manual; uma falha de escrita ou perda de `/tmp` pode impedir este comprovativo.
Gravar esse ficheiro e comunicar o resultado ao assistente são operações
independentes: uma falha do comprovativo não impede o envio de uma recuperação
verificada ao assistente ainda vivo. Se o pipe também falhar, o assistente
mantém a incerteza e exige a verificação do ecrã antes de outra operação.

## Validação efetuada e condição para ativação por predefinição

Os ensaios automáticos usam processos reais e uma ferramenta Sway fictícia
num diretório temporário. Cobrem confirmação, expiração, cancelamento depois
de `ready`, EOF do pipe, aplicação atrasada, substituição da sessão e `SIGKILL`
real do frontend, incluindo sobrevivência e encerramento do trabalhador e
permissões do comprovativo. Não alteram um ambiente de trabalho real.

Antes de ativar por predefinição, executar o protocolo de ensaios reais em
X11 e Sway: mudança efetiva de modo, layout com várias saídas, confirmação
tardia, desconexão, reinício do compositor e `SIGKILL` sob o gestor de sessões
usado pelo utilizador. Registar também o resultado do trabalhador e verificar
que um cgroup terminado não é apresentado como recuperação garantida. Xvfb
pode validar a ligação e o protocolo XRandR, mas não substitui estas campanhas.
No ensaio isolado efetuado neste ambiente, o Xvfb anunciou frequência zero,
recusada pelo validador de modos existente: nenhuma alteração foi tentada.
Esse resultado confirma a recusa segura desse fixture, sem validar uma mudança
efetiva de modo X11.
