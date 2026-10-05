# Conversas, imagens, atalho e documentos locais

Estas funcionalidades estão disponíveis a partir da versão **1.4.0**. As conversas
importadas, as imagens e os documentos são dados; não autorizam comandos nem
alterações no sistema.

## Importar conversas

No menu **Conversation History**, escolha **Import conversation** e selecione um
ficheiro JSON ou Markdown exportado pela aplicação. A janela mostra o título e o
texto integral a importar. A confirmação **Import conversation** cria uma nova
conversa; a conversa atual não é substituída.

Na CLI, inspecione primeiro a pré-visualização e importe o mesmo ficheiro apenas
quando estiver de acordo:

```bash
python -m src.cli sessions import conversa.md --preview
python -m src.cli sessions import conversa.md --yes
python -m src.cli sessions import conversa.json --preview
```

`--preview` não grava uma conversa. A importação JSON ou Markdown exige
`--yes`; a sua ausência mostra a pré-visualização e não autoriza persistência.

O Markdown novo conserva o texto legível, com comentários que identificam o
formato, a versão e os limites exatos de cada mensagem. Cabeçalhos, blocos de
código, Unicode e texto que imite esses comentários continuam a pertencer à
mensagem original. Não se inferem papéis de utilizador/assistente a partir de
cabeçalhos. Alterar o texto ou os comentários pode invalidar o enquadramento.

São aceites o formato JSON existente e a versão 1 do Markdown da aplicação.
Markdown antigo sem enquadramento e documentos Markdown arbitrários são
recusados: não permitem distinguir mensagens com segurança. Cada ficheiro pode
ter até 2 MiB, cada mensagem até 131072 caracteres e o número de mensagens tem
de respeitar o limite configurado do histórico (1000 por defeito).

A importação conserva mensagens, título e timestamps válidos. Não recupera
ações pendentes, escolhas de tarefas ou diagnósticos ativos. A pré-visualização
torna visíveis caracteres de controlo para não interpretar escapes do terminal.
Os exports não substituem destinos existentes e são criados com permissões
privadas. Conversas podem conter informação pessoal ou segredos escritos pelo
utilizador; reveja o ficheiro antes de o partilhar.

## Enviar uma imagem com uma pergunta

Escolha o modo remoto, o fornecedor **OpenRouter** e um modelo compatível, por
exemplo `google/gemini-2.5-flash` ou `openai/gpt-4o-mini`. A compatibilidade é uma
lista explícita em `src/image_attachments.py`; um identificador desconhecido não
é tratado como multimodal. A disponibilidade e o preço continuam a depender da
conta e do fornecedor. Esta primeira implementação não envia imagens diretamente
à API Google, Anthropic ou a servidores locais.

Use o botão de imagem ou **Capture an image for AI**, escreva a pergunta e
reveja a imagem e o destino antes de **Attach to next request**. A confirmação
prepara apenas a próxima pergunta; não envia imediatamente. Pode remover o anexo
antes de enviar. OpenRouter e o fornecedor do modelo escolhido recebem os
pixels. Uma alteração do ficheiro original depois da revisão não altera a imagem
preparada. Cancelar uma captura ou mudar de conversa enquanto ela decorre
invalida o resultado; uma captura que termine mais tarde não anexa a imagem a
outra pergunta.

Na CLI, `--image` sem autorização de envio mostra as dimensões e o destino
preparado, sem fazer um pedido à IA. Esta saída não é uma pré-visualização dos
pixels: reveja o ficheiro num visualizador local. A autorização de envio usa
`--send-image`:

```bash
python -m src.cli chat --image imagem.png 'Explica este gráfico'
python -m src.cli chat --image imagem.png --send-image 'Explica este gráfico'
```

São aceites PNG, JPEG e WebP com uma só imagem, até 12 MiB, 32 megapixels e 12000
pixels por lado na origem. O anexo é convertido para JPEG, com no máximo 2048
pixels por lado e 4 MiB. A orientação EXIF é aplicada e os metadados são removidos;
transparência é composta sobre branco. Reveja o resultado, pois a redução e a
compressão podem alterar detalhes pequenos. Informação visível nos pixels não é
ocultada automaticamente.

