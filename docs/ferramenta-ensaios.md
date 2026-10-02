# Ferramenta local de ensaios e debug

A ferramenta está integrada no Linux AI Assistant através do botão **Ensaios / Debug** (`Trials / Debug`) junto ao seletor de conversas, e da entrada equivalente no menu. O mesmo coletor funciona pela CLI, sem abrir a interface gráfica. Esta combinação permite testar o programa normalmente e recolher evidência mesmo quando a interface gráfica apresenta uma falha.

A recolha funciona offline, sem API de IA e como utilizador normal. Iniciar uma campanha ou um caso regista dados; as alterações a pacotes, monitores, serviços ou ficheiros continuam a ser realizadas pelo fluxo normal da funcionalidade ensaiada. Os registos nunca autorizam nem repetem essas alterações.

Este guia descreve a ferramenta implementada. O [protocolo de ensaios reais](protocolo-ensaios-reais.md) define os ambientes, os casos, a verificação independente e o reporte de resultados e bugs.

## 1. Fluxo recomendado na interface gráfica

1. Abrir **Ensaios / Debug**. Indicar título da campanha, identificador do ambiente, revisão da build e tipo de ensaio: máquina física, VM, WSL ou fixture simulado. Escolher **Iniciar novo ensaio** (`Start new trial`). A revisão é fornecida pelo operador: não é obtida automaticamente do checkout. Se ficar vazia, será registada como `unknown`.
2. Selecionar a conversa de teste na janela principal. No painel, indicar o ID real do caso do protocolo, por exemplo `PKG-01`, a variante/tentativa e as notas de preparação. Escolher **Iniciar caso** (`Begin case`). O caso fica associado à conversa ativa nesse momento.
3. Testar na janela principal ou observar a funcionalidade no sistema. O painel de ensaios não é modal: pode permanecer aberto enquanto se usa o programa. Escrever os pedidos, as escolhas, o resultado esperado e as verificações relevantes nas notas, usando dados sintéticos.
4. Consultar o estado real do alvo e preencher as observações. Selecionar `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN` ou `N/A`, de acordo com o protocolo, e escolher **Registar resultado** (`Record result`). Se houver uma operação específica, indicar o respetivo ID para restringir os eventos dessa conversa. A inclusão do excerto do log da aplicação é opcional e começa desativada.
5. Adicionar apenas os ficheiros de evidência escolhidos, através de **Adicionar evidência local** (`Add local evidence`). Usar **Ver anexo** (`View attachment`) para rever texto ou imagem; **Excluir anexo** (`Exclude attachment`) retira-o do pacote exportável.
6. Repetir para os outros casos. Cada repetição abre um novo registo de tentativa. Concluir a campanha com **Terminar ensaio** (`Finish trial`) quando não houver um caso aberto.
7. Atualizar e ler a pré-visualização completa. Rever também os pixels e metadados de todas as imagens. Assinalar a confirmação de revisão e escolher **Exportar ZIP revisto** (`Export reviewed ZIP`), com um destino novo.

Fechar o painel conserva a campanha e o caso aberto; não os conclui nem lhes atribui um resultado. Reabrir o painel permite continuar. Enquanto há um caso aberto, a campanha não pode ser terminada nem exportada. Também não se pode selecionar outra campanha deixando um caso aberto. Para iniciar uma campanha nova, terminar primeiro a campanha selecionada.

GUI e CLI partilham a campanha selecionada para esse utilizador. Usar um operador por campanha e atualizar o painel após comandos CLI. A janela conserva a sua informação de seleção até ser atualizada; não assumir que duas interfaces coordenam automaticamente as escolhas humanas.

## 2. Utilização pela CLI

Executar com o Python da instalação a testar, como utilizador normal. Num checkout, executar a partir da raiz do projeto. Os comandos de recolha também podem ser usados em scripts: não executam ações do sistema nem solicitam autenticação administrativa.

