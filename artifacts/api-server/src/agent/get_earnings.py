import sys, json
import yfinance as yf

# bounded_parallel é importado dos DOIS jeitos porque este script roda dos dois
# jeitos: como arquivo solto (scenarios.ts spawna por caminho, sem PYTHONPATH --
# aí sys.path[0] é o próprio diretório agent/) e, em outros pontos, como módulo
# do pacote. Só stdlib dentro dele, então o import flat é seguro.
try:
    from bounded_parallel import deadline_exceeded
except ImportError:
    from agent.bounded_parallel import deadline_exceeded

# ETFs/fundos e índices nunca têm data de resultados no Yahoo Finance — pular
# de cara evita um round-trip de rede que sempre falha (404). Mantido em sync
# manualmente com config.NO_EARNINGS_TICKERS: este script roda como arquivo
# solto (não como `-m agent.get_earnings`), então não pode importar o pacote.
_NO_EARNINGS_TICKERS = frozenset({
    "SGOV", "BIL", "SHV", "SHY", "SPY", "QQQ", "VOO", "IVV", "VTI", "DIA",
    "AGG", "BND", "TLT", "IEF", "GOVT", "MUB", "XLK", "XLF", "XLE", "XLV",
    "SMH", "SOXX", "ARKK", "VXX", "UVXY",
})


def _has_no_earnings_data(ticker):
    t = (ticker or "").strip().upper()
    return t.startswith("^") or t in _NO_EARNINGS_TICKERS


def get_earnings(tickers):
    result = []
    for t in tickers:
        # Para antes de o Node matar o processo, preservando o que já foi
        # buscado (o consumidor já trata earningsDate=None). Ver
        # bounded_parallel.py::deadline_exceeded.
        if deadline_exceeded():
            result.append({"ticker": t, "name": t, "earningsDate": None,
                           "epsEstimate": None, "skipped": "sem tempo"})
            continue
        if _has_no_earnings_data(t):
            result.append({"ticker": t, "name": t, "earningsDate": None, "epsEstimate": None})
            continue
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}
            cal = tk.calendar
            earnings_date = None
            if cal is not None:
                if hasattr(cal, 'empty') and not cal.empty:
                    dates = cal.columns.tolist()
                    if dates:
                        d = dates[0]
                        earnings_date = d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d)[:10]
                elif isinstance(cal, dict) and cal.get('Earnings Date'):
                    earnings_date = str(cal['Earnings Date'][0])[:10]
            result.append({
                "ticker": t,
                "name": info.get("shortName", t),
                "earningsDate": earnings_date,
                "epsEstimate": info.get("epsForward"),
                "sector": info.get("sector"),
            })
        except Exception as e:
            result.append({"ticker": t, "name": t, "earningsDate": None, "epsEstimate": None})
    print(json.dumps(result))

if __name__ == "__main__":
    tickers = sys.argv[1].split(",") if len(sys.argv) > 1 else []
    get_earnings(tickers)
