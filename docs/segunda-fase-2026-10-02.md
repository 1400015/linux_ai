# Segunda fase — diagnóstico e recuperação (1.3.0)

## Relatório de diagnóstico

No menu **Relatório de diagnóstico**, descreve o sintoma, cola um excerto de registo e seleciona as verificações locais que queres recolher. Nenhuma vem selecionada. **Preparar relatório** apresenta o sistema identificado, observações, indícios, hipóteses, próximos passos e fontes. Revê o resultado e exporta em Markdown ou JSON.

Não é necessário configurar uma API ou instalar um modelo. Os relatórios não são enviados a um fornecedor, nem adicionados ao contexto de IA ou histórico de conversas. O identificador da conversa de origem fica associado ao relatório, mesmo que a seleção da conversa mude durante a recolha. Fechar o diálogo cancela a continuação da recolha; uma sondagem já iniciada pode terminar até ao seu timeout.

Só são executáveis sete sondagens fixas: interfaces, endereços, rotas IPv4/IPv6, espaço, inodes e memória. Cada uma passa pela autorização atual de `SystemUtils`, tem timeout de oito segundos e saída limitada. Não se executam comandos retirados do texto colado, consultas externas ou reparações.

A entrada tem limite de 64 KiB e cada observação apresentada tem limite de 12 000 caracteres. Falhas de permissão, comandos indisponíveis e resultados truncados são identificados. Uma sondagem falhada não é interpretada como sucesso ou ausência de rota. As exportações criam ficheiros privados e recusam substituir destinos existentes.

Credenciais comuns, chaves privadas, emails, diretórios pessoais, IPs e MACs são ocultados antes da exportação. Esta filtragem é parcial: nomes de máquinas, outros caminhos e segredos em formatos desconhecidos podem permanecer. Remove dados adicionais na entrada e volta a preparar o relatório antes de partilhar.

```bash
python -m src.cli diagnose 'Serviço não arranca' --input erro.log
python -m src.cli diagnose 'Rede indisponível' --collect links --collect addresses --collect routes --collect routes6 --output rede.md
python -m src.cli diagnose 'Disco cheio' --collect disk --collect inodes --format json --output disco.json
journalctl --no-pager -n 50 | python -m src.cli diagnose 'Falha recente' --stdin
```

## Interpretação offline e conhecimento

As regras distinguem resolução DNS, destino inalcançável, ligação recusada, porta ocupada, permissões, falta de espaço, montagem só de leitura, I/O, OOM, TLS, falha de execução systemd, ausência de unidade, runit e erros APT/dpkg/XBPS. A interpretação limita-se ao excerto fornecido e não confirma uma causa raiz. Percentagens de disco e memória só são avaliadas quando o tipo da observação é conhecido; memória usa `available`, não apenas `free`.

Há **25 procedimentos PT/EN**, incluindo cinco novos: porta ocupada/ligação recusada, certificados/relógio, `203/EXEC`, dpkg interrompido e bibliotecas XBPS não resolvidas. As referências foram revistas em 2 de outubro de 2026; revisão documental e testes com exemplos não equivalem a validação de todas as versões das distribuições.

Referências novas: [Void/XBPS](https://docs.voidlinux.org/xbps/troubleshooting/common-issues.html), [dpkg](https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html), [systemd.exec](https://manpages.debian.org/bookworm/systemd/systemd.exec.5.en.html), [sockets](https://man7.org/linux/man-pages/man2/bind.2.html) e [verificação OpenSSL](https://docs.openssl.org/master/man1/openssl-verification-options/).

## Registo e recuperação de ficheiros

As escritas de blocos de ficheiros aprovadas em modo especialista ficam em `~/.config/linux_ai_assistant/changes.json`, com data, conversa, caminho, hashes e localização das cópias. O registo não guarda o conteúdo dos ficheiros. As cópias permanecem junto do ficheiro afetado, com propriedade e permissões preservadas; os ficheiros novos usam `0600`.

Em **Alterações a ficheiros**, seleciona uma alteração, escolhe **Rever recuperação**, inspeciona o diff e confirma. Recuperar uma edição repõe os bytes originais, preservando as permissões atuais, e cria uma cópia do conteúdo substituído. Recuperar uma criação remove apenas o ficheiro criado ainda inalterado, depois de guardar uma cópia de recuperação. Não são removidos diretórios.

São revalidados o caminho permitido, o hash atual, a cópia original e a identidade do diretório. Alterações externas, cópias modificadas/ausentes ou mudanças de permissões de acesso impedem a recuperação. Alterações sucessivas só podem ser repostas quando o conteúdo atual corresponde ao registo selecionado. A pré-visualização gráfica da cópia está limitada a 1 MiB; ficheiros maiores precisam de revisão manual.

O bloqueio do registo serializa operações concorrentes. A reserva do registo acontece antes da escrita e uma falha de backup impede substituir o original. Um registo pendente pode indicar interrupção ou falha de armazenamento depois da escrita: nesse caso, conserva as cópias adjacentes e faz revisão manual. O registo recusa entradas corrompidas e não descarta silenciosamente alterações ao atingir o limite de 500. Para conservar a recuperação, arquiva o registo cheio e os backups associados antes de iniciar outro.

Quando um ficheiro ou backup de sistema não permite leitura pelo utilizador, a revisão usa uma sondagem autenticada e limitada através de `pkexec`. Esta verifica os hashes antes de mostrar o conteúdo; a reposição volta a validá-los. A autenticação pode ser pedida tanto para rever como para repor. Ficheiros privados no diretório pessoal não levam automaticamente a elevação.

O registo cobre escritas de ficheiros aprovadas nesta versão. Os efeitos de comandos arbitrários, instalação de pacotes e alterações anteriores não têm recuperação automática.

```bash
python -m src.cli changes list
python -m src.cli changes show IDENTIFICADOR
python -m src.cli changes restore IDENTIFICADOR --yes
```

Na CLI, `--yes` é a aprovação explícita da recuperação depois de rever o registo e as cópias. Caminhos de sistema continuam a exigir autorização de edição e autenticação `pkexec`.

## Validação

Os testes exercitam exemplos de Ubuntu/Debian e Void/runit/XBPS, interpretação e ocultação de dados, exportação, permissões dinâmicas, falhas de backup/registo, mudanças de ficheiros, symlinks, concorrência entre processos, recuperação de bytes e diálogos GTK reais. O relatório de validação da entrega identifica o ambiente e resultados efetivamente executados.

A recolha local é verificada no Ubuntu/WSL disponível. Não se afirma que todos os procedimentos foram executados numa instalação Void real, nem que foi construído um pacote binário XBPS. A receita conserva o snapshot de origem e um patch cumulativo verificado.

Atalho global, visão, MCP, voz, loja de extensões e downloads de modelos permanecem nas etapas posteriores já acordadas.
