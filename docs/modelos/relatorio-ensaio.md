# Relatório de ensaio — <campanha_id>

Estado: RASCUNHO · Modelo sem resultados. Substituir os campos `<...>` e apagar instruções que não se apliquem.

## Identificação

| Campo | Valor |
| --- | --- |
| Campanha / execução / ambiente | <C-AAAAmmdd-NN / RUN-NNN / ENV-NN> |
| Observador / responsável pela campanha | <identificadores ou nomes aprovados para partilha> |
| Início / fim UTC | <ISO 8601, por exemplo AAAA-MM-DDThh:mm:ssZ> |
| Fuso local dos logs | <fuso e offset efetivamente observado> |
| Build | <SHA completo, versão e alterações locais> |
| Instalação | <checkout+venv / wheel / pacote nativo; origem e hash do artefacto> |
| Tipo de ensaio | <físico / VM com arranque completo / WSL / fixture> |
| Protocolo utilizado | <versão/data do protocolo e lista de casos selecionados> |
| Issue da campanha | <URL quando criada; deixar pendente até existir> |

## Ambiente e pré-condições

| Campo | Valor observado |
| --- | --- |
| Distribuição / versão / arquitetura | <...> |
| Kernel / libc | <...> |
| Python / GTK / ferramentas relevantes | <versões> |
| Gestor de serviços ativo / evidência | <nome, PID 1 e consulta que confirmou o estado> |
| Sessão / desktop / compositor | <X11 ou Wayland; nome/versão e evidência> |
| GPU / driver / monitores | <hardware de teste; modos/layout relevantes> |
| Modo de assistência / fornecedor / modelo | <offline/local/remote/auto; sem chave> |
| Rede / servidor local | <ligada/desligada; disponível/indisponível> |
| Agente polkit / autenticação | <agente e disponibilidade; não incluir password> |
| Periféricos e aplicações de apoio | <recursos sintéticos/teste usados> |
| Permissões/configuração relevante | <apenas campos necessários e revistos> |
| Snapshot / recuperação independente | <identificação local e procedimento disponível> |

Descrever diferenças relativamente à instalação padrão e estado inicial de pacotes, serviços, ficheiros e monitores. Indicar se um pressuposto não foi confirmado. Não colar o `config.json` inteiro.

## Âmbito planeado e resultados

Anexar `resultados-casos.csv`, uma linha por caso/variante/tentativa. Separar interfaces, modos e ambientes. Remover linhas de exemplo antes de publicar.

| Medida | Valor |
| --- | --- |
| Variantes aplicáveis planeadas | <N> |
| Variantes executadas pelo menos uma vez | <N> |
| Tentativas executadas | <N> |
| PASS / FAIL / BLOCKED / NOT_RUN / N/A | <contagens, indicando se são de variantes ou tentativas> |
| Taxa de passagem das tentativas executadas | <PASS / (PASS + FAIL); não calcular se denominador for zero> |
| Cobertura das variantes aplicáveis planeadas | <variantes executadas / variantes aplicáveis planeadas> |

Não contar repetições como aumento de cobertura. Indicar variantes intermitentes e frequência de falha. N/A exige justificação; BLOCKED e NOT_RUN não são PASS.

## Observações e falhas

| Caso / variante | Observação e estado real | Frequência | Bug / evidência |
| --- | --- | --- | --- |
| <ID / GUI-offline> | <resultado esperado/observado e consulta independente> | <falhas/tentativas> | <URL ou caminho relativo> |

Descrever também recusas esperadas, dados truncados, evidência indisponível e alterações externas durante o ensaio.

## Estado final e recuperação

- Alterações realizadas: <recursos, sem segredos>.
- Reposição automática observada: <o que foi verificado e como>.
- Reposição manual/snapshot: <intervenção e resultado>.
- Estado final confirmado: <pacote/serviço/ficheiro/monitor e método de verificação>.
- Recursos que ainda precisam de reposição: <lista ou nenhum>.

## Avaliação da build

Resultado: <aceite no âmbito descrito / rejeitada / inconclusiva>.

Justificar com evidências. Listar falhas abertas, ambientes/hardware não ensaiados, casos bloqueados/não executados e restrições à afirmação de compatibilidade. Esta avaliação não substitui ensaios nos ambientes ausentes.

## Anexos e partilha

| Ficheiro | Caso/intervalo | O que demonstra | Revisão/ocultação/truncagem |
| --- | --- | --- | --- |
| <caminho relativo da cópia revista> | <ID e horas> | <descrição> | <dados removidos e limites> |

- Originais: <local privado; não publicar caminho pessoal completo>.
- Cópia partilhada: <Issue ou artefacto revisto>.
- Responsável pela retenção / data de revisão: <...>.
- Revisão humana dos anexos concluída por: <...>.

## Reteste de correções

| Bug | Build da correção | Caso/variante/ambiente | Resultado verificado | Evidência |
| --- | --- | --- | --- | --- |
| <URL> | <SHA completo> | <...> | <PASS/FAIL/BLOCKED; frequência> | <...> |

Reexecutar o cenário original e regressões próximas. Não fechar como corrigido apenas porque um teste automatizado passou.
