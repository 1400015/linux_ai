"""Executa comandos do catálogo confirmados numa unit transient do systemd de utilizador.

Porquê: os comandos do catálogo do assistente offline correm depois de uma
confirmação explícita, mas corriam directamente na sessão do utilizador.
Envolvê-los num serviço transient com `PrivateTmp=yes` isola o `/tmp` da
unit — um comando sugerido por um modelo não lê nem escreve temporários da
sessão — sem mudar o âmbito nos ficheiros do utilizador (esse limite é de
`command_policy`/`allowed_edit_dirs`).

Porquê serviço e não `--scope`: `PrivateTmp=` é uma propriedade de
execução de processos e as scopes não executam processos — o systemd
rejeita-a em units scope. `--wait --pipe` mantém stdio ligado ao chamador
e o estado de saída significativo; `--same-dir` preserva o directório de
trabalho (por omissão o serviço correria em `/`); `RuntimeMaxSec` limita a
unit mesmo que o chamador seja morto — matar o cliente `systemd-run` NÃO
para um serviço transient, ao contrário de uma scope, cujos processos são
ainda filhos do chamador.

Degradação: sem sessão de utilizador do systemd (Void/runit, contentores,
CI) a sonda canário falha uma vez e os comandos correm sem wrapper, exacto
como antes. Uma falha de arranque depois da sonda desactiva o wrapper para
o resto da sessão; quando a falha garante que a unit nunca arrancou
("Failed to start transient"), o retry sem wrapper é seguro — nada
executou.

`LINUX_AI_COMMAND_SANDBOX` controla o comportamento: `0`/`off` desactiva,
`1`/`on` força (sem sonda), qualquer outro valor (ou ausência) é `auto`.
A env é lida por chamada para que a suite de testes possa fixar o
comportamento sem patches.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

from .process_output import run_bounded

# Mensagens do próprio systemd-run (não do comando) que garantem que a unit
# nunca foi criada — só estas autorizam o retry sem wrapper.
_STARTUP_FAILURES = (
    "Failed to start transient",
    "Failed to enqueue",
)

_state = {"available": None, "demoted": False}
_lock = threading.Lock()


def _mode():
    value = os.environ.get("LINUX_AI_COMMAND_SANDBOX", "auto").strip().lower()
    if value in ("0", "off", "no", "false"):
        return "off"
    if value in ("1", "on", "yes", "true"):
        return "on"
    return "auto"


def _prefix(timeout: int):
    seconds = max(1, int(timeout) + 5)
    return [
        "systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet",
        "--same-dir", "-p", "PrivateTmp=yes", "-p", f"RuntimeMaxSec={seconds}",
        "--",
    ]


def _probe(timeout: int) -> bool:
    """True quando uma sessão de utilizador do systemd consegue correr um unit."""
    if not shutil.which("systemd-run"):
        return False
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    # Indicador standard do user manager sem invocá-lo (um `systemctl --user`
    # pode bloquear quando o manager está em arranque ou morto).
    if not runtime or not Path(runtime, "systemd").is_dir():
        return False
    try:
        code, _, _ = run_bounded([*_prefix(min(timeout, 10)), "/bin/true"], 10, 4096)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return code == 0


def _available(timeout: int) -> bool:
    with _lock:
        if _state["available"] is None:
            _state["available"] = _probe(timeout)
        return _state["available"]


def wrap(argv, timeout: int):
    """Devolva argv com o prefixo sandbox, ou argv intacto quando inativo."""
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(item, str) for item in argv)):
        # Não decidir aqui: run_bounded levanta o erro de validação próprio.
        return argv
    if _state["demoted"] or _mode() == "off":
        return argv
    if _mode() == "auto" and not _available(timeout):
        return argv
    return _prefix(timeout) + list(argv)


def started_failed(output: str) -> bool:
    """True quando o systemd-run falhou antes de criar a unit (nada correu)."""
    return isinstance(output, str) and output.startswith(_STARTUP_FAILURES)


def demote() -> None:
    """Desactiva o wrapper para o resto da sessão (chamado após falha de arranque)."""
    with _lock:
        _state["demoted"] = True


def reset_for_tests() -> None:
    """Repor o estado cacheado — apenas para testes que injectam sondas."""
    with _lock:
        _state["available"] = None
        _state["demoted"] = False
