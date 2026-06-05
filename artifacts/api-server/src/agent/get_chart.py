#!/usr/bin/env python3
"""
Intraday / historical OHLCV fetcher using yfinance.
Called as: python3 -m agent.get_chart SYMBOL PERIOD
Periods: 1d  5d  1mo  3mo  6mo  1y
Outputs a JSON object to stdout.
"""
import sys
import json
import yfinance as yf

PERIOD_MAP = {
    "1d":  {"period": "1d",  "interval": "5m"},
    "5d":  {"period": "5d",  "interval": "30m"},
    "1mo": {"period": "1mo", "interval": "1d"},
    "3mo": {"period": "3mo", "interval": "1d"},
    "6mo": {"period": "6mo", "interval": "1d"},
    "1y":  {"period": "1y",  "interval": "1wk"},
}

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: get_chart.py SYMBOL PERIOD"}))
        sys.exit(1)

    symbol = sys.argv[1].upper()
    period_key = sys.argv[2]

    if period_key not in PERIOD_MAP:
        print(json.dumps({"error": f"Unknown period: {period_key}"}))
        sys.exit(1)

    params = PERIOD_MAP[period_key]

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=params["period"], interval=params["interval"])

        if hist.empty:
            print(json.dumps({"symbol": symbol, "period": period_key, "candles": []}))
            sys.exit(0)

        candles = []
        for ts, row in hist.iterrows():
            try:
                t = int(ts.timestamp() * 1000)
                candles.append({
                    "t": t,
                    "o": round(float(row["Open"]), 4),
                    "h": round(float(row["High"]), 4),
                    "l": round(float(row["Low"]), 4),
                    "c": round(float(row["Close"]), 4),
                    "v": int(row["Volume"]),
                })
            except Exception:
                continue

        print(json.dumps({"symbol": symbol, "period": period_key, "candles": candles}))
    except Exception as e:
        print(json.dumps({"symbol": symbol, "period": period_key, "candles": [], "error": str(e)}))
