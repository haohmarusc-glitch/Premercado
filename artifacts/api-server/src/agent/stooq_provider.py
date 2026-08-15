"""
stooq_provider.py — Histórico diário via Stooq como FALLBACK do yfinance.

## Por que Stooq

Custo zero antes de implantar: sem chave, sem cadastro, sem tier pago, sem
limite de requisição que importe no nosso volume (poucas dezenas de tickers, e
só quando o disjuntor do yfinance está aberto — ver `provider_health.py`).
Isso evita gastar um provedor pago (FMP, Alpha Vantage) só para cobrir um
caminho de fallback que na maior parte dos dias nunca é exercitado.

## O que este módulo NÃO promete

Stooq devolve OHLCV diário, "as traded" — não confirmamos que o ajuste de
split/dividendo bate 1:1 com `yfinance(auto_adjust=True)`. Por isso este
módulo é fallback de **continuidade** para indicadores técnicos (RSI, SMA,
tendência, contagem de candles) e para a UI não ficar sem gráfico; não é
substituto para cálculo de P&L ou preço médio, que continuam exigindo a
fonte primária. Todo resultado carrega `source="stooq"` explícito — ver
`market_data_provider.py`, que é quem decide o que fazer com esse rótulo.
Mesmo espírito do playbook do repo: dado de fonte externa merece checagem
cruzada, não confiança cega.

## Formato de saída

DataFrame com colunas `Open, High, Low, Close, Volume` e índice de datas
(sem timezone — diferente do yfinance, que devolve `DatetimeIndex` com tz do
pregão). Nenhum consumidor de indicador técnico do repo depende de tz, só da
ordenação cronológica e dos valores de fechamento; quem depende de tz para
decidir "hoje" usa `brt.py`, não o índice do histórico.
"""
from __future__ import annotations

import io
import sys
from datetime import timedelta

import pandas as pd

try:
    from .http_retry import SESSION
    from .brt import today_brt
except ImportError:  # execução standalone (mesmo padrão dos demais módulos)
    from http_retry import SESSION
    from brt import today_brt

_BASE_URL = "https://stooq.com/q/d/l/"

# period do yfinance -> dias corridos aproximados. Só os períodos que
# hist_cache.py já considera cacheáveis (candle de hoje não domina o
# resultado) fazem sentido como fallback — ver PERIODOS_CACHEAVEIS lá.
_PERIOD_DAYS = {
    "3mo": 100,
    "6mo": 200,
    "1y": 380,
    "2y": 760,
    "5y": 1900,
}


def _stooq_symbol(ticker: str) -> str:
    """NVDA -> nvda.us. Tickers com ponto (ex.: BRK.B) viram traço (brk-b),
    convenção do próprio Stooq para classes de ação."""
    return ticker.strip().lower().replace(".", "-") + ".us"


def fetch_daily_history(ticker: str, period: str = "6mo") -> pd.DataFrame | None:
    """Histórico diário do Stooq no mesmo formato de `yf.Ticker(...).history()`.

    Retorna None em qualquer falha (símbolo não existente no Stooq, rede,
    CSV vazio/malformado) — fail-open, igual ao resto do repo. Quem chama
    decide o que fazer com None (tipicamente: servir cache velho, ou
    devolver erro explícito ao usuário).
    """
    days = _PERIOD_DAYS.get(period, 200)
    symbol = _stooq_symbol(ticker)
    try:
        resp = SESSION.get(_BASE_URL, params={"s": symbol, "i": "d"}, timeout=8)
        resp.raise_for_status()
        text = resp.text.strip()
        # Stooq devolve um corpo de erro em texto plano (não CSV, sem
        # cabeçalho "Date,Open,...") quando o símbolo não existe — checar
        # isso explicitamente evita que pandas.read_csv interprete a
        # mensagem de erro como uma linha de dado válida.
        if not text or not text.startswith("Date,"):
            print(f"[stooq_provider] {ticker}: resposta sem dado ({text[:80]!r})",
                  file=sys.stderr)
            return None

        df = pd.read_csv(io.StringIO(text), parse_dates=["Date"])
        if df.empty:
            return None

        df = df.set_index("Date").sort_index()
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        df = df[keep]

        cutoff = pd.Timestamp(today_brt() - timedelta(days=days))
        df = df[df.index >= cutoff]

        return df if not df.empty else None
    except Exception as ex:
        print(f"[stooq_provider] {ticker}: {ex}", file=sys.stderr)
        return None


def fetch_last_close(ticker: str) -> dict | None:
    """Último fechamento diário — usado por `market_data_provider.get_quote()`
    quando o yfinance (única fonte de cotação intradiária/pré-mercado do
    repo) está indisponível. Sempre marcado como delayed/EOD pelo chamador:
    Stooq não tem pré-mercado nem tick intradiário no plano gratuito, então
    isto NUNCA deve ser apresentado como preço "ao vivo" — fonte alternativa
    entra com rótulo, nunca disfarçada.
    """
    df = fetch_daily_history(ticker, period="3mo")
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    price = float(last["Close"])
    prev_close = float(prev["Close"]) if prev is not None else None
    change = change_pct = None
    if prev_close is not None and prev_close != 0:
        change = round(price - prev_close, 4)
        change_pct = round((price - prev_close) / prev_close * 100, 4)
    return {
        "price": round(price, 4),
        "previousClose": round(prev_close, 4) if prev_close is not None else None,
        "change": change,
        "changePct": change_pct,
        "asOf": df.index[-1].strftime("%Y-%m-%d"),
        "volume": int(last["Volume"]) if pd.notna(last.get("Volume")) else None,
    }


if __name__ == "__main__":
    import json

    symbols = sys.argv[1:] or ["NVDA"]
    out = {s: fetch_last_close(s) for s in symbols}
    print(json.dumps(out, indent=2, ensure_ascii=False))
