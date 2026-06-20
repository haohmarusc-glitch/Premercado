#!/usr/bin/env python3
"""
Intraday / historical OHLCV fetcher using yfinance.
Called as: python3 -m agent.get_chart SYMBOL PERIOD
Periods: 1d  5d  1mo  3mo  6mo  1y
Outputs a JSON object to stdout.
"""
import sys
import json
import datetime
import yfinance as yf

PERIOD_MAP = {
    "1d":  {"interval": "5m",  "days": 1},
    "5d":  {"interval": "30m", "days": 5},
    "1mo": {"interval": "1d",  "days": 35},
    "3mo": {"interval": "1d",  "days": 95},
    "6mo": {"interval": "1d",  "days": 185},
    "1y":  {"interval": "1wk", "days": 370},
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
        end = datetime.date.today() + datetime.timedelta(days=1)
        start = datetime.date.today() - datetime.timedelta(days=params["days"])

        hist = yf.download(
            symbol,
            start=str(start),
            end=str(end),
            interval=params["interval"],
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )

        if hist is None or hist.empty:
            print(json.dumps({"symbol": symbol, "period": period_key, "candles": []}), file=sys.stdout)
            print(f"[get_chart] empty result for {symbol} {period_key}", file=sys.stderr)
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
            except Exception as e:
                print(f"[get_chart] skipping row {ts}: {e}", file=sys.stderr)
                continue

        print(json.dumps({"symbol": symbol, "period": period_key, "candles": candles}))
    except Exception as e:
        print(f"[get_chart] exception: {e}", file=sys.stderr)
        print(json.dumps({"symbol": symbol, "period": period_key, "candles": [], "error": str(e)}))
