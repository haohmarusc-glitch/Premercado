"""Dados alternativos "smart money" via provedores externos, opcionais — standalone.

Cada seção só funciona se a env var correspondente estiver configurada. Sem
a chave, a seção volta {"configured": false} em vez de erro -- o resto do
app funciona normalmente.

- Congresso (STOCK Act, câmara + senado): Quiver Quantitative (pago)
  https://api.quiverquant.com — env QUIVER_API_KEY
  (as fontes gratuitas sem chave que existiam, housestockwatcher.com e
  senate-stock-watcher-data, estão fora do ar ou paradas desde 2020 — ver
  investigação registrada no commit que introduziu este arquivo)
- Fluxo de opções não-usual / dark pool: Unusual Whales (pago)
  https://api.unusualwhales.com — env UNUSUAL_WHALES_API_KEY
- Insider trading (Form 4 da SEC, executivos/diretoria da própria empresa —
  diferente do congresso acima): Form4API (tier grátis, 15k req/mês)
  https://api.form4api.com — env FORM4API_KEY

Input (stdin JSON):  {"tickers": ["NVDA", "MU"]}
Output (stdout JSON): {
  "congress": {"configured": bool, "trades": [...], "error"?: str},
  "darkPool": {"configured": bool, "trades": [...], "error"?: str},
  "insiders": {"configured": bool, "trades": [...], "error"?: str}
}
"""
import csv
import datetime
import io
import sys, json, os
import requests
try:
    # Rodando como script standalone (spawn direto do .py, sem -m agent.xxx)
    # -- Python coloca o diretório do próprio script no sys.path.
    from security import sanitize_ticker, friendly_error
except ImportError:
    # Importado como agent.get_alt_data de dentro do processo principal
    # (ex.: tools.py) -- aqui `agent` já é um pacote, precisa de import relativo.
    from .security import sanitize_ticker, friendly_error

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

def insider_trades(tickers: set[str], lookback_days: int = 90) -> dict:
    """Form 4 da SEC (compra/venda de insiders -- CEO, CFO, diretoria da
    própria empresa) via Form4API. Diferente de congress_trades: aqui é
    quem realmente dirige o negócio, não político. Só 1 ticker por chamada
    (o endpoint de export exige um symbol/cik/from bem delimitado); chama
    em loop pra cada ticker pedido, sem quebrar se um deles falhar."""
    api_key = os.environ.get("FORM4API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "FORM4API_KEY não configurada — cadastre-se em form4api.com para ativar (tier grátis, sem cartão).",
        }
    since = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()
    all_trades = []
    errors = []
    for ticker in tickers:
        try:
            r = requests.get(
                "https://api.form4api.com/v1/transactions/export",
                headers={"X-Api-Key": api_key},
                params={"ticker": ticker, "from": since},
                timeout=15,
            )
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                all_trades.append({
                    "ticker": row.get("ticker") or row.get("symbol") or ticker,
                    "insider": row.get("insider_name") or row.get("reporting_owner") or row.get("insider"),
                    "role": row.get("title") or row.get("role") or row.get("officer_title"),
                    "transactionType": row.get("transaction_code") or row.get("transaction_type"),
                    "shares": row.get("shares") or row.get("quantity"),
                    "pricePerShare": row.get("price") or row.get("price_per_share"),
                    "value": row.get("value") or row.get("transaction_value"),
                    "transactionDate": row.get("transaction_date") or row.get("date"),
                    "filedDate": row.get("filed_at") or row.get("filing_date"),
                })
        except Exception as e:
            print(f"[get_alt_data] insider_trades({ticker}): {e}", file=sys.stderr)
            errors.append(f"{ticker}: {friendly_error(e)}")
    result = {"configured": True, "trades": all_trades}
    if errors:
        result["error"] = "; ".join(errors)
    return result

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
        "insiders": insider_trades(clean),
    }, ensure_ascii=False))
