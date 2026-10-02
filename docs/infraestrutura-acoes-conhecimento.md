# Infraestrutura de ações e conhecimento — 2026-10-02

Esta implementação segue os primeiros quatro incrementos da integração do plano d77void. Acrescenta também a consulta de armazenamento, que precede operações destrutivas. A expansão para novos desktops, gestores e hardware continua incremental.

## Contexto e conteúdo

`SystemContext` contém distribuição, versão, ferramentas, sessão, gestor de serviços, compositor, rede e áudio, com evidência e estados explícitos. Um cliente instalado não prova que um daemon esteja ativo. A seleção de componentes permite, por exemplo, Void com systemd observado; nomes de desktops provenientes do ambiente são inferências.

O contexto inicial é partilhado pela CLI, GUI e prompt. Os adapters de serviços voltam a observar o gestor e o recurso antes de controlar um serviço. `DistroInfo` mantém a interface dos consumidores anteriores; os seus templates continuam disponíveis como documentação. Nas interfaces integradas, as mutações de serviços usam o contrato novo.

Sete módulos YAML e quatro schemas JSON v0.1 são incluídos no wheel. Foram migrados os perfis Void/Debian e nove procedimentos XBPS/APT/runit/systemd, preservando conteúdo, fontes, datas e ordem de pesquisa. Os outros perfis continuam em Python durante a migração gradual. As dependências entre módulos validam a coleção; não declaram que todos os componentes convencionais de uma distribuição estão ativos.

O loader rejeita chaves duplicadas, anchors/aliases, tags de objetos, campos desconhecidos, referências não registadas, ciclos, dependências ausentes, datas inválidas e conteúdo acima dos limites. Comandos descritos nos guias são texto. Apenas IDs de ações e probes conhecidos no código podem resolver para capacidades executáveis. A verificação estrutural não é uma revisão das fontes nem um ensaio numa distribuição.

## Capacidades do contrato

| ID | Comportamento | Verificação / recuperação |
| --- | --- | --- |
| `packages.search` | Pesquisa os índices configurados APT/XBPS. | Identifica nome, versão e origem. |
| `packages.install` | Instala a versão selecionada após o pedido humano. | Revalida versão/origem, consulta estado instalado; sem rollback automático de pacotes. |
| `display.list_modes` | Descobre modos X11/RandR ou Sway nativo. | Conserva as limitações de composição e de snapshot anteriores. |
| `display.apply_mode` | Aplica um modo temporariamente. | Watchdog independente de 15 segundos; confirmação visual e reposição verificada. |
| `services.list` | Lista unidades systemd ou definições/supervisão runit. | Exige evidência de gestor ativo; não presume atividade a partir do cliente. |
| `services.status` | Consulta um serviço descoberto pelo nome exato. | Estado de execução/ativação e identidade da definição. |
| `services.control` | Iniciar, parar, reiniciar, ativar ou desativar um serviço. | Confirmação do alvo, identidade fresca e estado posterior; serviços centrais protegidos. |
| `files.checksum` | Calcula SHA-256 de um ficheiro permitido. | Ficheiro regular, identidade antes/depois, cancelamento e limites; não prova autenticidade. |
| `storage.list` | Consulta a topologia apresentada por `lsblk`. | Valida dados e identifica montagens em descendentes; não autoriza escrita. |

Todos os adapters têm `ActionDefinition`, preparação validada, recurso, risco, privilégio, recuperação declarada e `ActionResult`. O executor só aceita adapters registados no código. As autorizações são tokens de uso único, vinculados à preparação e à conversa, conservados apenas em memória. Dados importados e planos serializados não autorizam nem retomam execução.

Serviços são tratados como alterações de impacto elevado: podem interromper ligações e processos. A confirmação é específica da operação e do alvo, expira após 15 minutos e desaparece ao fechar o programa. No systemd, `enable`/`disable` alteram ativação no arranque, sem `--now`; `start`/`stop`/`restart` alteram a execução atual. No runit, ativar supervisão pode iniciar imediatamente o serviço; desativar um serviço ativo exige que a paragem seja confirmada antes de remover a ligação.

O helper runit recebe apenas operação, nome e fingerprint. A entrada privilegiada verifica PID 1, diretórios/definições protegidos por root e identidade, usa descriptors e lock, e cria/remove apenas a ligação do serviço em `/var/service`. Não aceita caminhos livres nem remove diretórios recursivamente. Definições personalizadas fora de `/etc/sv` são recusadas. Não existe uma reposição universal do estado interno de um processo reiniciado.

