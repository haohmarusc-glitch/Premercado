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
    "1d":  {"interval": "5m",  "period": "1d",  "days": None},
    "5d":  {"interval": "30m", "period": "5d",  "days": None},
    "1mo": {"interval": "1d",  "period": None,  "days": 35},
    "3mo": {"interval": "1d",  "period": None,  "days": 95},
    "6mo": {"interval": "1d",  "period": None,  "days": 185},
    "1y":  {"interval": "1wk", "period": None,  "days": 370},
}


def _flatten(df):
    """Achata MultiIndex de colunas retornado pelo yfinance >= 0.2.x."""
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    return df


def _fetch(symbol, params):
    ticker = yf.Ticker(symbol)
    if params["period"]:
        # intradiário: period= é obrigatório para 1d/5d com 5m/30m.
        # prepost=True inclui os candles de pré/pós-mercado (fora do
        # normal, o yfinance só devolve o pregão regular 9h30-16h ET).
        df = ticker.history(period=params["period"], interval=params["interval"], auto_adjust=True, prepost=True)
    else:
        end = datetime.date.today() + datetime.timedelta(days=1)
        start = datetime.date.today() - datetime.timedelta(days=params["days"])
        df = ticker.history(start=str(start), end=str(end), interval=params["interval"], auto_adjust=True)
    df = _flatten(df)
    if "Close" in df.columns:
        df = df[df["Close"].notna()]
    print(f"[get_chart] {symbol}: shape={df.shape} cols={list(df.columns)}", file=sys.stderr)
    return df


_MARKET_OPEN = datetime.time(9, 30)
_MARKET_CLOSE = datetime.time(16, 0)


def _session_for(ts, intraday: bool) -> str:
    """"pre" | "regular" | "post" -- só tem sentido pra candles intradiários
    (1d/5d, onde o índice do yfinance já vem localizado no fuso do próprio
    pregão, ex. America/New_York pra ativos dos EUA). Períodos diários/
    semanais (1mo+) não têm essa distinção -- cada candle já é um pregão
    inteiro, então sempre "regular"."""
    if not intraday:
        return "regular"
    t = ts.time()
    if t < _MARKET_OPEN:
        return "pre"
    if t >= _MARKET_CLOSE:
        return "post"
    return "regular"


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
    intraday = params["period"] is not None

    try:
        hist = _fetch(symbol, params)

        if hist is None or hist.empty:
            print(f"[get_chart] EMPTY for {symbol} {period_key}", file=sys.stderr)
            print(json.dumps({"symbol": symbol, "period": period_key, "candles": []}))
            sys.exit(0)

        candles = []
        for ts, row in hist.iterrows():
            try:
                t = int(ts.timestamp() * 1000)
                candles.append({
                    "t": t,
                    "o": round(float(row["Open"]),  4),
                    "h": round(float(row["High"]),  4),
                    "l": round(float(row["Low"]),   4),
                    "c": round(float(row["Close"]), 4),
                    "v": int(row["Volume"]),
                    "session": _session_for(ts, intraday),
                })
            except Exception as e:
                print(f"[get_chart] skipping row {ts}: {e}", file=sys.stderr)
                continue

        print(f"[get_chart] OK {symbol} {period_key}: {len(candles)} candles", file=sys.stderr)
        print(json.dumps({"symbol": symbol, "period": period_key, "candles": candles}))
    except Exception as e:
        print(f"[get_chart] EXCEPTION: {e}", file=sys.stderr)
        print(json.dumps({"symbol": symbol, "period": period_key, "candles": [], "error": str(e)}))