O envio usa o endpoint oficial OpenRouter e não autoriza fallback para outro
fornecedor. Um modelo incompatível ou erro com o anexo é comunicado; não se
substitui silenciosamente a imagem por OCR ou por uma resposta offline. A imagem
não é guardada no histórico, incluída nas exportações nem repetida em perguntas
seguintes. O texto da conversa segue as regras normais do histórico. As métricas
de tokens da imagem dependem dos valores comunicados pela API. O marcador de
anexo no histórico não inclui o nome do ficheiro; esse nome só é usado na
interface local e não é enviado no payload de imagem.

## Abrir o assistente com um atalho global

O atalho está desligado por defeito. Em **Settings → Appearance**, ative
**Enable global shortcut to open the assistant** e escolha uma combinação com
Ctrl, Alt ou Super. A proposta inicial é
**Ctrl+Alt+Espaço** (`<Ctrl><Alt>space`). O atalho apresenta ou foca a janela; não
lê texto selecionado nem escreve noutras aplicações.

Em X11, a aplicação reserva a combinação através de libX11 e liberta-a quando o
atalho é desligado ou a aplicação termina. Um conflito com outro programa é
comunicado. Em Wayland, o registo usa o portal **GlobalShortcuts** e depende da
aprovação e implementação do ambiente de trabalho. A combinação final pode ser
escolhida pelo próprio desktop. Não se altera a configuração do compositor
automaticamente.

Se a sessão não fornecer o portal ou recusar o pedido, configure um atalho nas
definições do desktop/compositor para executar:

```bash
linux-ai-assistant --show
```

Para a instalação Flatpak, configure o atalho do desktop para:

```bash
flatpak run io.github.linux_ai_assistant --show
```

Numa execução a partir do checkout, use o Python do ambiente instalado:

```bash
python -m src.app --show
```

O comando inicia o assistente se necessário e apresenta a instância já aberta na
mesma sessão. É necessário D-Bus de sessão; a ausência desse bus é comunicada em
vez de abrir outra instância sem controlo. Em Wayland, as regras de foco do
compositor continuam a aplicar-se. Compatibilidade real de portal, foco e
conflitos exige ensaio no ambiente de trabalho usado. O comportamento do portal
num desktop Wayland e da aplicação instalada em Flatpak ainda precisa de ensaio
nesses ambientes.

## Consultar documentos escolhidos

Use **Manage selected documents** para abrir **Local documents**, adicionar
ficheiros, rever conteúdo, atualizar um documento ou remover itens do índice.
A aplicação mantém snapshots privados:
adicionar um documento lê o seu conteúdo uma vez; a pesquisa não volta a ler o
ficheiro de origem. Use **Reindex selected document** para atualizar o snapshot
depois de alterar o original. Falhas de leitura ou importação preservam o índice
anterior. Remover documentos ou limpar o índice não modifica os originais.

Esta primeira implementação usa pesquisa lexical local, sem embeddings,
downloads de modelos ou serviços remotos. Pesquisa palavras do pedido nos
documentos escolhidos e ordena os trechos relevantes. Não é pesquisa semântica:
sinónimos sem palavras comuns podem não produzir resultados. É possível
consultar os trechos sem usar IA.

Na CLI, adicione apenas os ficheiros pretendidos e consulte as referências:

```bash
python -m src.cli documents add manual.md notas.txt
python -m src.cli documents list
python -m src.cli documents search 'resolução de erros DNS'
python -m src.cli documents reindex DOCUMENT_ID
python -m src.cli documents remove DOCUMENT_ID
```

`DOCUMENT_ID` é o identificador mostrado pela listagem. `documents reindex`
aceita vários identificadores ou, sem identificadores, atualiza todos os
snapshots. A limpeza exige a autorização explícita abaixo; sem `--yes` é
recusada. Os ficheiros originais são preservados.

```bash
python -m src.cli documents clear --yes
```

