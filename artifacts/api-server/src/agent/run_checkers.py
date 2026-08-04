"""Roda VÁRIOS checkers de background num processo só.

Por que existe: o ciclo de 5min do alert-checker.ts spawnava um processo Python
por checker. Cada um paga do zero o import de pandas + numpy + yfinance (~11,6s
a frio, ~1s a quente) -- e, pior, eles subiam praticamente juntos e disputavam a
CPU do container. Medido em produção: 8-46s só de startup, com os três estourando
o próprio timeout na mesma manhã. O custo de import não é intrínseco (a quente é
~1s); os 46s eram contenção.

Rodando os três no mesmo processo o import é pago UMA vez, e há um bônus que não
era o objetivo mas vale mais que ele em rede: check_intraday_spike,
check_dead_cat_bounce e check_squeeze_setup puxam histórico dos MESMOS tickers, e
market_alerts._HIST_CACHE é por processo -- o segundo e o terceiro check passam a
acertar cache em vez de refazer as chamadas.

## Orçamento por check, não um só pro lote

A armadilha óbvia de batelar seria dar um orçamento único ao processo: se o
squeeze pendura, spike e bounce -- que já terminaram -- morrem junto, e o ciclo
entrega zero em vez de dois. Isso trocaria três falhas parciais independentes por
uma falha total, exatamente nas manhãs ruins em que os alertas importam.

Então cada check recebe a SUA fatia do tempo que resta, e o que sobra de quem
terminou cedo é redistribuído pra quem vem depois (ver _fatia). Um check que
estoura a fatia entrega o parcial do bounded_parallel_map; um que explode vai
pra `falhas` e os outros seguem com os resultados deles intactos.

## SIGTERM entrega o que já tem

O Node mata o processo quando o timeout externo vence. Sem tratar o sinal, tudo
que já foi calculado ia pro lixo. O handler abaixo imprime o acumulado e sai --
resultado parcial é melhor que timeout puro, e todos os consumidores do lado
Node já toleram lista vazia ou incompleta.

Input  (stdin JSON):  {"tickers": [...], "checks": ["spike","bounce","squeeze"]}
Output (stdout JSON): {"resultados": {"spike": [...], ...}, "falhas": {"squeeze": "..."}}

Os scripts standalone (get_intraday_spikes.py etc.) continuam existindo e
funcionando -- este módulo importa as funções POR TICKER deles em vez de
reimplementar, então não há uma segunda cópia da lógica pra sair de sincronia.
"""
import json
import signal
import sys
import time

# Mede quanto do tempo do processo é interpretador+import, antes de qualquer
# trabalho útil. Ver startup_probe.py.
from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

from agent import config
from agent.bounded_parallel import (
    MIN_BUDGET_S,
    bounded_parallel_map,
    budget_from_deadline,
    exit_now,
)

# Os módulos de check entram AQUI, não sob demanda dentro de cada _roda_*.
#
# A primeira versão importava cada um dentro da função, pra um check não pago
# não custar o import e pra um ImportError derrubar só o seu check. Custou caro:
# o import pesado (pandas+numpy+yfinance, via market_alerts/tools) passou a
# acontecer DEPOIS de budget_from_deadline() ter calculado a fatia, então o
# tempo dele não entrava em orçamento nenhum.
#
# Produção 04/08: `[run_checkers] spike: 121.9s (fatia de 40.0s)` -- o
# bounded_parallel_map respeitou os 40s dele certinho ("orçamento esgotado com
# 6 pendentes"), e os outros ~80s foram o import escondido dentro da medição do
# check. É exatamente o bug que budget_from_deadline existe pra impedir
# (ver a docstring dele), reintroduzido por uma otimização de import.
#
# Importar aqui devolve o custo pra dentro da janela do probe, onde ele é
# medido e onde o orçamento seguinte já o desconta. O isolamento de falha
# continua existindo -- é o try/except por check no main(), não o import.
from agent.get_bounce_alerts import _bounce_for
from agent.get_intraday_spikes import _spikes_for
from agent.get_squeeze_alerts import _progress_for
from agent.market_alerts import Severity

_probe_imports()

# Fallback de orçamento quando o processo roda sem AGENT_DEADLINE_TS (execução
# manual). Com a variável no env, o orçamento real vem do deadline do chamador.
BUDGET_S = 200.0