## Utilização sem API

Na conversa, por exemplo:

```text
lista serviços
estado do serviço cups
reinicia serviço cups
confirma reiniciar cups.service
checksum /home/utilizador/Downloads/imagem.iso
lista discos
mostra ações
detalhes IDENTIFICADOR_DA_OPERACAO
```

O nome na confirmação é o descoberto pelo adapter: num serviço runit pode ser `cups`; numa unidade systemd pode ser `cups.service`. Pede novamente a ação se mudar de conversa, reiniciar o programa ou expirar a confirmação. Entrada por pipe/ficheiro continua a permitir apenas propostas para alterações.

Na CLI:

```bash
python -m src.cli system context
python -m src.cli knowledge-verify
python -m src.cli actions list
python -m src.cli actions show IDENTIFICADOR_DA_OPERACAO
python -m src.cli chat "lista serviços"
```

Na GUI, o botão **Ações**, junto ao seletor de conversa, mostra tarefa, alvo, estado e ID, com eventos expansíveis. O resultado da operação continua no chat.

## Auditoria e instalação

O registo privado `~/.config/linux_ai_assistant/actions.json` mantém os últimos 512 eventos, com escrita atómica e atribuição à conversa. Regista preparação, execução, comandos, verificação e recuperação de monitores. A impossibilidade de guardar o início impede a execução do backend. Problemas posteriores de auditoria não impedem verificação nem rollback. Operações pendentes/interrompidas não são retomadas automaticamente; verifica o estado real.

O log não guarda mensagens humanas, passwords, parâmetros completos nem stdout/stderr dos backends. Aplica ocultação de credenciais conhecidas aos argumentos dos comandos; novos adapters devem declarar e proteger os seus próprios dados sensíveis. Esta primeira migração cobre as capacidades da tabela. Dispositivos, comandos legados e ficheiros com `ChangeJournal` ainda têm os seus percursos anteriores, a migrar posteriormente.

Atualizar um checkout com venv existente requer instalar as novas dependências:

```bash
./venv/bin/python -m pip install -r requirements.txt
```

`pyproject.toml`, `requirements.txt` e as dependências do template XBPS incluem PyYAML/jsonschema. O wheel inclui os YAML/schemas sem depender do checkout. O template XBPS continua com o commit/checksum da publicação anterior: esses valores devem ser atualizados quando este código for publicado, sem inventar uma revisão para alterações ainda locais.

## Próximos incrementos, pela ordem recomendada

1. Ensaiar pacotes, monitores e serviços completos em ambientes Void/runit e Debian/systemd descartáveis, incluindo autenticação polkit e reinício/crash da aplicação. Fixtures e testes do helper não substituem estes ensaios.
2. Migrar dispositivos e operações legadas para o mesmo contrato; acrescentar um adapter nativo GNOME ou KDE segundo o ambiente usado. Continuar a migração de conhecimento e acrescentar módulos de hardware com fontes e evidência verificáveis.
3. Acrescentar gestores/subsistemas como OpenRC, dinit/66, áudio, energia ou perfis de aplicações conforme casos reais, com descoberta, parâmetros validados e verificação. Perfis d77 descrevem apenas diferenças comprovadas da base.
4. Expandir armazenamento com `findmnt`, swap, relações entre partições/volumes, proteção do disco do sistema/origem, identidade estável e revalidação de hotplug. Um indicador de removível não basta.
5. Implementar gravação ISO com helper restrito, confirmação de perda de dados, flush e verificação dos bytes escritos; ensaiar falhas e alterações de alvo em VM com discos virtuais descartáveis antes de disponibilizar escrita real.

**A gravação de ISOs permanece indisponível nesta entrega.** A inventariação não marca um dispositivo como autorizado para escrita. GNOME/KDE, novos gestores e configuração arbitrária de aplicações também não são anunciados como capacidades executáveis.

Fontes de referência para os novos adapters: [systemctl](https://github.com/systemd/systemd/blob/main/man/systemctl.xml), [sv](https://smarden.org/runit/sv.8), [Void Handbook — serviços](https://docs.voidlinux.org/config/services/index.html), [lsblk](https://man7.org/linux/man-pages/man8/lsblk.8.html). As fontes/proveniência dos guias migrados estão nos módulos YAML.
