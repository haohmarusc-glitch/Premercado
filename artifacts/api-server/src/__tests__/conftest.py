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
# Existe um `agent.py` DENTRO de `agent/`, e vários testes inserem
# `src/agent/` no sys.path para importar módulos soltos (`from brt import
# ...`). A partir daí o nome `agent` passa a resolver para o MÓDULO
# `agent/agent.py` em vez do pacote — e como `agent.py` usa import relativo,
# qualquer `from agent.x import y` coletado depois estoura com
# "attempted relative import with no known parent package".
#
# O sintoma é traiçoeiro: a suíte inteira passa, e o mesmo arquivo falha
# quando rodado junto com um teste "poluidor" numa ordem diferente. Já
# aconteceu duas vezes neste repo. Importar o pacote aqui resolve de vez:
# uma vez em sys.modules, ele ganha de qualquer alteração posterior de
# sys.path, para todos os testes.
importlib.import_module("agent")
