"""Dados alternativos "smart money" via provedores pagos, opcionais — standalone.

Nenhum dos dois provedores abaixo tem tier gratuito com acesso via API (só o
site tem visualização gratuita limitada): cada seção só funciona se a env var
correspondente estiver configurada. Sem a chave, a seção volta
{"configured": false} em vez de erro -- o resto do app funciona normalmente.

- Congresso (STOCK Act, câmara + senado): Quiver Quantitative
  https://api.quiverquant.com — env QUIVER_API_KEY
  (as fontes gratuitas sem chave que existiam, housestockwatcher.com e
  senate-stock-watcher-data, estão fora do ar ou paradas desde 2020 — ver
  investigação registrada no commit que introduziu este arquivo)
- Fluxo de opções não-usual / dark pool: Unusual Whales
  https://api.unusualwhales.com — env UNUSUAL_WHALES_API_KEY

Input (stdin JSON):  {"tickers": ["NVDA", "MU"]}
Output (stdout JSON): {
  "congress": {"configured": bool, "trades": [...], "error"?: str},
  "darkPool": {"configured": bool, "trades": [...], "error"?: str}
}
"""
import sys, json, os
import requests
from security import sanitize_ticker, friendly_error

def congress_trades(tickers: set[str]) -> dict:
    api_key = os.environ.get("QUIVER_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "QUIVER_API_KEY não configurada — cadastre-se em quiverquant.com para ativar.",
        }
    try:
        r = requests.get(
            "https://api.quiverquant.com/beta/live/congresstrading",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json()
        trades = [
            {
                "ticker": row.get("Ticker"),
                "representative": row.get("Representative") or row.get("Senator"),
                "chamber": "senate" if row.get("Senator") else "house",
                "transaction": row.get("Transaction"),
                "range": row.get("Range") or row.get("Amount"),
                "transactionDate": row.get("TransactionDate"),
                "filedDate": row.get("Filed") or row.get("ReportDate"),
            }
            for row in rows
            if isinstance(row, dict) and str(row.get("Ticker", "")).upper() in tickers
        ]
        return {"configured": True, "trades": trades}
    except Exception as e:
        print(f"[get_alt_data] congress_trades: {e}", file=sys.stderr)
        return {"configured": True, "error": friendly_error(e)}

def dark_pool_flow(tickers: set[str]) -> dict:
    api_key = os.environ.get("UNUSUAL_WHALES_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "UNUSUAL_WHALES_API_KEY não configurada — cadastre-se em unusualwhales.com para ativar.",
        }
    try:
        r = requests.get(
            "https://api.unusualwhales.com/api/darkpool/recent",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        rows = body.get("data", body) if isinstance(body, dict) else body
        trades = [
            {
                "ticker": row.get("ticker"),
                "price": row.get("price"),
                "size": row.get("size"),
                "premium": row.get("premium"),
                "executedAt": row.get("executed_at") or row.get("executedAt"),
            }
            for row in (rows or [])
            if isinstance(row, dict) and str(row.get("ticker", "")).upper() in tickers
        ]
        return {"configured": True, "trades": trades}
    except Exception as e:
        print(f"[get_alt_data] dark_pool_flow: {e}", file=sys.stderr)
        return {"configured": True, "error": friendly_error(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    raw_tickers = args.get("tickers", [])
    clean: set[str] = set()
    for t in raw_tickers:
        try:
            clean.add(sanitize_ticker(t))
        except ValueError:
            continue

    print(json.dumps({
        "congress": congress_trades(clean),
        "darkPool": dark_pool_flow(clean),
    }, ensure_ascii=False))
