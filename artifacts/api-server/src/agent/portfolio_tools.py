import os
import requests
from typing import List

from .data_fallback import get_stock_data_with_fallback
from .security import sanitize_ticker


def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


def _headers() -> dict:
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def get_portfolio_positions() -> List[dict]:
    try:
        r = requests.get(f"{_api_url()}/api/portfolio", headers=_headers(), timeout=10)
        r.raise_for_status()
        positions = r.json()
        enriched = []
        for pos in positions:
            ticker = pos.get("ticker", "")
            current = get_stock_data_with_fallback(sanitize_ticker(ticker))
            purchase = pos.get("avgPrice") or pos.get("avg_price") or 0
            current_price = current.get("regular_market_price") or current.get("last_close")
            shares = pos.get("shares", 0)
            change_pct = (
                round((current_price - purchase) / purchase * 100, 2)
                if current_price and purchase
                else None
            )
            pnl = (
                round((current_price - purchase) * shares, 2)
                if current_price and purchase
                else None
            )
            enriched.append({**pos, "currentPrice": current_price, "changePct": change_pct, "pnl": pnl})
        return enriched
    except Exception as e:
        return [{"error": str(e)}]


def get_portfolio_performance() -> dict:
    positions = get_portfolio_positions()
    if not positions or "error" in positions[0]:
        return {"error": "Sem posições"}
    total_invested = total_current = total_pnl = 0
    winners = losers = 0
    for pos in positions:
        shares = pos.get("shares", 0)
        purchase = pos.get("avgPrice") or pos.get("avg_price") or 0
        current = pos.get("currentPrice") or 0
        pnl = pos.get("pnl") or 0
        total_invested += shares * purchase
        total_current += shares * current
        total_pnl += pnl
        if pnl > 0:
            winners += 1
        elif pnl < 0:
            losers += 1
    return {
        "totalInvested": round(total_invested, 2),
        "totalCurrent": round(total_current, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl / total_invested * 100, 2) if total_invested else 0,
        "positionsCount": len(positions),
        "winners": winners,
        "losers": losers,
        "positions": positions,
    }


def get_position_alerts() -> List[dict]:
    positions = get_portfolio_positions()
    alerts = []
    for pos in positions:
        if "error" in pos:
            continue
        ticker = pos.get("ticker", "")
        purchase = pos.get("avgPrice") or pos.get("avg_price") or 0
        current = pos.get("currentPrice") or 0
        alert_high = pos.get("alertHighPct") or pos.get("alert_high_pct")
        alert_low = pos.get("alertLowPct") or pos.get("alert_low_pct")
        if not (purchase and current):
            continue
        pct = (current - purchase) / purchase * 100
        if alert_high and pct >= alert_high:
            alerts.append({"ticker": ticker, "type": "high", "message": f"{ticker} subiu {pct:.1f}% acima da compra"})
        if alert_low and pct <= -abs(alert_low):
            alerts.append({"ticker": ticker, "type": "low", "message": f"{ticker} caiu {abs(pct):.1f}% abaixo da compra"})
    return alerts


PORTFOLIO_TOOLS = [
    {
        "name": "get_portfolio_positions",
        "description": "Retorna posições da carteira com preço médio de compra, preço atual, high/low e P&L por posição.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_portfolio_performance",
        "description": "Performance geral da carteira: total investido, valor atual, P&L total e % de retorno.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_position_alerts",
        "description": "Alertas de posições: tickers que atingiram alerta de alta ou baixa configurado.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

PORTFOLIO_DISPATCH = {
    "get_portfolio_positions": get_portfolio_positions,
    "get_portfolio_performance": get_portfolio_performance,
    "get_position_alerts": get_position_alerts,
}
