import sys, json
import yfinance as yf

tickers = sys.argv[1].split(",") if len(sys.argv) > 1 else []
result = {}
for t in tickers:
    try:
        fi = yf.Ticker(t).fast_info
        result[t] = {
            "price": getattr(fi, "last_price", None),
            "previousClose": getattr(fi, "previous_close", None),
        }
    except:
        result[t] = {"price": None, "previousClose": None}
print(json.dumps(result))
