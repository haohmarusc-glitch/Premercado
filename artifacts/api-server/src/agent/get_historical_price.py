"""Fetch real historical closing prices for a ticker on specific dates.

Input (stdin JSON):  {"ticker": "NVDA", "dates": ["2026-03-20", "2026-05-18"]}
Output (stdout JSON): {"prices": {"2026-03-20": 121.4, "2026-05-18": 134.2}}

For each requested date, returns the close of that day, or the most recent
trading day on/before it (handles weekends/holidays).
"""
import sys, json, re
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

def run(ticker, dates):
    ticker = sanitize_ticker(ticker)
    valid = sorted({d for d in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", str(d))})
    if not valid:
        return {"prices": {}}

    start = (datetime.strptime(valid[0], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (datetime.strptime(valid[-1], "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")

    df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
    if df.empty:
        return {"prices": {}, "error": "Sem dados para o período"}
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].dropna()
    # Normalise index to tz-naive date strings
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()

    prices = {}
    for d in valid:
        target = pd.Timestamp(d)
        on_or_before = close[close.index <= target]
        if len(on_or_before) > 0:
            prices[d] = round(float(on_or_before.iloc[-1]), 2)
    return {"prices": prices}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    result = run(args["ticker"], args.get("dates", []))
    print(json.dumps(result))
