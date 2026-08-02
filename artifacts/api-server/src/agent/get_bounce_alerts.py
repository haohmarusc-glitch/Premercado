"""Snapshot standalone do alerta de "repique" (dead-cat bounce / possível
realização de lucro), via market_alerts.check_dead_cat_bounce -- pensado pro
poller de background em alert-checker.ts, que envia e-mail quando dispara
(diferente do get_intraday_spikes.py irmão, que só persiste pro card "Alertas
de Mercado" sem notificar por e-mail).

Mesmo motivo/padrão de import de get_intraday_spikes.py: roda como
`python -m agent.get_bounce_alerts` (import absoluto via pacote) porque
market_alerts.py faz `from .cache import cached` -- import relativo que só
resolve nesse contexto de pacote.

Busca em PARALELO (ThreadPoolExecutor, mesmo padrão dos demais get_*.py
standalone) -- cada ticker é uma chamada de rede (yfinance, 6mo history)
própria, cacheada em memória por execução do processo (ver _HIST_CACHE em
market_alerts.py, reaproveitado por check_overbought/_atr_pct/etc quando
rodam no mesmo processo).

Input (stdin JSON): {"tickers": ["NVDA", ...]}  (default: config.TICKERS)
Output (stdout JSON): {"alerts": [...]}  -- cada item já traz `value` com o
sinal correto (positivo = repique de alta dentro de queda maior, negativo =
possível realização de lucro dentro de alta maior) -- suficiente pro
alert-checker.ts decidir a direção sem precisar reparsear o título.
"""
import sys
import json

from agent import config
from agent.bounded_parallel import bounded_parallel_map, budget_from_deadline, exit_now
from agent.market_alerts import check_dead_cat_bounce

# Timeout do lado Node (alert-checker.ts::fetchBounceAlerts) é 60s.
# Fallback quando o processo roda sem AGENT_DEADLINE_TS no env (execução
# manual do script). Com a variável definida, o orçamento real vem do
# deadline do chamador via budget_from_deadline() -- constante fixa aqui
# não conseguia cobrir o custo de import (~8s de pandas/numpy/yfinance),
# que sai da mesma folga. Ver bounded_parallel.py.
BUDGET_S = 45


def _bounce_for(ticker: str) -> list:
    try:
        return check_dead_cat_bounce(ticker)
    except Exception as e:
        print(f"[get_bounce_alerts] {ticker}: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS

    results = bounded_parallel_map(
        _bounce_for,
        tickers,
        budget_s=budget_from_deadline(BUDGET_S, label="get_bounce_alerts"),
        label="get_bounce_alerts",
    )
    alerts = [a for sub in results for a in sub]

    exit_now(json.dumps({"alerts": [a.to_dict() for a in alerts]}, ensure_ascii=False) + "\n")
