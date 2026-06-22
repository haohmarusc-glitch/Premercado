"""Options summary per ticker (put/call ratio, ATM IV) — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA", "ARM"]}
Output (stdout JSON): {"items": [ {ticker, putCallRatio, atmIvPct, ...}, ... ]}
"""
import sys, json, re
import yfinance as yf

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

def for_ticker(ticker: str) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {"ticker": ticker, "error": "Sem opções"}
        exp = expirations[0]
        chain = t.option_chain(exp)
        calls, puts = chain.calls, chain.puts

        total_call_vol = int(calls["volume"].fillna(0).sum()) if not calls.empty else 0
        total_put_vol = int(puts["volume"].fillna(0).sum()) if not puts.empty else 0
        pc_ratio = round(total_put_vol / total_call_vol, 3) if total_call_vol > 0 else None

        spot = getattr(t.fast_info, "last_price", None)
        atm_iv = None
        if spot is not None and not calls.empty:
            near = calls.iloc[(calls["strike"] - spot).abs().argsort()[:3]]
            iv = near["impliedVolatility"].mean()
            atm_iv = round(float(iv) * 100, 2) if iv == iv else None  # NaN guard

        sentiment = ("bearish" if pc_ratio and pc_ratio > 1.0
                     else "bullish" if pc_ratio and pc_ratio < 0.7 else "neutro")

        return {
            "ticker": ticker,
            "expiry": exp,
            "putCallRatio": pc_ratio,
            "atmIvPct": atm_iv,
            "totalCallVolume": total_call_vol,
            "totalPutVolume": total_put_vol,
            "sentiment": sentiment,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    items = [for_ticker(t) for t in args.get("tickers", [])]
    print(json.dumps({"items": items}))
