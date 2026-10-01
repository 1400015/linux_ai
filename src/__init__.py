# Linux AI Assistant Package
__version__ = "1.0.0"

# Set up logging for the package.
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

try:
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    log_dir = Path.home() / ".cache" / "linux_ai_assistant"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    # Rotate: a plain FileHandler grew app.log without bound (DEBUG records
    # from every request, forever).
    _file_handler = RotatingFileHandler(
        log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8",
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    _file_handler.setFormatter(_file_formatter)

    logger.addHandler(_file_handler)
except Exception as e:
    print(f"Warning: Could not set up file logging: {e}")

logger.addHandler(_console_handler)
