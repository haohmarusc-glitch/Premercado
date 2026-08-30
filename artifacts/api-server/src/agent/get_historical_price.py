"""Fetch real historical closing prices for a ticker on specific dates.

Input (stdin JSON):  {"ticker": "NVDA", "dates": ["2026-03-20", "2026-05-18"]}
Output (stdout JSON): {"prices": {"2026-03-20": 121.4, "2026-05-18": 134.2}}

For each requested date, returns the close of that day, or the most recent
trading day on/before it (handles weekends/holidays).

## NÃO integrar com market_data_provider (a cadeia de fallback)

Este script alimenta o `purchasePrice` dos lotes da carteira
(`routes/portfolio.ts`: backfill ao inserir, `/backfill-prices`, e a consulta
de preço por data). Ou seja: é a base do preço médio e de todo o P&L.

Dois motivos para ficar de fora, e o segundo é o que decide:

1. A fonte externa devolve preço "as traded", sem ajuste de split/dividendo
   confirmado. Custo de aquisição errado por fator de split não é ruído — é
   um P&L errado.
2. Diferente de um indicador, que é recalculado no ciclo seguinte, o preço de
   compra é **gravado no banco** e nunca mais recalculado da fonte. Um número
   ruim aqui não se corrige sozinho: fica.

Se o yfinance estiver fora, o certo é FALHAR e deixar o usuário informar o
preço à mão (o fluxo já existe, `priceManuallyEdited`) — não preencher com
uma aproximação que ninguém vai auditar depois.
"""
import sys, json, re
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
from agent.security import sanitize_ticker
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


def run(ticker, dates):
    ticker = sanitize_ticker(ticker)
    valid = sorted({d for d in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", str(d))})
    if not valid:
        return {"prices": {}}

    start = (datetime.strptime(valid[0], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (datetime.strptime(valid[-1], "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")

    df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
    if df.empty:
        return {"prices": {}, "error": "Sem dados para o período"}
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].dropna()
    # Normalise index to tz-naive date strings
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()

    prices = {}
    for d in valid:
        target = pd.Timestamp(d)
        on_or_before = close[close.index <= target]
        if len(on_or_before) > 0:
            prices[d] = round(float(on_or_before.iloc[-1]), 2)
    return {"prices": prices}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    result = run(args["ticker"], args.get("dates", []))
    print(json_seguro.dumps(result))
