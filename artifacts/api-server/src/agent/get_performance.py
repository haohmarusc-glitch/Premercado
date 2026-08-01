import sys, json
import yfinance as yf

tickers = sys.argv[1].split(",") if len(sys.argv) > 1 else []
result = {}
for t in tickers:
    try:
        ticker_obj = yf.Ticker(t)
        fi = ticker_obj.fast_info
        price = getattr(fi, "last_price", None)
        # previous_close vem do candle diario oficial (.history()), NAO de
        # fast_info.previous_close -- as duas fontes podem divergir (mesmo
        # motivo/mesmo padrão de tools.py::get_stock_data, já corrigido
        # por causa do mesmo bug no Veredito do Dia: change_pct errado,
        # às vezes com sinal trocado, sistemático em vários tickers).
        prev_close = None
        try:
            hist = ticker_obj.history(period="5d")
            if hist is not None and len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
        except Exception:
            pass
        if prev_close is None:
            prev_close = getattr(fi, "previous_close", None)
        result[t] = {"price": price, "previousClose": prev_close}
    except:
        result[t] = {"price": None, "previousClose": None}
print(json.dumps(result))
