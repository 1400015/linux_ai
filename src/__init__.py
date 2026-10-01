# Linux AI Assistant Package
__version__ = "1.0.0"

# Configurar logging for the package
import logging
import sys

# Os modulos usam `logging.getLogger(__name__)`, ou seja, nomes como
# "src.config_manager". Configurar apenas "linux_ai_assistant" nao atingia
# nenhum deles (nao e ancestral), pelo que os handlers nunca eram aplicados.
# Configuramos o logger do pacote "src", que e ancestral de todos.
logger = logging.getLogger("src")
logger.setLevel(logging.INFO)

# Handler for console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# Handler for ficheiro
try:
    from pathlib import Path
    log_dir = Path.home() / ".cache" / "linux_ai_assistant"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    
    logger.addHandler(file_handler)
except Exception as e:
    print(f"Warning: Could not set up file logging: {e}")

logger.addHandler(console_handler)
