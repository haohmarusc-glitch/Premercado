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
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import config
from agent.market_alerts import check_intraday_spike, Severity


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

    alerts = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_spikes_for, t): t for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            try:
                alerts += future.result()
            except Exception as e:
                print(f"[get_intraday_spikes] {t}: {e}", file=sys.stderr)

    order = {Severity.CRITICO: 0, Severity.ATENCAO: 1, Severity.INFO: 2}
    alerts.sort(key=lambda a: order[a.severity])

    print(json.dumps({"alerts": [a.to_dict() for a in alerts]}, ensure_ascii=False))