Exemplo de uma campanha de pesquisa de pacotes, com verificação manual. Substituir a referência da build e os nomes pelos valores reais do ensaio; o ID de caso deve existir no protocolo adotado.

```bash
python -m src.cli trials start --title 'Pesquisa offline' --environment VOID-X11-01 --build 'SHA-ou-identificador-verificado' --type physical
python -m src.cli trials begin PKG-01 --variant 'primeira-tentativa' --notes 'Índices locais existentes; pedido sintético'

# Exercitar a funcionalidade normal; a pesquisa apenas apresenta candidatos.
python -m src.cli chat 'procura o programa VLC'

# Preencher apenas depois de observar e verificar o resultado.
python -m src.cli trials end --result PASS --notes 'Candidatos e origem conferidos; não ocorreu instalação'
python -m src.cli trials finish
python -m src.cli trials preview --output ensaio-preview-novo.md

# Ler o ficheiro completo e rever todos os anexos antes deste comando.
python -m src.cli trials export --output ensaio-revisto-novo.zip --reviewed
```

`--type` aceita `physical`, `vm`, `wsl`, `fixture` ou `unknown`. A CLI não presume que o ambiente seja uma máquina física. `--build` é opcional; a sua ausência produz a referência `unknown`, além da versão do programa e de Python.

### Comandos disponíveis

| Comando | Efeito e opções |
| --- | --- |
| `trials start --title TITULO --environment ID` | Cria e seleciona uma campanha. Aceita `--build REFERENCIA` e `--type TIPO`. |
| `trials list` | Lista campanhas locais. |
| `trials status` | Mostra a campanha selecionada e os seus casos/anexos. |
| `trials use RUN_ID` | Seleciona uma campanha existente. Usar o ID devolvido por `start` ou `list`. |
| `trials begin CASO_ID` | Abre um caso na conversa ativa. Aceita `--session ID`, `--variant TEXTO` e `--notes TEXTO`. `--session` associa o caso sem trocar a conversa ativa do programa. |
| `trials end --result RESULTADO` | Fecha o caso e recolhe evidência. Aceita `--notes TEXTO`, `--operation ID` e `--logs`. Os resultados aceites são `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN` e `N/A`. |
| `trials finish` | Conclui a campanha; exige que o caso esteja fechado. |
| `trials attach CAMINHO` | Adiciona um ficheiro escolhido e devolve o seu ID de anexo. |
| `trials exclude ATT_ID` | Retira um anexo da lista exportável. |
| `trials preview` | Mostra o conteúdo textual completo do pacote e os metadados dos anexos. `--output NOVO_FICHEIRO` cria um ficheiro privado sem substituir ficheiros existentes. |
| `trials export --output NOVO_ZIP --reviewed` | Exporta apenas depois de existir uma pré-visualização atual e de o operador confirmar a revisão. Não conclui a campanha automaticamente. |

Por exemplo, depois de identificar uma operação no painel **Ações** ou em `python -m src.cli actions list`, pode restringir a recolha ao respetivo ID real:

```bash
python -m src.cli trials end --result FAIL --operation ID_REAL_DA_OPERACAO --notes 'Resultado esperado e observado; verificação independente' --logs
```

Não executar o placeholder `ID_REAL_DA_OPERACAO` literalmente. Se a operação não tiver auditoria estruturada, usar notas, metadados do journal e anexos escolhidos. Ausência de eventos não demonstra ausência de efeitos.

O stdout da CLI pode incluir mensagens de log da aplicação. Para guardar a pré-visualização sem esse conteúdo adicional, usar `preview --output`, em vez de redirecionar stdout para um ficheiro supostamente estruturado. A pré-visualização contém todo o texto exportável e pode ser extensa.

## 3. Dados recolhidos e seus limites

