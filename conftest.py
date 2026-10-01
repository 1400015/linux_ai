"""Pytest/unittest bootstrap único de sys.path.

Antes cada ficheiro de testes (excepto test_installation.py) tinha o seu
próprio hack de sys.path, e test_fusion.py importava módulos como
render_core/i18n de topo enquanto o resto usa src.* — criando DOIS módulos
com estado separado (ex.: _current_lang do i18n). Com este conftest, os
imports padronizam-se em `src.*` e funciona com `pytest` directo a partir
de qualquer directório.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
