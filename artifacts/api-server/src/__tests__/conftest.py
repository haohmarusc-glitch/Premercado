"""
Garante que `import agent.xxx` funciona ao rodar pytest de qualquer diretório,
sem precisar exportar PYTHONPATH manualmente — replica o mesmo setup que
runner.ts já usa em produção (cwd=artifacts/api-server/src, PYTHONPATH=mesmo dir).
"""

import importlib
import os
import sys

_API_SERVER_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_SERVER_SRC_DIR not in sys.path:
    sys.path.insert(0, _API_SERVER_SRC_DIR)

# Fixa o PACOTE `agent` em sys.modules antes de qualquer teste rodar.
#
# A ameaça específica que motivou isto acabou em 30/08/2026: existia um
# `agent.py` DENTRO de `agent/`, e vários testes inserem `src/agent/` no
# sys.path para importar módulos soltos (`from brt import ...`). A partir
# daí o nome `agent` resolvia para o MÓDULO em vez do pacote — e como ele
# usa import relativo, qualquer `from agent.x import y` coletado depois
# estourava com "attempted relative import with no known parent package".
#
# O sintoma era traiçoeiro: a suíte inteira passava, e o mesmo arquivo
# falhava quando rodado junto com um teste "poluidor" numa ordem diferente.
# Aconteceu duas vezes neste repo. O arquivo virou `llm_runtime.py`, e
# test_scripts_de_spawn_importam.py impede que outro apareça.
#
# A fixação fica: custa um import e continua valendo para qualquer arquivo
# que venha a colidir com o nome do pacote. Uma vez em sys.modules, ele
# ganha de qualquer alteração posterior de sys.path.
importlib.import_module("agent")
