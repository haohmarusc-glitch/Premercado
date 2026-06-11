"""
Script standalone para recalcular high/low desde a data de compra
para todas as posições da carteira e persistir via API.

Uso:
    python artifacts/api-server/src/agent/update_high_low.py
"""
import datetime
import os
import sys

import requests
import yfinance as yf

API_URL = os.environ.get("INTERNAL_API_URL", "http://localhost:5000")
API_KEY = os.environ.get("OPERATOR_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


def _period_for_days(days: int) -> str:
    if days <= 5:   return "5d"
    if days <= 30:  return "1mo"
    if days <= 90:  return "3mo"
    if days <= 180: return "6mo"
    if days <= 365: return "1y"
    if days <= 730: return "2y"
    return "5y"


def get_positions() -> list:
    try:
        r = requests.get(f"{API_URL}/api/portfolio", headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Erro ao buscar posições: {e}")
        return []


def update_position(pos_id: int, data: dict) -> bool:
    try:
        r = requests.put(
            f"{API_URL}/api/portfolio/{pos_id}",
            json=data,
            headers=HEADERS,
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def main():
    positions = get_positions()
    if not positions:
        print("Nenhuma posição encontrada.")
        sys.exit(0)

    print(f"Atualizando high/low para {len(positions)} posição(ões)...\n")

    for pos in positions:
        ticker = pos.get("ticker", "")
        pos_id = pos.get("id")
        first_date = pos.get("firstPurchaseDate") or pos.get("first_purchase_date")

        if not ticker or not pos_id:
            continue

        try:
            t = yf.Ticker(ticker)
            if first_date:
                start = datetime.datetime.strptime(first_date[:10], "%Y-%m-%d").date()
                days = (datetime.date.today() - start).days
                period = _period_for_days(max(days, 1))
            else:
                period = "1y"

            hist = t.history(period=period)
            if hist is None or hist.empty:
                print(f"  ⚠️  {ticker}: sem dados históricos")
                continue

            high = float(hist["High"].max())
            low = float(hist["Low"].min())
            high_date = hist["High"].idxmax().strftime("%Y-%m-%d")
            low_date = hist["Low"].idxmin().strftime("%Y-%m-%d")

            ok = update_position(pos_id, {
                "highSincePurchase": high,
                "lowSincePurchase":  low,
                "highDate": high_date,
                "lowDate":  low_date,
            })

            if ok:
                print(f"  ✅ {ticker}: High ${high:.2f} ({high_date})  Low ${low:.2f} ({low_date})")
            else:
                print(f"  ❌ {ticker}: API retornou erro ao persistir")

        except Exception as e:
            print(f"  ❌ {ticker}: {e}")

    print("\nDone!")


if __name__ == "__main__":
    main()
