# Manutenção e compatibilidade

O contrato atual mantém Python `>=3.8` e GTK 3. Os metadados do pacote, a sintaxe do código e a matriz de testes têm de concordar com esse contrato. A análise de tipos tem um âmbito separado e limitado: a versão de Python usada pelo analisador não aumenta o mínimo de execução da aplicação.

## Validação existente

O workflow de testes executa a suite em Python 3.8, 3.10 e 3.12, compila os ficheiros Python e executa Ruff e ShellCheck. O job GTK instala os bindings do sistema e as dependências da aplicação num ambiente virtual com `--system-site-packages`, usa Xvfb e rejeita skips inesperados, incluindo os causados por dependências da interface ausentes.

Para reproduzir as verificações principais num ambiente preparado:

```bash
python -m unittest discover -s tests -v
python -m ruff check src/ tests/ plugins/ scripts/run_gtk_tests.py
python -m src.cli --help
for script in run.sh scripts/*.sh; do bash -n "$script" || exit; done
```

Os extras opcionais são instalados conforme a funcionalidade a validar. Os testes do cofre usam um backend falso, sem acesso a credenciais da sessão. Os testes de encriptação requerem `.[encryption]`; o job GTK instala-o para não aceitar essa ausência como um resultado verde. A execução sob Xvfb não confirma comportamento em todas as distribuições, compositores ou equipamentos físicos.

## Análise de tipos gradual

O workflow `typecheck.yml` usa Python 3.12 e `mypy==2.4.0`, fixado no extra de desenvolvimento. A configuração em `pyproject.toml` seleciona quatro módulos:

| Módulo | Responsabilidade |
| --- | --- |
| `src/render_core.py` | Renderização sem GTK. |
| `src/build_info.py` | Proveniência observada da build. |
| `src/system_context.py` | Contexto local estruturado do sistema. |
| `src/credential_store.py` | Contrato tipado do cofre opcional. |

`check_untyped_defs = true` verifica também os corpos de funções sem anotações. `follow_imports = silent` permite ler tipos de módulos importados sem transformar os seus diagnósticos numa condição de aprovação desta primeira etapa. A configuração rejeita opcionais implícitos e mostra os códigos de erro.

Para executar a mesma análise:

```bash
python -m pip install '.[dev]'
python -m mypy --config-file pyproject.toml
```

O analisador fixado requer Python 3.10 ou superior; o CI utiliza 3.12. Esta condição aplica-se à ferramenta de desenvolvimento. O workflow não afirma que a aplicação inteira esteja sem erros de tipos, e módulos de GUI, configuração e clientes de API não pertencem a este primeiro conjunto. Alargar o conjunto exige rever os contratos e os diagnósticos de cada módulo, sem desativar erros de toda a aplicação para conseguir um resultado verde.

## Versão mínima de Python

Python 3.10 terminou o suporte upstream em 1 de outubro de 2026, segundo o [PEP 619](https://peps.python.org/pep-0619/), e não é uma base adequada para uma futura subida do mínimo. A presença de 3.10 na matriz atual verifica compatibilidade declarada; não prolonga o suporte do interpretador.

Uma eventual escolha de Python 3.11 ou 3.12 como mínimo depende das versões de Debian/Ubuntu, Void e restantes distribuições que o projeto decidir suportar e dos seus pacotes Python/GTK. Essa decisão continua pendente. Não foi alterado `requires-python`, nem retirado Python 3.8 da matriz como efeito das novas funcionalidades ou do mypy.

Ao aprovar uma subida futura, atualiza em conjunto os metadados, instaladores, documentação, matriz de execução e pacotes das distribuições alvo. Confirma os bindings `gi` do sistema dentro do ambiente Python escolhido; instalar o pacote PyPI homónimo não fornece PyGObject. O [protocolo de ensaios reais](protocolo-ensaios-reais.md) define a evidência necessária para ampliar as afirmações de compatibilidade.

## Organização atual e trabalho posterior

As definições de credenciais e modelos têm componentes próprios: `credential_settings.py`, `credential_store.py`, `remote_model_settings.py` e `remote_models.py`. O contrato e a execução de ações, a persistência JSON e o coletor de ensaios também têm módulos separados. O README apresenta uma seleção da árvore, não um inventário completo nem uma afirmação de que o refactoring da aplicação terminou.

Continuam adiados a reorganização geral de `main_window.py` e `ai_client.py`, a migração dos catálogos atuais para gettext e a passagem de GTK 3 para GTK 4. Estas mudanças precisam de preservar os contratos de ações, os limites de rede, as migrações de configuração e os fluxos de confirmação; não são necessárias para ativar o armazenamento opcional de credenciais ou a listagem explícita de modelos.

Os valores predefinidos de modelos só completam configurações novas ou campos em falta. Não devem substituir escolhas existentes como parte de uma atualização de compatibilidade. A configuração, os dados de sessão, as estatísticas de tokens e os relatórios de ensaios têm migrações e contratos próprios; mudar a versão da aplicação não autoriza descartá-los.

## Código, build e receita XBPS

`src/_version.py` é a origem da versão da aplicação e dos metadados Python. Uma receita de distribuição pode apontar para um snapshot publicado específico: a receita XBPS atual identifica a aplicação 1.3.3 no commit `58e6723`. Isso não identifica alterações posteriores na árvore de trabalho. A receita só deve mudar de referência quando existir a fonte correspondente para construir e verificar; instruções de instalação e ensaios devem indicar a build realmente usada.

O coletor regista uma referência fornecida pelo operador ou observa o checkout que contém o código em execução, incluindo o estado local quando é possível determiná-lo. Um ensaio não valida automaticamente a receita de outra build. Consulta o [guia de ensaios](ferramenta-ensaios.md) para as limitações de proveniência e revisão.