| Fonte | Recolha e interpretação |
| --- | --- |
| Metadados da campanha/caso | Título, ambiente, referência explícita da build, versão do programa/Python, tipo de ensaio, IDs, interface, variantes, tempos UTC e resultados declarados pelo operador. Modo/fornecedor/modelo são registados antes e depois de cada caso; uma mudança gera aviso. São seleções de configuração, sem provar que houve inferência ou qual fornecedor respondeu. |
| `SystemContext` | Contexto observado no início da campanha e antes/depois do caso, com disponibilidade e evidência dos componentes. A recolha não executa subprocessos. Estes snapshots descrevem o ambiente; não verificam automaticamente a instalação de um pacote, o estado de um serviço ou a imagem física de um monitor. |
| Auditoria de ações | Eventos novos da conversa associada, comparados com a base existente no início do caso. O filtro opcional de operação restringe estes eventos. Não inclui tokens de autorização, stdout/stderr nem todas as ações antigas do programa. |
| Journal de ficheiros | Metadados novos/alterados de escritas aprovadas nessa conversa, como IDs, estados e hashes. Não copia conteúdos dos ficheiros nem backups; o filtro de operação não restringe o journal. |
| Log da aplicação | Apenas com inclusão explícita. Recolhe um excerto acrescentado desde o início do caso, até 64 KiB, com tratamento limitado de rotação e avisos. O log é global e pode conter informação de outras conversas. |
| Anexos escolhidos | Texto ou PNG/JPEG indicado pelo operador, com identificação, tamanho e hash. Não percorre pastas para incluir ficheiros automaticamente. |

As fontes têm limites e podem sofrer rotação, truncagem ou estar indisponíveis. A ferramenta regista avisos, mas não pode reconstruir informação já perdida. Fechar cada caso logo após a verificação ajuda a conservar a evidência relevante. Notas e anexos continuam a ser necessários para saídas específicas que não constem da auditoria.

O resultado é uma declaração do operador. Escrever nas observações como foi verificado: consulta independente, estado antes/depois, observação visual ou outro método do protocolo. `PASS` não é uma certificação automática. Uma variante não executada fica `NOT_RUN`; casos previstos mas nunca registados devem continuar na matriz de planeamento externa. Repetições aumentam o número de tentativas, não a cobertura.

## 4. Anexos e captura manual

São aceites `.txt`, `.log`, `.md`, `.csv`, `.json`, `.png`, `.jpg` e `.jpeg`. O texto é sujeito a ocultação automática parcial antes de ser guardado; objetos JSON também ocultam campos reconhecidos como credenciais. CSVs são normalizados para colunas separadas por vírgulas, com fórmulas neutralizadas; um CSV anexado pode ter até 10 000 linhas e 256 colunas. Rever sempre o resultado: estes mecanismos não reconhecem todos os segredos e podem ocultar dados relevantes para o diagnóstico.

As imagens mantêm os pixels e metadados originais. A pré-visualização gráfica pode ser reduzida para caber no painel; inspecionar também o ficheiro original e os seus metadados antes de confirmar a revisão. Na CLI, a pré-visualização mostra identificação/hash da imagem; os seus pixels devem ser revistos separadamente.

Para uma captura manual local, escolher um ficheiro novo numa pasta privada e usar a ferramenta de captura já existente, quando suportada pelo ambiente:

```bash
python -m src.cli capture --window --output captura-ensaio-nova.png
python -m src.cli trials attach captura-ensaio-nova.png
```

Confirmar o foco e o conteúdo: a janela ativa pode ser o terminal que lança o comando. Em Wayland, a seleção depende do compositor e das ferramentas de captura disponíveis. O comando `capture` tem o seu próprio comportamento de escrita; não lhe atribuir a garantia de destino novo da exportação de ensaios. Confirmar nome e permissões antes da captura.

O botão de captura/OCR da conversa adiciona texto OCR à conversa e esse texto pode ser usado num pedido posterior à IA. Para evidência visual do ensaio, preferir uma imagem local separada e revista, anexada ao painel de ensaios.

