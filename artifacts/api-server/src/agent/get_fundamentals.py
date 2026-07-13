"""Short interest + analyst ratings per ticker — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA", "ARM"]}
Output (stdout JSON): {"items": [ {ticker, short:{...}, analyst:{...}}, ... ]}
"""
import sys, json
import yfinance as yf
from security import sanitize_ticker

REC_LABELS = {
    "strongBuy": "compra forte", "buy": "compra", "hold": "manter",
    "sell": "venda", "strongSell": "venda forte",
}

def for_ticker(ticker: str) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        info = yf.Ticker(ticker).info or {}

        # Short interest
        short_pct = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        shares_short = info.get("sharesShort")
        shares_short_prior = info.get("sharesShortPriorMonth")
        short_change = None
        if shares_short and shares_short_prior and shares_short_prior > 0:
            short_change = round((shares_short - shares_short_prior) / shares_short_prior * 100, 2)
        squeeze = None
        if short_pct is not None:
            squeeze = ("alto" if short_pct > 0.20
                       else "moderado" if short_pct > 0.10 else "baixo")

        # Analyst ratings
        rec_key = info.get("recommendationKey", "")
        target_mean = info.get("targetMeanPrice")
        current_price = info.get("regularMarketPrice") or info.get("currentPrice")
        upside = None
        if target_mean and current_price and current_price > 0:
            upside = round((target_mean - current_price) / current_price * 100, 1)

        return {
            "ticker": ticker,
            "price": round(current_price, 2) if current_price else None,
            "short": {
                "shortPctOfFloat": round(short_pct * 100, 2) if short_pct else None,
                "daysToCover": round(short_ratio, 2) if short_ratio else None,
                "sharesShort": shares_short,
                "changeVsPriorMonthPct": short_change,
                "squeezeRisk": squeeze,
            },
            "analyst": {
                "consensus": REC_LABELS.get(rec_key, rec_key) or None,
                "recommendationMean": round(info["recommendationMean"], 2) if info.get("recommendationMean") else None,
                "numAnalysts": info.get("numberOfAnalystOpinions"),
                "targetMean": target_mean,
                "targetHigh": info.get("targetHighPrice"),
                "targetLow": info.get("targetLowPrice"),
                "upsidePct": upside,
            },
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    items = [for_ticker(t) for t in args.get("tickers", [])]
    print(json.dumps({"items": items}))