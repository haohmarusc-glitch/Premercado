import datetime

import yfinance as yf

from .security import sanitize_ticker


def _period_for_days(days: int) -> str:
    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


def update_all_positions_high_low(positions: list) -> dict:
    """
    Recalcula high/low desde a data de compra para cada posição.
    Recebe a lista de posições (output de get_positions).
    """
    results = []
    for pos in positions:
        if "error" in pos:
            continue
        ticker = pos.get("ticker", "")
        purchase_date = pos.get("purchaseDate") or pos.get("purchase_date") or ""
        try:
            clean = sanitize_ticker(ticker)
            t = yf.Ticker(clean)
            if purchase_date:
                start = datetime.datetime.strptime(purchase_date[:10], "%Y-%m-%d").date()
                days = (datetime.date.today() - start).days
                period = _period_for_days(max(days, 1))
            else:
                period = "1y"
            hist = t.history(period=period)
            if hist.empty:
                results.append({"ticker": ticker, "status": "no_data"})
                continue
            high_price = float(hist["High"].max())
            low_price = float(hist["Low"].min())
            high_date = hist["High"].idxmax().strftime("%Y-%m-%d")
            low_date = hist["Low"].idxmin().strftime("%Y-%m-%d")
            results.append({
                "ticker": ticker,
                "status": "updated",
                "highPrice": high_price,
                "lowPrice": low_price,
                "highDate": high_date,
                "lowDate": low_date,
            })
        except Exception as e:
            results.append({"ticker": ticker, "status": "error", "error": str(e)})

    updated = sum(1 for r in results if r.get("status") == "updated")
    errors = sum(1 for r in results if r.get("status") == "error")
    return {"updated": updated, "errors": errors, "details": results}
