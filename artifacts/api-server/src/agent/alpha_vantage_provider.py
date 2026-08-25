"""
alpha_vantage_provider.py — Histórico diário via Alpha Vantage como FALLBACK
externo do yfinance. Substitui o `stooq_provider.py`, que não sobreviveu ao
primeiro contato com a rede real.

## Por que não o Stooq

O plano original escolheu o Stooq por ser gratuito e sem chave. Medido do IP do
VPS pelo `provider_preflight.py`, ele não serve como fonte programática:

    python-requests  -> 404
    User-Agent de navegador -> 200, mas o corpo é uma página de desafio
                               anti-bot (proof-of-work em JavaScript + POST
                               para /__verify)

Fingir navegador transformaria um 404 limpo num 200 cujo corpo é HTML, e
resolver o desafio seria contornar um controle de acesso explícito do site.
Nenhum dos dois. O Stooq saiu.

## O que a Alpha Vantage NÃO promete aqui

`TIME_SERIES_DAILY` devolve preço "as traded" — a versão ajustada por
split/dividendo (`TIME_SERIES_DAILY_ADJUSTED`) é paga, mesma família do
`ANALYTICS_FIXED_WINDOW` que já nos deu 403. Vale o mesmo limite do Stooq:
fallback de **continuidade** para indicador técnico e para a tela não ficar
sem gráfico; nunca fonte de verdade para P&L, preço médio ou qualquer número
que vire dinheiro.

## Cota compartilhada — o motivo do teto

A chave é a MESMA do feed de notícias (`news_sources.py`, `NEWS_SENTIMENT`).
Sem teto, um dia ruim — yfinance fora e dezenas de tickers em vários checkers
— esgotaria a cota diária e derrubaria as notícias junto, trocando uma falha
parcial por duas. Por isso toda chamada passa por
`provider_health.consumir_orcamento_diario`.

## 200 OK não significa dado

A Alpha Vantage responde 200 com um JSON de aviso quando a chave é inválida,
a cota estourou ou o endpoint é premium (`Note`, `Information`,
`Error Message`) — armadilha que `news_sources.py` já documenta. Aqui isso é
detectado explicitamente e vira `None`, nunca um DataFrame vazio disfarçado
de resposta boa.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pandas as pd

try:
    from .http_retry import SESSION
    from .brt import today_brt
    from . import provider_health
except ImportError:  # execução standalone (mesmo padrão dos demais módulos)
    from http_retry import SESSION
    from brt import today_brt
    import provider_health

_BASE_URL = "https://www.alphavantage.co/query"

# Teto conservador: o fallback só dispara com o disjuntor do yfinance aberto,
# e mesmo aí precisa sobrar cota para o feed de notícias do dia.
_ORCAMENTO_DIARIO = int(os.environ.get("AGENT_ALPHAVANTAGE_MAX_DIA", "15"))

# period do yfinance -> dias corridos aproximados. Mesmos períodos que
# hist_cache.py considera cacheáveis.
_PERIOD_DAYS = {
    "3mo": 100,
    "6mo": 200,
    "1y": 380,
    "2y": 760,
    "5y": 1900,
}

# "compact" devolve 100 pregões numa resposta pequena; "full" devolve 20+ anos.
# As duas custam UMA chamada de cota, então o critério é só tamanho de payload.
_LIMITE_COMPACT = 100

_COLUNAS = {
    "1. open": "Open",
    "2. high": "High",
    "3. low": "Low",
    "4. close": "Close",
    "5. volume": "Volume",
}


def _api_key() -> str:
    return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()


def censurar_chave(texto) -> str:
    """Tira a chave de qualquer mensagem antes dela virar log.

    Não é paranoia genérica: quando a cota estoura, a Alpha Vantage responde
    com um aviso que ECOA A CHAVE em texto claro -- "We have detected your API
    key as XXXX and our standard API rate limit is 25 requests per day". Os
    três pontos do repo que imprimem esse aviso estavam, portanto, escrevendo
    a credencial no stderr do container, de onde ela vai para o log do Docker,
    para o terminal de quem roda o comando e para qualquer lugar em que essa
    saída for colada. Visto na rodada de 25/08/2026.

    Substituição literal pelo valor da chave, não regex de "coisa que parece
    chave": o que precisa sumir é este segredo, e casar por formato erraria
    para mais (censurando ticker) e para menos (chave em outro formato)."""
    texto = str(texto)
    chave = _api_key()
    if chave and len(chave) >= 8:
        texto = texto.replace(chave, "***")
    return texto


def fetch_daily_history(ticker: str, period: str = "6mo") -> pd.DataFrame | None:
    """Histórico diário no mesmo formato de `yf.Ticker(...).history()`.

    Retorna None em qualquer falha (sem chave, cota do dia esgotada, aviso da
    API, rede, JSON inesperado) — fail-open, igual ao resto do repo. Quem
    chama decide o que fazer com None.
    """
    chave = _api_key()
    if not chave:
        print("[alpha_vantage_provider] ALPHAVANTAGE_API_KEY ausente", file=sys.stderr)
        return None

    dias = _PERIOD_DAYS.get(period, 200)

    if not provider_health.consumir_orcamento_diario("alphavantage", _ORCAMENTO_DIARIO):
        print(
            f"[alpha_vantage_provider] {ticker}: cota diária de fallback "
            f"({_ORCAMENTO_DIARIO}) esgotada — preservando a cota do feed de notícias",
            file=sys.stderr,
        )
        return None

    try:
        resp = SESSION.get(
            _BASE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker.strip().upper(),
                "outputsize": "compact" if dias <= _LIMITE_COMPACT else "full",
                "apikey": chave,
            },
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()

        if not isinstance(body, dict):
            return None

        serie = body.get("Time Series (Daily)")
        if not isinstance(serie, dict) or not serie:
            # 200 OK sem série = aviso da API (cota, chave, endpoint premium).
            # Nunca tratar como "sem dado hoje" em silêncio: é a diferença
            # entre "o mercado não abriu" e "estamos cegos".
            motivo = body.get("Note") or body.get("Information") or body.get("Error Message")
            print(f"[alpha_vantage_provider] {ticker}: sem série "
                  f"({censurar_chave(motivo)[:160]!r})", file=sys.stderr)
            return None

        df = pd.DataFrame.from_dict(serie, orient="index")
        df = df.rename(columns=_COLUNAS)
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        if "Close" not in keep:
            return None
        df = df[keep].apply(pd.to_numeric, errors="coerce")
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()].sort_index()

        cutoff = pd.Timestamp(today_brt() - timedelta(days=dias))
        df = df[df.index >= cutoff]
        df = df.dropna(subset=["Close"])

        return df if not df.empty else None
    except Exception as ex:
        print(f"[alpha_vantage_provider] {ticker}: {ex}", file=sys.stderr)
        return None


def fetch_last_close(ticker: str) -> dict | None:
    """Último fechamento diário — usado por `market_data_provider.get_quote()`
    quando o yfinance (única fonte intradiária/pré-mercado do repo) está
    indisponível. Sempre marcado como atrasado pelo chamador: isto é
    fechamento do dia anterior, e NUNCA deve ser apresentado como preço ao
    vivo — fonte alternativa entra com rótulo, nunca disfarçada.
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
