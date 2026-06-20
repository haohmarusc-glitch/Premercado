"""
Garante que `import agent.xxx` funciona ao rodar pytest de qualquer diretório,
sem precisar exportar PYTHONPATH manualmente — replica o mesmo setup que
runner.ts já usa em produção (cwd=artifacts/api-server/src, PYTHONPATH=mesmo dir).
"""

import os
import sys

_API_SERVER_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_SERVER_SRC_DIR not in sys.path:
    sys.path.insert(0, _API_SERVER_SRC_DIR)
