# Linux AI Assistant Package
__version__ = "1.0.0"

# Configurar logging para o package
import logging
import sys

# Criar logger principal
logger = logging.getLogger("linux_ai_assistant")
logger.setLevel(logging.INFO)

# Handler para console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# Handler para ficheiro
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
    print(f"Warning: Não foi possível configurar logging para ficheiro: {e}")

logger.addHandler(console_handler)

# Importar módulos para que o logging esteja disponível
from . import ai_client, config_manager, system_utils, main_window, tray_icon, app