Excluir um anexo remove-o da lista de exportação e invalida a revisão. A cópia privada já recolhida fica no armazenamento local; não é apagada por esse comando. Ficheiros não registados nunca são incluídos por uma varredura da pasta.

## 5. Armazenamento e limites

O armazenamento fica em `~/.local/share/linux_ai_assistant/trials/`, partilhado pelas interfaces do mesmo utilizador:

```text
trials/
  index.json
  run-IDENTIFICADOR/
    run.json
    attachments/
```

Diretórios são privados (`0700`) e ficheiros novos/exportações são privados (`0600`). A recolha de anexos recusa fontes com links, ficheiros não regulares ou de outro proprietário; a recolha e a exportação ZIP recusam caminhos através de diretórios simbólicos. Usar caminhos reais, como utilizador normal, e corrigir a permissão específica quando necessário.

| Limite | Valor |
| --- | --- |
| Campanhas locais | 100 |
| Casos/tentativas por campanha | 100 |
| Anexos registados por campanha | 30 |
| Um anexo de texto | 2 MiB |
| Uma imagem | 10 MiB |
| Soma das cópias recolhidas, incluindo anexos excluídos retidos | 16 MiB |
| Dimensões de imagem | Até 12 000 pixels por lado e 20 milhões de pixels no total |
| Registo estruturado de uma campanha | 8 MiB |
| Evidência de um caso | 320 KiB |
| Excerto de log de um caso | 64 KiB |

Estes limites são fixos nesta versão. Quando o armazenamento de campanhas atingir o limite, exportar e arquivar manualmente as campanhas já revistas antes de continuar. Não há upload, sincronização ou remoção automática por antiguidade. As cópias privadas de anexos excluídos continuam a ocupar espaço; incluir essa retenção na gestão local de dados do laboratório.

## 6. Revisão, exportação e reporte

O ZIP contém `report.md`, `results.csv`, `trial.json`, `manifest.json` e os anexos registados. O CSV segue as colunas do modelo do protocolo; campos que dependem da triagem, como Issue/observador, podem ser completados no relatório de campanha. O manifesto lista ficheiros, tamanhos e hashes SHA-256: permite conferir integridade, não a veracidade das observações.

A revisão fica ligada ao conteúdo exato da pré-visualização. Alterar casos, concluir a campanha, acrescentar/excluir anexos ou modificar o conteúdo guardado exige rever novamente. `--reviewed` sozinho não ultrapassa a ausência de uma pré-visualização atual. Terminar a campanha antes da revisão evita invalidá-la logo a seguir.

Antes de partilhar, verificar texto, nomes de ficheiros, pixels, metadados das imagens e avisos de recolha. Selecionar os anexos mínimos necessários, usando recursos e mensagens sintéticas. Se for preciso remover mais informação de um anexo, preparar uma cópia local adequada, excluir a anterior e adicionar a nova cópia; atualizar depois a pré-visualização.

A exportação cria um ZIP local num destino novo. O envio do pacote e a criação de uma Issue são passos separados do operador, nos locais e com os modelos definidos no [protocolo](protocolo-ensaios-reais.md). Relacionar campanha, caso, tentativa, build e operação no relato do bug. Se o coletor falhar, conservar o erro e usar os modelos/recolha manual do protocolo.

## 7. Evolução prevista

O MVP recolhe dados estruturados e anexos escolhidos. Não inclui captura automática de ecrã, vídeo, áudio, teclas, histórico de shell, cópias completas da configuração/conversas ou envio à IA. A ferramenta não faz upload nem cria Issues automaticamente.

Uma evolução útil, depois dos primeiros ensaios, é acrescentar snapshots específicos de recursos através de adaptadores de leitura conhecidos e captura de janela explicitamente selecionada. Vídeo curto pode ser acrescentado quando bugs de foco, monitores ou intermitência justificarem esse custo. Essas capacidades precisam de revisão, limites e compatibilidade testada nos respetivos ambientes.
