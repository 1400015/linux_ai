# Linux AI Assistant Package
try:
    from ._version import __version__
except ImportError:  # pragma: no cover - import fora do pacote
    __version__ = "1.0.0"

import logging
import sys

# The modules use `logging.getLogger(__name__)`, producing names like
# "src.config_manager". Configuring only "linux_ai_assistant" did not reach
# any of them (it is not an ancestor), so the handlers were never applied.
# We configure the "src" logger, which is an ancestor of all of them.
logger = logging.getLogger("src")
# DEBUG here so the file handler (also DEBUG) actually receives debug
# records; each handler filters on its own level below.
logger.setLevel(logging.DEBUG)
# Stop records from bubbling to the root logger, which would print them a
# second time (e.g. via a `basicConfig` handler installed by a library).
logger.propagate = False

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_console_handler.setFormatter(_console_formatter)

logger.addHandler(_console_handler)


def setup_file_logging():
    """Instala o file handler de DEBUG (chamado no arranque da app/CLI).

    Antes isto corria NO import do pacote: `import src` criava
    ~/.cache/linux_ai_assistant/app.log como efeito colateral — o import
    não era idempotente nem seguro para biblioteca/testes.
    """
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    try:
        log_dir = Path.home() / ".cache" / "linux_ai_assistant"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"

        # 0600: o log em DEBUG inclui nomes de modelos, erros de providers
        # e contagens de tokens — não deve ser legível por outros
        # utilizadores locais (o handler antigo criava com umask 0644).
        import os
        fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)

        # Rotate: a plain FileHandler grew app.log without bound (DEBUG
        # records from every request, forever). delay=True evita reabrir
        # quando não há registos.
        _file_handler = RotatingFileHandler(
            log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8",
            delay=True,
        )
        _file_handler.setLevel(logging.DEBUG)
        _file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        _file_handler.setFormatter(_file_formatter)
        logger.addHandler(_file_handler)
        return True
    except Exception as e:
        print(f"Warning: Could not set up file logging: {e}")
        return False
