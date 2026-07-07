"""Structured technical indicators per ticker — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA", "ARM"]}
Output (stdout JSON): {"items": [ {ticker, price, rsi, rsiSignal, macd..., sma...}, ... ]}
"""
import sys, json, re
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import pandas as pd

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

def technicals(ticker: str, period: str = "6mo") -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.get_level_values(0)

        close = hist["Close"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else price
        change_pct = round((price - prev) / prev * 100, 2) if prev else None

        # RSI 14
        delta = close.diff()
        avg_gain = delta.clip(lower=0).rolling(14).mean()
        avg_loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal_line

        def _safe(series):
            val = series.iloc[-1]
            return round(float(val), 2) if not pd.isna(val) else None

        sma20 = _safe(close.rolling(20).mean())
        sma50 = _safe(close.rolling(50).mean())
        sma200 = _safe(close.rolling(200).mean()) if len(close) >= 200 else None

        def _pct_diff(a, b):
            return round((a - b) / b * 100, 2) if a and b else None

        vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
        vol_5d_avg = float(volume.iloc[-5:].mean())
        vol_ratio = round(vol_5d_avg / vol_avg20, 2) if vol_avg20 > 0 else None

        hist_val = float(histogram.iloc[-1])
        return {
            "ticker": ticker,
            "price": round(price, 2),
            "changePct": change_pct,
            "rsi": rsi,
            "rsiSignal": "sobrecomprado" if rsi > 70 else "sobrevendido" if rsi < 30 else "neutro",
            "macdHistogram": round(hist_val, 4),
            "macdTrend": "bullish" if hist_val > 0 else "bearish",
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "pctAboveSma50": _pct_diff(price, sma50),
            "pctAboveSma200": _pct_diff(price, sma200),
            "volumeRatio": vol_ratio,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    tickers = args.get("tickers", [])
    # Busca em paralelo (I/O-bound) — sequencial para ~25+ tickers arrisca
    # estourar o timeout do subprocesso no Node quando o yfinance está lento.
    # `technicals()` já captura suas próprias exceções por ticker; o
    # try/except aqui é só uma rede de segurança extra por chamada.
    items = [None] * len(tickers)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(technicals, t): i for i, t in enumerate(tickers)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                items[i] = future.result()
            except Exception as e:
                items[i] = {"ticker": tickers[i], "error": f"{type(e).__name__}: {e}"}
    print(json.dumps({"items": items}))
