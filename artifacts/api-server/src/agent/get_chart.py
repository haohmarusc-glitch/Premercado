#!/usr/bin/env python3
"""
Intraday / historical OHLCV fetcher using yfinance.
Called as: python3 -m agent.get_chart SYMBOL PERIOD
Periods: 1d  5d  1mo  3mo  6mo  1y
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


def _download(symbol, start, end, interval):
    """Try download() with and without multi_level_index to support all yfinance versions."""
    try:
        return yf.download(
            symbol, start=str(start), end=str(end),
            interval=interval, auto_adjust=True,
            progress=False, multi_level_index=False,
        )
    except TypeError:
        # older yfinance — no multi_level_index param
        df = yf.download(
            symbol, start=str(start), end=str(end),
            interval=interval, auto_adjust=True, progress=False,
        )
        # flatten MultiIndex columns if present (yfinance >= 0.2.x returns (Field, Ticker))
        if isinstance(df.columns, __import__("pandas").MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df


def _row_value(row, key):
    """Get a value from a row regardless of column casing."""
    for k in [key, key.lower(), key.upper()]:
        if k in row.index:
            return row[k]
    raise KeyError(key)


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

        hist = _download(symbol, start, end, params["interval"])

        if hist is None or hist.empty:
            print(f"[get_chart] empty for {symbol} {period_key}", file=sys.stderr)
            print(json.dumps({"symbol": symbol, "period": period_key, "candles": []}))
            sys.exit(0)

        print(f"[get_chart] {symbol} {period_key}: {len(hist)} rows, cols={list(hist.columns)}", file=sys.stderr)

        candles = []
        for ts, row in hist.iterrows():
            try:
                t = int(ts.timestamp() * 1000)
                candles.append({
                    "t": t,
                    "o": round(float(_row_value(row, "Open")),  4),
                    "h": round(float(_row_value(row, "High")),  4),
                    "l": round(float(_row_value(row, "Low")),   4),
                    "c": round(float(_row_value(row, "Close")), 4),
                    "v": int(_row_value(row, "Volume")),
                })
            except Exception as e:
                print(f"[get_chart] skipping row {ts}: {e}", file=sys.stderr)
                continue

        print(json.dumps({"symbol": symbol, "period": period_key, "candles": candles}))
    except Exception as e:
        print(f"[get_chart] exception: {e}", file=sys.stderr)
        print(json.dumps({"symbol": symbol, "period": period_key, "candles": [], "error": str(e)}))
