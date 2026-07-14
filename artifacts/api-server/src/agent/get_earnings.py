import sys, json
import yfinance as yf

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