São aceites ficheiros de texto UTF-8, com ou sem BOM, nas extensões `.txt`, `.md`,
`.markdown`, `.rst`, `.csv` e `.log`. Não há importação de PDF, documentos Office,
arquivos ou pastas inteiras. São recusados links simbólicos no caminho,
dispositivos, pipes e caracteres de controlo não textuais. Os limites são:

- 64 documentos e 16 MiB de conteúdo original no total;
- 2 MiB por documento;
- 4096 caracteres por linha e por consulta, com até 64 termos distintos por consulta;
- 4096 trechos no índice.

O índice fica em
`~/.config/linux_ai_assistant/documents/index.sqlite3`, dentro de um diretório
privado. Conserva conteúdo, nome/caminho local, hash e data de importação. Não
está encriptado; as permissões privadas não protegem contra processos com acesso
à conta do utilizador. Remover ou limpar o índice não garante eliminação de
backups externos ou dos dados recuperáveis no dispositivo de armazenamento.

Os resultados incluem referências como
`[document:IDENTIFICADOR:L12-L25]`, com as linhas do snapshot importado. A
atualização explícita mantém o identificador, mas pode alterar as linhas e o
texto. As referências descrevem o conteúdo indexado, não a versão atual de um
ficheiro que tenha sido modificado entretanto.

Para usar documentos numa pergunta, ative **Use selected documents** nesse
pedido. A opção não está ativa por defeito. Antes de enviar a um modelo remoto, reveja os trechos
e o fornecedor apresentados e confirme **Use these excerpts**. A pergunta recebe
no máximo 6000 caracteres de contexto documental; o modelo não recebe a pasta
nem o documento completo automaticamente. O contexto inclui apenas os trechos,
nomes e referências necessários, sem enviar caminhos locais completos.

Na CLI, `--documents` escolhe o índice para a pergunta. Em modo remoto, sem
`--send-document-context`, são mostrados os trechos e não é feito um pedido à IA.
O envio exige a autorização separada:

```bash
python -m src.cli chat --documents 'O que dizem os documentos sobre DNS?'
python -m src.cli chat --documents --send-document-context 'Explica o erro descrito no manual'
```

Use `documents search` para rever os trechos antes de autorizar o envio remoto.
Em modo de guias locais, a pergunta apresenta correspondências e referências
sem fazer um pedido a modelos.

Trechos mostrados em consultas offline ou no fallback local não são guardados
literalmente no histórico da conversa; é guardado apenas um resumo. Assim, uma
pergunta posterior comum não passa esses trechos ao fornecedor através do
histórico. O contexto documental aprovado é transitório; a resposta de IA segue
as regras normais do histórico e pode conter citações dos documentos.

Os documentos são apresentados ao modelo como citações sem autoridade para
executar ações. A resposta deve citar as referências fornecidas e dizer quando
os trechos não sustentam uma conclusão. Estas instruções não garantem a correção
da resposta: confira as linhas citadas. Se estiver a usar um servidor de IA local,
a confiança nesse servidor continua a ser necessária.

## Verificação manual

A disponibilidade de modelos, a captura de ecrã e o atalho dependem do fornecedor
e da sessão gráfica. Os testes simulados não substituem ensaios no ambiente de
trabalho usado pelo utilizador.

- Exporte uma conversa com blocos de código e caracteres acentuados, importe-a
  numa nova conversa e compare o texto. Cancele a pré-visualização e confirme que
  nenhuma conversa é criada.
- Reveja uma imagem com o modelo escolhido, envie uma pergunta e confirme que a
  pergunta seguinte não contém o anexo. Verifique também cancelamento e rejeição
  de um modelo incompatível.
- Teste o atalho com a janela oculta, confirme que apresenta a mesma instância e
  desligue-o. Em Wayland, teste aprovação e recusa do portal; em X11, um conflito
  de teclas.
- Adicione um documento sintético, procure uma frase, compare a referência com
  as linhas e altere o original. Confirme que a pesquisa só muda depois de
  atualizar o índice. Cancele a revisão de contexto remoto e confirme que nenhum
  pedido é enviado.