# Peso relativo de cada check na divisão do tempo. squeeze pesa o dobro porque
# check_squeeze_setup faz várias chamadas de rede por ticker (yfinance,
# iBorrowDesk, FINRA, Unusual Whales opcional) -- é o mesmo motivo pelo qual o
# timeout dele já era 120s contra 60s dos outros dois.
PESOS = {"spike": 1.0, "bounce": 1.0, "squeeze": 2.0}

# Ordem de execução: do mais barato pro mais caro. Se o tempo acabar, quem fica
# sem fatia é o caro -- e o caro é justamente o que já tem cache de 30min do
# lado do Python (@cached em check_squeeze_setup), então perdê-lo num ciclo
# custa menos que perder spike/bounce, que são de janela curta.
ORDEM = ["spike", "bounce", "squeeze"]

# Preenchido conforme os checks terminam; lido pelo handler de SIGTERM.
_resultados: dict[str, list] = {}
_falhas: dict[str, str] = {}


def _payload() -> str:
    return json.dumps(
        {"resultados": _resultados, "falhas": _falhas}, ensure_ascii=False
    ) + "\n"


def _ao_receber_sigterm(_signum, _frame) -> None:
    print(
        f"[run_checkers] SIGTERM recebido; entregando o parcial "
        f"({len(_resultados)} check(s) prontos)",
        file=sys.stderr,
    )
    exit_now(_payload())


def _fatia(restante_s: float, check: str, pendentes: list[str]) -> float:
    """Divide o tempo que sobrou entre os checks que ainda não rodaram.

    Recalculado a cada passo de propósito: quem termina antes da própria fatia
    devolve a diferença pros seguintes, em vez de o tempo ocioso ser perdido.
    """
    peso_total = sum(PESOS[c] for c in pendentes) or 1.0
    return max(MIN_BUDGET_S, restante_s * PESOS[check] / peso_total)


def _roda_spike(tickers: list[str], budget_s: float) -> list:
    resultados = bounded_parallel_map(
        _spikes_for, tickers, budget_s=budget_s, label="run_checkers/spike"
    )
    alertas = [a for sub in resultados for a in sub]
    ordem = {Severity.CRITICO: 0, Severity.ATENCAO: 1, Severity.INFO: 2}
    alertas.sort(key=lambda a: ordem[a.severity])
    return [a.to_dict() for a in alertas]


def _roda_bounce(tickers: list[str], budget_s: float) -> list:
    resultados = bounded_parallel_map(
        _bounce_for, tickers, budget_s=budget_s, label="run_checkers/bounce"
    )
    return [a.to_dict() for sub in resultados for a in sub]


def _roda_squeeze(tickers: list[str], budget_s: float) -> list:
    resultados = bounded_parallel_map(
        _progress_for, tickers, budget_s=budget_s, label="run_checkers/squeeze"
    )
    alertas = [a for a in resultados if a is not None]
    ordem = {"confirmed": 0, "near": 1}
    alertas.sort(key=lambda a: ordem[a["tier"]])
    return alertas


RUNNERS = {"spike": _roda_spike, "bounce": _roda_bounce, "squeeze": _roda_squeeze}


def main() -> None:
    signal.signal(signal.SIGTERM, _ao_receber_sigterm)

    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS
    pedidos = args.get("checks") or ORDEM
    # Preserva ORDEM (barato -> caro) e ignora nome desconhecido em vez de
    # explodir: o lado Node não pode derrubar o lote inteiro por um typo.
    pendentes = [c for c in ORDEM if c in pedidos]
    desconhecidos = [c for c in pedidos if c not in RUNNERS]
    if desconhecidos:
        print(f"[run_checkers] check(s) desconhecido(s) ignorado(s): {desconhecidos}",
              file=sys.stderr)

    while pendentes:
        check = pendentes.pop(0)
        # Recalcula o tempo restante a cada passo -- budget_from_deadline lê o
        # relógio agora, então já embute o que os checks anteriores gastaram.
        restante = budget_from_deadline(BUDGET_S, label="run_checkers")
        budget = _fatia(restante, check, [check, *pendentes])
        inicio = time.time()
        try:
            _resultados[check] = RUNNERS[check](tickers, budget)
        except Exception as e:
            # Um check que explode não pode levar os outros junto: é toda a
            # razão de existir do orçamento por check.
            _falhas[check] = f"{type(e).__name__}: {e}"
            print(f"[run_checkers] {check} falhou: {e}", file=sys.stderr)
        print(f"[run_checkers] {check}: {time.time() - inicio:.1f}s "
              f"(fatia de {budget:.1f}s)", file=sys.stderr)

    exit_now(_payload())


if __name__ == "__main__":
    main()
