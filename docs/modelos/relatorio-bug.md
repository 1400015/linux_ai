# [Bug][<área/ambiente>] <comportamento observado> — build <SHA curto>

Modelo sem falha observada. Preencher com um comportamento por Issue; procurar duplicados antes de publicar. Para vulnerabilidades ou anexos sensíveis, usar o canal privado indicado pelo mantenedor.

## Resumo e impacto

<Descrever em uma ou duas frases o que aconteceu e o efeito concreto no utilizador/sistema.>

- Gravidade proposta: <S0 crítica / S1 alta / S2 média / S3 baixa>.
- Frequência: <falhas/tentativas; indicar intermitência>.
- Recuperação/alternativa: <necessária, disponível ou não encontrada>.
- Campanha / ambiente / execução / caso / variante: <IDs>.
- Issue da campanha ou bug semelhante: <URLs verificadas, quando existirem>.

## Build e ambiente

- SHA completo / versão: <...>.
- Origem da instalação / hash do pacote ou wheel: <...>.
- Alterações locais: <nenhuma ou patch necessário, revisto>.
- Distribuição / versão / arquitetura / libc / kernel: <...>.
- Python / GTK / ferramentas relevantes: <...>.
- Gestor de serviços ativo e evidência / sessão / compositor: <...>.
- GPU/monitores ou periférico de teste, quando relevante: <...>.
- Interface / modo de assistência / fornecedor / modelo: <...; nunca incluir chave>.
- Rede / servidor local / agente polkit: <estado relevante>.
- Tipo de ensaio: <físico / VM / WSL / fixture>.

## Pré-condições e estado inicial

<Recurso de teste, configuração mínima, opções apresentadas e estado real antes do pedido. Indicar se a máquina foi reposta e se a falha também ocorre com instalação limpa.>

## Passos mínimos de reprodução

1. <Abrir a interface/conversa ou executar o comando exato no Python da instalação.>
2. <Pedido humano literal com dados sintéticos/pseudónimos consistentes.>
3. <Opção selecionada e confirmação ou cancelamento; indicar o nome exato do recurso.>
4. <Espera, mudança de conversa, fecho, hotplug ou outro desencadeador, se aplicável.>
5. <Consulta independente que demonstrou o estado observado.>

Início / fim UTC: <...>. Fuso/offset dos logs: <...>.

Conversa / operação / registo de alteração: <IDs existentes ou não aplicável>. Se a operação não chegou a criar ID, indicar isso.

## Resultado esperado

<Comportamento suportado pelo protocolo/contrato. Incluir efeito esperado e estado final.>

## Resultado observado

<Resposta da aplicação, erro completo relevante e estado real. Separar observação de hipótese de causa.>

```text
<excerto mínimo revisto; remover este bloco se não houver erro textual>
```

- Código de saída CLI, se aplicável: <...>.
- Consulta independente / resultado: <...>.
- Reposição automática / manual / estado final: <...>.
- Intervenções ou alterações externas: <...>.

## Regressão e hipótese

- Última build conhecida sem falha: <SHA ou desconhecida>.
- Primeira build conhecida com falha: <SHA ou desconhecida>.
- Tentativas adicionais e variantes: <resultados, incluindo as que passaram>.
- Hipótese de causa, se houver: <claramente identificada como hipótese>.

## Evidências revistas

| Anexo | O que demonstra | Caso/intervalo | Dados removidos / truncagem |
| --- | --- | --- | --- |
| <bug.md ou texto da Issue> | Reprodução | <...> | <...> |
| <contexto.txt> | Ambiente observado; build registada separadamente | <...> | <...> |
| <antes.txt / depois.txt> | Estado independente | <...> | <...> |
| <acao.txt / app-excerto.log> | Operação / erro | <...> | <...> |
| <imagem ou vídeo opcional> | Sintoma visual | <...> | <...> |

Remover linhas que não se apliquem. Não anexar `.env`, API keys, passwords, `.encryption_key`, configurações completas, histórico integral ou backups reais. A exportação de conversa não oculta automaticamente mensagens; os diagnósticos só fazem ocultação parcial. Rever conteúdo, nomes de ficheiros e ZIP antes de anexar.

Se não houver evidência disponível, explicar o motivo e o que foi observado. Não é obrigatório anexar um vídeo nem um diagnóstico genérico para reportar um bug reproduzível.

## Critério de reteste

<Comportamento que deverá ser observado na build da correção, no mesmo ambiente e com o mesmo recurso, incluindo verificação independente e casos próximos.>

Reteste: <pendente / build + data + resultado + evidência>. Preencher apenas depois de executar.
