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

import datetime

PERIOD_MAP = {
    "1d":  {"period": "1d",  "interval": "5m",  "days": None},
    "5d":  {"period": "5d",  "interval": "30m", "days": None},
    "1mo": {"period": "1mo", "interval": "1d",  "days": 35},
    "3mo": {"period": "3mo", "interval": "1d",  "days": 95},
    "6mo": {"period": "6mo", "interval": "1d",  "days": 185},
    "1y":  {"period": "1y",  "interval": "1wk", "days": 370},
}

def _fetch(ticker, params):
    """Try period= first; fall back to start=/end= if result is empty."""
    hist = ticker.history(period=params["period"], interval=params["interval"], auto_adjust=True)
    if not hist.empty:
        return hist
    if params["days"]:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=params["days"])
        hist = ticker.history(start=str(start), end=str(end), interval=params["interval"], auto_adjust=True)
    return hist

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
        hist = _fetch(ticker, params)

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
