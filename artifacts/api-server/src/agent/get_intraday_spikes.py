"""Snapshot standalone dos picos intraday (candle de 1min) de volume/preço,
via market_alerts.check_intraday_spike -- pensado pro poller de background
em alert-checker.ts (a cada 5min), que persiste os disparos no Postgres pra
sobreviverem entre polls (ver intraday_spikes na migration 0019).

Mesmo motivo/padrão de import de get_market_alerts_snapshot.py: roda como
`python -m agent.get_intraday_spikes` (import absoluto via pacote) porque
market_alerts.py faz `from .cache import cached` -- import relativo que só
resolve nesse contexto de pacote.

Busca em PARALELO (ThreadPoolExecutor, mesmo padrão de
get_market_alerts_snapshot.py/get_technicals.py) -- cada ticker é uma
chamada de rede de 1min/1d própria via yfinance.

Input (stdin JSON): {"tickers": ["NVDA", ...]}  (default: config.TICKERS)
Output (stdout JSON): {"alerts": [...]}
"""
import sys
import json

from agent import config
from agent.bounded_parallel import bounded_parallel_map, exit_now
from agent.market_alerts import check_intraday_spike, Severity

# Timeout do lado Node (alert-checker.ts::fetchIntradaySpikes) é 60s --
# orçamento aqui fica abaixo disso de propósito, senão o processo Python
# só descobre que estourou quando o Node já matou o subprocesso à força
# (ver bounded_parallel.py).
BUDGET_S = 45


def _spikes_for(ticker: str) -> list:
    try:
        return check_intraday_spike(ticker)
    except Exception as e:
        print(f"[get_intraday_spikes] {ticker}: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS

    results = bounded_parallel_map(_spikes_for, tickers, budget_s=BUDGET_S, label="get_intraday_spikes")
    alerts = [a for sub in results for a in sub]

    order = {Severity.CRITICO: 0, Severity.ATENCAO: 1, Severity.INFO: 2}
    alerts.sort(key=lambda a: order[a.severity])

    exit_now(json.dumps({"alerts": [a.to_dict() for a in alerts]}, ensure_ascii=False) + "\n")
