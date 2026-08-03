"""
startup_probe.py — mede quanto tempo o subprocesso Python leva para ficar de pé.

Por que existe: em 02/08 todos os subprocessos Python do container deployado
estouravam seus timeouts (60s, 120s), inclusive depois das PRs #198 e #199, que
deram orçamento interno aos scripts. A suspeita é que o orçamento nunca chega a
ser consultado -- o processo ainda estaria importando pandas/numpy/yfinance
quando o Node o mata. O indício é indireto: o agente levou ~3 minutos entre o
POST /api/agent/run e o primeiro "STEP:", e importa a mesma pilha.

Indício não é medida. Este módulo separa os dois tempos que a hipótese confunde:

  - `boot`:    do exec() do processo até a PRIMEIRA linha do nosso código.
               Custa nada nosso -- é o interpretador subindo e o container
               entregando CPU/disco. Vem de /proc/self/stat, não do relógio de
               quando este módulo foi importado.
  - `imports`: da primeira linha do nosso código até o fim dos imports pesados.
               Esse é o custo que a medição no sandbox estimou em ~7,7s.

Só stdlib, e de propósito: qualquer dependência aqui entraria na conta que o
módulo existe para medir.

Saída em stderr (stdout é o canal de resultado, e o Node faz JSON.parse nele).
Sempre ligado: são duas linhas por processo, e o custo de não ter a medição
quando algo quebra já ficou caro duas vezes.
"""

from __future__ import annotations

import os
import sys
import time

_IMPORTADO_EM = time.time()


def _inicio_do_processo() -> float | None:
    """Epoch em que o processo começou, de /proc/self/stat (Linux).

    Sem isto só dá para medir a partir do import DESTE módulo, que já é tarde:
    o tempo de o container entregar CPU e o interpretador subir ficaria
    invisível -- e é exatamente o suspeito.
    """
    try:
        with open("/proc/self/stat", encoding="utf-8") as f:
            # O campo 2 (comm) vem entre parênteses e pode conter espaços;
            # cortar no último ")" evita quebrar o índice dos campos seguintes.
            campos = f.read().rsplit(")", 1)[1].split()
        starttime_ticks = int(campos[19])  # campo 22 do stat
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", encoding="utf-8") as f:
            uptime_s = float(f.read().split()[0])
        return (time.time() - uptime_s) + starttime_ticks / hz
    except Exception:
        return None


_INICIO_PROCESSO = _inicio_do_processo()


def _marca(rotulo: str, desde: float | None) -> None:
    if desde is None:
        return
    print(f"[probe] {rotulo} +{time.time() - desde:.2f}s", file=sys.stderr, flush=True)


# Um boot por PROCESSO, não por módulo que chama.
#
# run_checkers.py importa get_intraday_spikes/get_bounce_alerts/
# get_squeeze_alerts sob demanda, e cada um chama boot() no topo. Sem esta
# guarda o log ganhava três "[probe] boot +1.72s" no mesmo processo -- cada
# número certo como "decorrido desde o exec()", mas lido por qualquer humano
# como três boots de 1,72s. O tempo por check quem mede é o run_checkers, que
# imprime a duração real de cada um.
_ja_marcou: set[str] = set()


def _uma_vez(chave: str) -> bool:
    if chave in _ja_marcou:
        return False
    _ja_marcou.add(chave)
    return True


def boot() -> None:
    """Chame na PRIMEIRA linha executável do script, antes dos imports pesados."""
    if _uma_vez("boot"):
        _marca("boot", _INICIO_PROCESSO)


def imports_prontos() -> None:
    """Chame logo depois dos imports pesados (pandas/numpy/yfinance)."""
    if _uma_vez("imports"):
        _marca("imports", _IMPORTADO_EM)
        _marca("total_ate_imports", _INICIO_PROCESSO)
