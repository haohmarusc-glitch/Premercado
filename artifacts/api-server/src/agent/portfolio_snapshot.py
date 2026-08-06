"""Snapshot das posições abertas da carteira (qty, custo, P&L)."""
from __future__ import annotations

import os

from .http_retry import SESSION


def _internal_headers() -> dict:
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


def get_portfolio_snapshot(include_prices: bool = True) -> dict:
    """
    Snapshot das posições abertas da carteira: quantidade, custo médio,
    investido e (opcional) preço atual + P&L. Use quando o usuário perguntar
    sobre a carteira, patrimônio, quanto tem em X, ou resultado não realizado.
    """
    try:
        r = SESSION.get(
            f"{_api_url()}/api/portfolio",
            headers=_internal_headers(),
            timeout=10,
        )
        r.raise_for_status()
        positions = r.json()
    except Exception as e:
        return {"error": str(e), "positions": []}

    if not isinstance(positions, list):
        return {"error": "resposta inesperada da API de carteira", "positions": []}

    if include_prices:
        from .tools import get_stock_data
    else:
        get_stock_data = None  # type: ignore

    active = []
    for p in positions:
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0.00001:
            continue
        try:
            avg_cost = float(p.get("avgCost") or 0)
        except (TypeError, ValueError):
            avg_cost = 0.0
        try:
            invested = float(p.get("investedAmount") or 0)
        except (TypeError, ValueError):
            invested = 0.0
        try:
            dividends = float(p.get("dividends") or 0)
        except (TypeError, ValueError):
            dividends = 0.0

        row = {
            "ticker": str(p.get("ticker") or "").upper(),
            "quantity": qty,
            "avgCost": avg_cost,
            "investedAmount": invested,
            "dividends": dividends,
            "isEtf": bool(p.get("isEtf")),
            "isSimulated": bool(p.get("isSimulated")),
        }

        if include_prices and get_stock_data and row["ticker"]:
            try:
                quote = get_stock_data(row["ticker"])
                if not isinstance(quote, dict):
                    quote = {}
                raw_price = (
                    quote.get("regular_market_price")
                    or quote.get("last_close")
                    or quote.get("pre_market_price")
                )
                if raw_price is not None:
                    price_f = float(raw_price)
                    market_value = price_f * qty
                    pnl = market_value - invested
                    pnl_pct = (pnl / invested * 100.0) if invested > 0 else None
                    row["price"] = price_f
                    row["marketValue"] = round(market_value, 2)
                    row["unrealizedPnl"] = round(pnl, 2)
                    row["unrealizedPnlPct"] = (
                        round(pnl_pct, 2) if pnl_pct is not None else None
                    )
                    if quote.get("change_pct") is not None:
                        row["dayChangePct"] = quote.get("change_pct")
            except Exception:
                pass

        active.append(row)

    total_invested = sum(x["investedAmount"] for x in active)
    total_market = sum(x.get("marketValue") or 0 for x in active)
    has_prices = any("marketValue" in x for x in active)
    result: dict = {
        "positions": active,
        "count": len(active),
        "totalInvested": round(total_invested, 2),
    }
    if has_prices:
        result["totalMarketValue"] = round(total_market, 2)
        result["totalUnrealizedPnl"] = round(total_market - total_invested, 2)
        if total_invested > 0:
            result["totalUnrealizedPnlPct"] = round(
                (total_market - total_invested) / total_invested * 100.0, 2
            )
    return result
