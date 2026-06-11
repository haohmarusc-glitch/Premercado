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
            avg_price = pos.get("avgPrice") or pos.get("avg_price") or 0
            current_price = current.get("regular_market_price") or current.get("last_close")
            shares = pos.get("shares") or pos.get("totalShares") or 0
            invested = pos.get("totalInvested") or (shares * avg_price) or 0

            change_pct = (
                round((current_price - avg_price) / avg_price * 100, 2)
                if current_price and avg_price
                else None
            )
            pnl = (
                round((current_price - avg_price) * shares, 2)
                if current_price and avg_price
                else None
            )

            # drawdown desde o high (se disponível)
            high = pos.get("highSincePurchase") or pos.get("high_since_purchase")
            drawdown_from_high = (
                round((current_price - high) / high * 100, 2)
                if current_price and high
                else None
            )

            enriched.append({
                **pos,
                "currentPrice": current_price,
                "todayChangePct": current.get("change_pct"),
                "changePct": change_pct,
                "pnl": pnl,
                "invested": round(invested, 2),
                "currentValue": round(current_price * shares, 2) if current_price else None,
                "drawdownFromHigh": drawdown_from_high,
            })
        return enriched
    except Exception as e:
        return [{"error": str(e)}]


def get_portfolio_performance() -> dict:
    positions = get_portfolio_positions()
    if not positions or "error" in positions[0]:
        return {"error": "Sem posições"}

    total_invested = total_current = total_pnl = today_pnl = 0
    winners = losers = 0

    for pos in positions:
        shares = pos.get("shares") or pos.get("totalShares") or 0
        avg_price = pos.get("avgPrice") or pos.get("avg_price") or 0
        current = pos.get("currentPrice") or 0
        pnl = pos.get("pnl") or 0
        today_chg = pos.get("todayChangePct") or 0

        total_invested += pos.get("invested") or (shares * avg_price)
        total_current += pos.get("currentValue") or (shares * current)
        total_pnl += pnl
        today_pnl += (current * shares * today_chg / 100) if (current and shares and today_chg) else 0

        if pnl > 0:
            winners += 1
        elif pnl < 0:
            losers += 1

    return {
        "totalInvested": round(total_invested, 2),
        "totalCurrent": round(total_current, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl / total_invested * 100, 2) if total_invested else 0,
        "todayPnl": round(today_pnl, 2),
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
        avg_price = pos.get("avgPrice") or pos.get("avg_price") or 0
        current = pos.get("currentPrice") or 0
        high = pos.get("highSincePurchase") or pos.get("high_since_purchase")
        alert_high = pos.get("alertHighPct") or pos.get("alert_high_pct") or 20.0
        alert_low = pos.get("alertLowPct") or pos.get("alert_low_pct") or -10.0

        if not (avg_price and current):
            continue

        pct = (current - avg_price) / avg_price * 100

        # Alerta de alta acima do threshold
        if pct >= abs(alert_high):
            alerts.append({
                "ticker": ticker, "type": "high",
                "pct": round(pct, 2),
                "message": f"{ticker} +{pct:.1f}% acima do custo médio (alerta: +{abs(alert_high):.0f}%)",
            })

        # Alerta de queda abaixo do threshold
        if pct <= -abs(alert_low):
            alerts.append({
                "ticker": ticker, "type": "low",
                "pct": round(pct, 2),
                "message": f"{ticker} {pct:.1f}% abaixo do custo médio (alerta: -{abs(alert_low):.0f}%)",
            })

        # Drawdown desde o high (alerta se > 15% abaixo do pico)
        if high and current:
            dd = (current - high) / high * 100
            if dd <= -15:
                alerts.append({
                    "ticker": ticker, "type": "drawdown",
                    "pct": round(dd, 2),
                    "message": f"{ticker} {dd:.1f}% abaixo do high desde compra (${high:.2f})",
                })

    return alerts


PORTFOLIO_TOOLS = [
    {
        "name": "get_portfolio_positions",
        "description": (
            "Retorna posições da carteira com custo médio, preço atual, P&L por posição, "
            "variação de hoje, valor investido, valor atual e drawdown desde o high."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_portfolio_performance",
        "description": (
            "Performance geral da carteira: total investido, valor atual, P&L total ($/%),  "
            "variação de hoje, contagem de vencedoras/perdedoras."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_position_alerts",
        "description": (
            "Alertas de posições: tickers que atingiram alerta de alta/baixa configurado "
            "ou estão em drawdown > 15% desde o high."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

PORTFOLIO_DISPATCH = {
    "get_portfolio_positions": get_portfolio_positions,
    "get_portfolio_performance": get_portfolio_performance,
    "get_position_alerts": get_position_alerts,
}
