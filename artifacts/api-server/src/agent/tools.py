"""t:
Ferramentas disponíveis para o agente de pré-mercado.
"""

import datetime
import json
import os
import re
import sys

import requests
import yfinance as yf

from . import get_alt_data as _alt_data
from . import market_alerts as _ma
from . import sector_contagion as _sc
from .cache import cached
from .security import sanitize_for_llm, sanitize_ticker, sanitize_url

_PERIOD_RE = re.compile(r"^\s*(\d+)\s*(d|mo|y)\s*$", re.IGNORECASE)


def _history_for_period(t: "yf.Ticker", period: str):
    """Busca histórico para qualquer período (ex.: '5mo', '4mo', '18mo'),
    não só o conjunto fixo que o yfinance aceita nativamente (1mo, 3mo, 6mo,
    1y, 2y, 5y, 10y, ytd, max). Converte 'Nd'/'Nmo'/'Ny' em start/end; valores
    fora desse formato (ex.: 'ytd', 'max') são passados direto ao yfinance.
    """
    m = _PERIOD_RE.match(period or "")
    if not m:
        return t.history(period=period)
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n if unit == "d" else n * 30 if unit == "mo" else n * 365
    start = datetime.date.today() - datetime.timedelta(days=days)
    return t.history(start=start.isoformat())


# ── Cotações ──────────────────────────────────────────────────────────────────


@cached("stock_data:{0}", ttl=120)
def get_stock_data(ticker: str) -> dict:
    """Retorna dados de cotação e pré-mercado do ticker."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info

        price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)
        volume = getattr(fi, "last_volume", None)
        year_high = getattr(fi, "year_high", None)
        year_low = getattr(fi, "year_low", None)
        currency = getattr(fi, "currency", "USD")

        change_pct = None
        if price is not None and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 4)

        # pre_market via info (best-effort; fast_info doesn't expose it)
        try:
            info = t.info or {}
            pre_market = info.get("preMarketPrice")
            avg_volume = info.get("averageVolume")
        except Exception:
            info = {}
            pre_market = None
            avg_volume = None

        return {
            "ticker": ticker,
            "last_close": round(price, 4) if price is not None else None,
            "previous_close": round(prev_close, 4) if prev_close is not None else None,
            "pre_market_price": pre_market,
            "regular_market_price": round(price, 4) if price is not None else None,
            "change_pct": change_pct,
            "volume": int(volume) if volume is not None else None,
            "avg_volume": avg_volume,
            "52w_high": round(year_high, 4) if year_high is not None else None,
            "52w_low": round(year_low, 4) if year_low is not None else None,
            "currency": currency,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Notícias ──────────────────────────────────────────────────────────────────


def _parse_news_items(news: list, max_items: int) -> list[dict]:
    """Normaliza a lista bruta de `Ticker.news` do yfinance pro formato
    enxuto usado pelo agente (resumo truncado pra economizar tokens de
    input). Compartilhado por _get_news_for_ticker e _get_news_for_macro_proxy."""
    result = []
    for item in news[:max_items]:
        content = item.get("content", {})
        summary = content.get("summary", item.get("summary", "")) or ""
        result.append(
            {
                "title": sanitize_for_llm(content.get("title", item.get("title", ""))),
                "published": content.get(
                    "pubDate", item.get("providerPublishTime", "")
                ),
                # Truncado: o modelo precisa do gist, não do texto completo da notícia.
                "summary": sanitize_for_llm(summary[:280] + ("..." if len(summary) > 280 else "")),
                "source": content.get("provider", {}).get("displayName", "")
                if isinstance(content.get("provider"), dict)
                else "",
                # url removida do payload — não é usada na análise e só consome tokens de input.
            }
        )
    return result


@cached("news:{0}:{1}", ttl=600)
def _get_news_for_ticker(ticker: str, max_items: int = 6) -> list[dict]:
    """Manchetes recentes de UM ticker via yfinance (resumo truncado para economizar
    tokens). Cacheada por ticker — chamada internamente por get_news, que agrupa vários
    tickers numa única ferramenta sem perder o cache individual de cada um."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return [{"error": str(e)}]
    try:
        t = yf.Ticker(ticker)
        return _parse_news_items(t.news or [], max_items)
    except Exception as e:
        return [{"error": str(e)}]


def get_news(tickers: list[str], max_items: int = 6) -> dict[str, list[dict]]:
    """Retorna as manchetes mais recentes de UM OU MAIS tickers numa única chamada.

    Sempre passe TODOS os tickers relevantes juntos nesta única chamada (nunca
    um por vez em turnos separados) — o retorno já vem no formato
    {ticker: [manchetes...]}, pronto para ser usado direto como
    headlines_by_ticker em check_market_alerts.
    """
    if isinstance(tickers, str):
        tickers = [tickers]  # tolerância a chamada acidental com string única
    return {ticker: _get_news_for_ticker(ticker, max_items) for ticker in tickers}


# Proxies de mercado amplo cujas manchetes do yfinance naturalmente cobrem
# falas/decisões de chefes de estado (tarifas, sanções, política econômica),
# guerra, petróleo e controle de exportação de semicondutores -- sem precisar
# de API paga de rede social (X/Twitter exige plano pago desde 2023 pra
# busca; ver decisão do usuário em 18/07). Cada entrada usa o MESMO
# mecanismo já validado em produção (Ticker.news) que get_news, só que
# apontado pra um índice/futuro/ETF em vez de uma ação específica.
_MACRO_NEWS_PROXIES = {
    "^GSPC": "mercado_amplo_eua",  # S&P 500 -- tarifas, Fed, geopolítica geral
    "^NDX": "big_techs",  # Nasdaq-100 -- antitrust/regulação de Big Techs, IA
    "CL=F": "petroleo_wti",  # petróleo WTI -- guerra, OPEC, sanções
    "SOXX": "semicondutores",  # ETF de semicondutores -- export controls China/Taiwan
}


@cached("macro_news:{0}:{1}", ttl=1800)
def _get_news_for_macro_proxy(proxy_ticker: str, max_items: int = 6) -> list[dict]:
    """Mesma lógica de _get_news_for_ticker, mas pra um índice/futuro/ETF
    usado como proxy de tema macro (não uma ação da carteira). Cache mais
    longo (30min) que notícia de ação (10min) — esse tipo de manchete muda
    com menos frequência ao longo do dia."""
    try:
        t = yf.Ticker(proxy_ticker)
        return _parse_news_items(t.news or [], max_items)
    except Exception as e:
        return [{"error": str(e)}]


def get_geopolitical_news(max_items: int = 6) -> dict[str, list[dict]]:
    """Retorna manchetes recentes sobre temas macro/geopolíticos que
    impactam a bolsa: falas e decisões de chefes de estado (EUA e outros
    países) sobre tarifas/comércio, guerra, preço do petróleo, Big Techs
    (antitrust/regulação/IA) e controle de exportação de semicondutores
    (China/Taiwan). Não precisa de ticker — chame isso UMA vez por
    execução, já cobre todos os temas de uma vez. Complementa get_news (que
    é por ativo específico da carteira)."""
    return {
        label: _get_news_for_macro_proxy(proxy_ticker, max_items)
        for proxy_ticker, label in _MACRO_NEWS_PROXIES.items()
    }


# ── SEC EDGAR ─────────────────────────────────────────────────────────────────

EDGAR_HEADERS = {
    "User-Agent": "PremarketAgent contact@example.com",
    "Accept": "application/json",
}

TICKER_TO_CIK = {
    # Originais
    "MU": "0000723125",
    "SMCI": "0001375365",
    "NVDA": "0001045810",
    "INTC": "0000050863",
    "GOOGL": "0001652044",
    "ARM": "0001973239",
    "TSLA": "0001318605",
    # Memória/Armazenamento
    "WDC": "0000106040",
    # Interconexão/Servidores
    "ANET": "0001313925",
    # Fundição/Equipamentos
    "TSM": "0001046179",
    "ASML": "0000937966",
    # Saúde (EUA)
    "LLY": "0000059478",
    "UNH": "0000731766",
    "JNJ": "0000200406",
    "ABBV": "0001551152",
    "MRK": "0000310158",
    "PFE": "0000078003",
    # IPO 10/jul/2026 na Nasdaq (ADR); foreign private issuer, arquiva 20-F/F-6
    "SKHY": "0002120882",
}


@cached("edgar:{0}:{1}:{2}", ttl=1800)
def search_edgar_filings(
    ticker: str, form_type: str = "8-K", count: int = 5
) -> list[dict]:
    """Busca filings recentes na SEC EDGAR para o ticker."""
    cik = TICKER_TO_CIK.get(ticker.upper())
    if not cik:
        return [{"error": f"CIK desconhecido para {ticker}"}]
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        descriptions = filings.get("primaryDocument", [])
        results = []
        for i, (form, date, acc, doc) in enumerate(
            zip(forms, dates, accessions, descriptions)
        ):
            if form_type and form != form_type:
                continue
            acc_clean = acc.replace("-", "")
            doc_name = doc.split("/")[
                -1
            ]  # strip XSLT viewer prefix (ex: xslF345X06/) p/ XML bruto
            results.append(
                {
                    "form": form,
                    "date": date,
                    "accession": acc,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc_name}",
                }
            )
            if len(results) >= count:
                break
        return results or [
            {"info": f"Nenhum filing {form_type} recente encontrado para {ticker}"}
        ]
    except Exception as e:
        return [{"error": str(e)}]


@cached("filing:{0}:{1}", ttl=86400)
def read_filing(url: str, max_chars: int = 4000) -> str:
    """Lê o conteúdo de um filing da SEC (truncado). Cacheado por 24h — um
    filing já publicado não muda."""
    try:
        url = sanitize_url(url)
    except ValueError as e:
        return f"[erro ao ler filing: {e}]"
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text
        import re

        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = sanitize_for_llm(text)
        return text[:max_chars] + (" [TRUNCADO]" if len(text) > max_chars else "")
    except Exception as e:
        return f"[erro ao ler filing: {e}]"


# ── Autenticação interna ───────────────────────────────────────────────────────


def _internal_headers() -> dict:
    """Retorna os headers de autenticação para chamadas internas à API."""
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


# ── Memória / observações ─────────────────────────────────────────────────────


def save_observation(
    ticker: str, summary: str, sentiment: str, price: float | None = None
) -> dict:
    """
    Salva observação do dia via API interna.
    Retorna o resultado da gravação.
    """
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"saved": False, "error": str(e)}
    try:
        today = datetime.date.today().isoformat()
        payload = {
            "ticker": ticker,
            "date": today,
            "summary": summary,
            "sentiment": sentiment.lower(),
            "priceAtObservation": price,
        }
        r = requests.post(
            f"{_api_url()}/api/observations/internal",
            json=payload,
            headers=_internal_headers(),
            timeout=10,
        )
        r.raise_for_status()
        return {"saved": True, "ticker": ticker, "sentiment": sentiment}
    except Exception as e:
        return {"saved": False, "error": str(e)}


# ── Gerenciamento de alertas ──────────────────────────────────────────────────


def list_alerts(symbol: str | None = None) -> list[dict]:
    """
    Lista os alertas de preço ativos no sistema.
    Filtra por símbolo se informado.
    """
    try:
        r = requests.get(
            f"{_api_url()}/api/alerts",
            headers=_internal_headers(),
            timeout=10,
        )
        r.raise_for_status()
        alerts = r.json()
        if symbol:
            alerts = [a for a in alerts if a["symbol"].upper() == symbol.upper()]
        return [
            {
                "id": a["id"],
                "symbol": a["symbol"],
                "condition": a["condition"],
                "thresholdPct": a["thresholdPct"],
                "enabled": a["enabled"],
                "lastTriggeredAt": a.get("lastTriggeredAt"),
            }
            for a in alerts
        ]
    except Exception as e:
        return [{"error": str(e)}]


def create_alert(
    symbol: str, condition: str, threshold_pct: float, reason: str
) -> dict:
    """
    Cria um novo alerta de preço.
    condition: 'above' ou 'below'
    threshold_pct: variação percentual relativa ao fechamento anterior (ex: -8.0 para queda de 8%).
        Calibre pelo atr_pct do ativo (get_technical_indicators), não um valor fixo igual pra todos.
    reason: motivo técnico/fundamentalista que justifica o alerta
    """
    try:
        payload = {
            "symbol": symbol.upper(),
            "condition": condition,
            "thresholdPct": float(threshold_pct),
        }
        r = requests.post(
            f"{_api_url()}/api/alerts",
            json=payload,
            headers=_internal_headers(),
            timeout=10,
        )
        r.raise_for_status()
        created = r.json()
        return {
            "created": True,
            "id": created["id"],
            "symbol": created["symbol"],
            "condition": created["condition"],
            "thresholdPct": created["thresholdPct"],
            "reason": reason,
        }
    except Exception as e:
        return {"created": False, "error": str(e)}


def delete_alert(alert_id: int, reason: str) -> dict:
    """
    Remove um alerta de preço que não é mais relevante.
    Use quando um nível técnico foi superado ou o contexto mudou.
    reason: motivo pelo qual o alerta foi removido
    """
    try:
        r = requests.delete(
            f"{_api_url()}/api/alerts/{alert_id}",
            headers=_internal_headers(),
            timeout=10,
        )
        if r.status_code == 204:
            return {"deleted": True, "id": alert_id, "reason": reason}
        return {"deleted": False, "id": alert_id, "status": r.status_code}
    except Exception as e:
        return {"deleted": False, "error": str(e)}


# ── Opções ────────────────────────────────────────────────────────────────────


@cached("options:{0}:{1}", ttl=300)
def get_options_data(ticker: str, expiry: str | None = None) -> dict:
    """Retorna put/call ratio, IV ATM e as opções mais negociadas do ticker."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {"ticker": ticker, "error": "Sem dados de opções disponíveis"}

        exp = expiry if expiry in expirations else expirations[0]
        chain = t.option_chain(exp)
        calls = chain.calls
        puts = chain.puts

        total_call_vol = int(calls["volume"].sum()) if not calls.empty else 0
        total_put_vol = int(puts["volume"].sum()) if not puts.empty else 0
        pc_ratio = (
            round(total_put_vol / total_call_vol, 3) if total_call_vol > 0 else None
        )

        def _top(df, n=5):
            cols = [
                "strike",
                "lastPrice",
                "volume",
                "openInterest",
                "impliedVolatility",
            ]
            return (
                (df.nlargest(n, "volume")[cols].fillna(0).round(4).to_dict("records"))
                if not df.empty
                else []
            )

        spot = getattr(t.fast_info, "last_price", None)
        atm_iv = None
        if spot is not None and not calls.empty:
            near = calls.iloc[(calls["strike"] - spot).abs().argsort()[:3]]
            atm_iv = round(float(near["impliedVolatility"].mean()) * 100, 2)

        return {
            "ticker": ticker,
            "expiry_used": exp,
            "next_expirations": list(expirations[:5]),
            "put_call_ratio": pc_ratio,
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "atm_iv_pct": atm_iv,
            "top_calls_by_volume": _top(calls),
            "top_puts_by_volume": _top(puts),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Indicadores técnicos ──────────────────────────────────────────────────────


@cached("technicals:{0}:{1}", ttl=300)
def get_technical_indicators(ticker: str, period: str = "6mo") -> dict:
    """Calcula RSI-14, MACD, Bollinger Bands, EMA 8/21, SMA 50/200 e ATR-14.

    atr_14/atr_pct = volatilidade real do ativo (Average True Range, em $ e
    % do preço). Use isso para calibrar o threshold_pct de create_alert em
    vez de um percentual fixo: ativos como ARM/SMCI têm atr_pct bem mais alto
    que GOOGL/big techs estáveis, então o mesmo movimento de preço tem
    significância estatística muito diferente. Regra prática: threshold_pct
    ≈ atr_pct * 1.5 (alerta só dispara em movimento acima do "ruído" normal
    do ativo, não em qualquer variação do dia a dia).

    rsi_signal já vem calibrado pelas bandas de rsi_oversold_threshold/
    rsi_overbought_threshold (20/80 em ativos de ATR alto, 25/75 nos demais)
    — não aplique 30/70 fixo por cima disso.

    ema8/ema21/ema_trend = leitura de curtíssimo prazo (timing de entrada,
    swing/day trade). sma50/sma200/macro_trend_filter servem só de filtro de
    tendência maior (ex.: só considerar long se macro_trend_filter="alta").
    """
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        import pandas as pd

        t = yf.Ticker(ticker)
        hist = _history_for_period(t, period)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker, "error": "Dados insuficientes"}

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])

        # ATR 14 (Average True Range) — volatilidade real em $ e % do ativo,
        # pra calibrar limiares de alerta por ticker em vez de usar o mesmo
        # % fixo pra todo mundo (5% é ruído pra ARM e evento raro pra GOOGL).
        prev_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr14 = true_range.rolling(14).mean().iloc[-1]
        atr_14 = round(float(atr14), 2) if not pd.isna(atr14) else None
        atr_pct = round(float(atr14) / price * 100, 2) if not pd.isna(atr14) and price else None

        # Bandas de RSI calibradas pela volatilidade real do ativo (ATR%) em
        # vez do 30/70 padrão pra todo mundo: NVDA/SMCI/ARM (ATR% alto) ficam
        # "esticados" por muito mais tempo que big techs estáveis (GOOGL/MSFT)
        # antes de reverter de verdade — 30/70 fixo gera sinal de reversão
        # prematuro nos ativos voláteis.
        if atr_pct is None:
            rsi_oversold, rsi_overbought = 30.0, 70.0
        elif atr_pct >= 6.0:
            rsi_oversold, rsi_overbought = 20.0, 80.0
        else:
            rsi_oversold, rsi_overbought = 25.0, 75.0

        # RSI 14 — quando os 14 dias não têm nenhuma queda, avg_loss = 0 e a
        # divisão vira NaN; json.dumps serializa isso como o token `NaN`, que
        # não é JSON válido em quem for reparsear estritamente. RSI=100 é o
        # valor tecnicamente correto pra essa condição (só alta).
        delta = close.diff()
        avg_gain = delta.clip(lower=0).rolling(14).mean()
        avg_loss = (-delta.clip(upper=0)).rolling(14).mean()
        avg_gain_last = float(avg_gain.iloc[-1])
        avg_loss_last = float(avg_loss.iloc[-1])
        if avg_loss_last == 0:
            rsi = 100.0 if avg_gain_last > 0 else 50.0
        else:
            rsi = round(100 - 100 / (1 + avg_gain_last / avg_loss_last), 2)

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal_line

        # EMA 8/21 — leitura de curtíssimo prazo (timing de entrada/swing).
        # SMA 50/200 abaixo seguem servindo só de filtro de tendência maior.
        ema8 = float(close.ewm(span=8).mean().iloc[-1])
        ema21 = float(close.ewm(span=21).mean().iloc[-1])

        # Bollinger Bands (20, 2)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = float((sma20 + 2 * std20).iloc[-1])
        bb_middle = float(sma20.iloc[-1])
        bb_lower = float((sma20 - 2 * std20).iloc[-1])
        pct_b = (
            round((price - bb_lower) / (bb_upper - bb_lower) * 100, 1)
            if (bb_upper - bb_lower) != 0
            else None
        )

        def _safe(series):
            val = series.iloc[-1]
            return round(float(val), 2) if not pd.isna(val) else None

        sma50 = _safe(close.rolling(50).mean())
        sma200 = _safe(close.rolling(200).mean()) if len(close) >= 200 else None

        def _pct_diff(a, b):
            return round((a - b) / b * 100, 2) if a and b else None

        vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
        vol_5d_avg = float(volume.iloc[-5:].mean())
        vol_ratio = round(vol_5d_avg / vol_avg20, 2) if vol_avg20 > 0 else None

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "rsi_14": rsi,
            "rsi_signal": "sobrecomprado"
            if rsi > rsi_overbought
            else "sobrevendido"
            if rsi < rsi_oversold
            else "neutro",
            "rsi_oversold_threshold": rsi_oversold,
            "rsi_overbought_threshold": rsi_overbought,
            "macd": {
                "macd_line": round(float(macd_line.iloc[-1]), 4),
                "signal_line": round(float(signal_line.iloc[-1]), 4),
                "histogram": round(float(histogram.iloc[-1]), 4),
                "trend": "bullish" if float(histogram.iloc[-1]) > 0 else "bearish",
            },
            "bollinger": {
                "upper": round(bb_upper, 2),
                "middle": round(bb_middle, 2),
                "lower": round(bb_lower, 2),
                "pct_b": pct_b,
            },
            "ema8": round(ema8, 2),
            "ema21": round(ema21, 2),
            "ema_trend": "bullish" if ema8 > ema21 else "bearish",
            "sma50": sma50,
            "sma200": sma200,
            "pct_above_sma50": _pct_diff(price, sma50),
            "pct_above_sma200": _pct_diff(price, sma200),
            "macro_trend_filter": (
                ("alta" if price > sma200 else "baixa") if sma200 else None
            ),
            "volume_ratio_5d_vs_20d": vol_ratio,
            "atr_14": atr_14,
            "atr_pct": atr_pct,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def detect_candle_patterns(ticker: str, period: str = "1mo", lookback: int = 5) -> dict:
    """
    Detecta padrões clássicos de candlestick nos últimos `lookback` candles
    diários (dentro da janela `period`, ex.: '1mo', '3mo', '5mo', '6mo', '1y', '2y'):
    Doji, Martelo/Enforcado, Estrela Cadente/Martelo Invertido, Engolfo de
    Alta/Baixa e Estrela da Manhã/Noite. Para analisar o período inteiro
    (ex.: o ano todo com period='1y'), passe um `lookback` alto o bastante
    para cobrir todos os candles do período.

    Regras heurísticas padrão de OHLC (sem TA-Lib): tamanho do corpo relativo
    ao range do candle, tamanho dos pavios, e contexto de tendência dos ~4
    candles anteriores para distinguir padrões de mesma forma mas sinal
    oposto (ex.: Martelo em fundo de baixa vs. Enforcado em topo de alta).
    Use junto com get_news do mesmo ticker para cruzar reversão técnica com
    o motivo por trás dela (ex.: engolfo de baixa no mesmo dia de notícia
    negativa de guidance é sinal mais forte que qualquer um isolado).
    """
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        hist = _history_for_period(t, period)
        if hist.empty or len(hist) < 5:
            return {"ticker": ticker, "error": "Dados insuficientes"}

        o, h, l, c = hist["Open"], hist["High"], hist["Low"], hist["Close"]
        found = _ma.detect_candle_patterns_in_hist(hist, lookback=lookback)

        return {
            "ticker": ticker,
            "period": period,
            "patterns": found,
            "latest_candle": {
                "date": hist.index[-1].strftime("%Y-%m-%d"),
                "o": round(float(o.iloc[-1]), 2),
                "h": round(float(h.iloc[-1]), 2),
                "l": round(float(l.iloc[-1]), 2),
                "c": round(float(c.iloc[-1]), 2),
            },
            "note": None if found else f"Nenhum padrão clássico detectado nos últimos {lookback} candles.",
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Performance de ETFs de setor ──────────────────────────────────────────────

_SECTOR_ETFS = {
    "SMH": "VanEck Semiconductor ETF",
    "SOXX": "iShares Semiconductor ETF",
    "XLK": "Technology Select Sector SPDR",
    "QQQ": "Invesco QQQ (Nasdaq 100)",
    "SPY": "SPDR S&P 500 ETF",
    "IWM": "iShares Russell 2000 ETF",
    "XLF": "Financial Select Sector SPDR",
    "XLV": "Health Care Select Sector SPDR",
    "IBB": "iShares Biotechnology ETF",
}


@cached("sector_perf:{0}", ttl=180)
def get_sector_performance(etfs: list[str] | None = None) -> list[dict]:
    """Retorna a performance e pré-mercado dos principais ETFs de setor."""
    symbols = etfs or list(_SECTOR_ETFS.keys())
    results = []
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            fi = t.fast_info
            price = getattr(fi, "last_price", None)
            prev_close = getattr(fi, "previous_close", None)
            pre = info.get("preMarketPrice")

            def _chg(p, c):
                if p and c and c != 0:
                    return round((p - c) / c * 100, 2)
                return None

            results.append(
                {
                    "symbol": sym,
                    "name": _SECTOR_ETFS.get(sym, sym),
                    "price": round(price, 2) if price else None,
                    "pre_market_price": round(pre, 2) if pre else None,
                    "change_pct": _chg(price, prev_close),
                    "pre_market_change_pct": _chg(pre, prev_close),
                }
            )
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return results


# ── Short interest ────────────────────────────────────────────────────────────


@cached("short_interest:{0}", ttl=3600)
def get_short_interest(ticker: str) -> dict:
    """Retorna short float %, days-to-cover e variação em relação ao mês anterior."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        short_pct = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        shares_short = info.get("sharesShort")
        shares_short_prior = info.get("sharesShortPriorMonth")
        float_shares = info.get("floatShares")

        short_change_pct = None
        if shares_short and shares_short_prior and shares_short_prior > 0:
            short_change_pct = round(
                (shares_short - shares_short_prior) / shares_short_prior * 100, 2
            )

        squeeze_risk = (
            "alto"
            if short_pct and short_pct > 0.20
            else "moderado"
            if short_pct and short_pct > 0.10
            else "baixo"
        )

        return {
            "ticker": ticker,
            "short_pct_of_float": round(short_pct * 100, 2) if short_pct else None,
            "days_to_cover": round(short_ratio, 2) if short_ratio else None,
            "shares_short": shares_short,
            "shares_short_prior_month": shares_short_prior,
            "float_shares": float_shares,
            "short_change_vs_prior_month_pct": short_change_pct,
            "squeeze_risk": squeeze_risk,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Setup de squeeze + reversão técnica ─────────────────────────────────────────
# Detector combinado: risco de short squeeze (short interest + days-to-cover)
# só importa de verdade se houver confirmação técnica de que o preço está
# revertendo de um fundo, não em queda livre. Não é sinal de compra — é um
# "vale a pena olhar de perto" que o agente pode citar no relatório com o
# contexto completo (a decisão continua sendo do usuário).

_SQUEEZE_SHORT_PCT_DANGER = 20.0  # % do float — mesmo limiar de get_short_interest "alto"
_SQUEEZE_DTC_DANGER = 5.0  # dias pra cobrir
_SQUEEZE_BORROW_FEE_DANGER = 30.0  # % ao ano
_SQUEEZE_SHORT_VOLUME_DANGER = 50.0  # % do volume do pregão vendido a descoberto (FINRA Reg SHO)
_SUPPORT_TOUCH_PCT = 5.0  # "tocou o suporte" = dentro de 5% da mínima rolante
_BOTTOM_VOLUME_MULT = 1.5  # 150% da média de 20 dias, pedido explícito do usuário
_BREAKOUT_LOOKBACK_DAYS = 20
_BREAKOUT_VOLUME_MULT = 3.0
_DIVERGENCE_LOOKBACK_DAYS = 40
_IBORROWDESK_HEADERS = {"User-Agent": "Mozilla/5.0"}  # sem UA de navegador, o site devolve 403


def _fetch_borrow_fee(ticker: str) -> tuple[float | None, str]:
    """Taxa de aluguel (borrow fee) anualizada, via iBorrowDesk — espelha
    dado público do próprio IBKR, sem custo e sem precisar de API key
    (github.com/iborrowdesk, iborrowdesk.com/api/ticker/{ticker}). Fail-open:
    qualquer falha de rede/parsing devolve (None, nota explicando o motivo)
    sem derrubar o resto do detector."""
    try:
        r = requests.get(
            f"https://iborrowdesk.com/api/ticker/{ticker}",
            headers=_IBORROWDESK_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        daily = (r.json() or {}).get("daily") or []
        if not daily:
            return None, "iBorrowDesk sem dado de aluguel pra este ticker (pode não ser negociado via IBKR)."
        latest = max(daily, key=lambda d: d.get("date", ""))
        fee = latest.get("fee")
        if fee is None:
            return None, "iBorrowDesk retornou sem o campo de taxa de aluguel."
        return round(float(fee), 2), "Fonte: iBorrowDesk (espelha dado público do IBKR, sem custo)."
    except Exception as e:
        return None, f"iBorrowDesk indisponível no momento ({e})."


def _fetch_dark_pool_activity(ticker: str) -> tuple[dict | None, str]:
    """Prints recentes de dark pool via Unusual Whales (get_alt_data.py já
    tem essa integração, usada hoje só num painel separado da UI -- aqui só
    reaproveita pro squeeze detector). Opcional: sem UNUSUAL_WHALES_API_KEY
    configurada, volta (None, nota explicando) sem quebrar o resto do
    detector -- mesmo padrão fail-open do borrow_fee/iBorrowDesk acima."""
    try:
        result = _alt_data.dark_pool_flow({ticker})
    except Exception as e:
        return None, f"Unusual Whales indisponível no momento ({e})."
    if not result.get("configured"):
        return None, result.get("message", "UNUSUAL_WHALES_API_KEY não configurada.")
    if result.get("error"):
        return None, f"Unusual Whales indisponível no momento ({result['error']})."
    trades = result.get("trades") or []
    if not trades:
        return None, "Sem prints de dark pool recentes pra este ticker (Unusual Whales)."
    total_premium = sum(float(t["premium"]) for t in trades if t.get("premium") is not None)
    return {
        "trade_count": len(trades),
        "total_premium": round(total_premium, 2),
    }, "Fonte: Unusual Whales (prints de dark pool recentes)."


def _fetch_short_volume_ratio(ticker: str) -> tuple[float | None, str]:
    """% do volume do pregão vendido a descoberto, via arquivo público diário
    da FINRA (Reg SHO) -- grátis, sem API key. Diferente do short_pct_of_float
    (yfinance, publicado só de 15 em 15 dias): isso é o pregão de ONTEM,
    mostra se a pressão vendedora está subindo ou caindo dia a dia. Tenta os
    últimos dias úteis até achar um arquivo publicado (finais de semana e
    feriados não têm arquivo). Fail-open: sem achar em ~7 dias corridos, ou
    qualquer falha de rede/parsing, devolve (None, nota) sem quebrar o resto
    do detector."""
    today = datetime.date.today()
    for days_back in range(1, 8):
        day = today - datetime.timedelta(days=days_back)
        if day.weekday() >= 5:  # sábado/domingo, sem arquivo
            continue
        url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day.strftime('%Y%m%d')}.txt"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception:
            continue
        for line in r.text.splitlines()[1:]:
            parts = line.split("|")
            if len(parts) >= 5 and parts[1].strip().upper() == ticker:
                try:
                    short_vol = float(parts[2])
                    total_vol = float(parts[4])
                except ValueError:
                    return None, "FINRA Reg SHO: linha do ticker em formato inesperado."
                if total_vol <= 0:
                    return None, f"FINRA Reg SHO: volume total zerado no pregão de {day.isoformat()}."
                ratio = round(short_vol / total_vol * 100, 2)
                return ratio, f"Fonte: FINRA Reg SHO, pregão de {day.isoformat()} (grátis, sem API key)."
        return None, f"FINRA Reg SHO: ticker não encontrado no arquivo de {day.isoformat()}."
    return None, "FINRA Reg SHO: nenhum arquivo publicado nos últimos 7 dias corridos."


def _local_minima_idx(values, order: int = 3) -> list[int]:
    """Índices posicionais de mínimos locais (menor valor numa janela de
    `order` dias pra cada lado). Implementação direta sem scipy (não é
    dependência do projeto) — suficiente pra uma janela de ~40 dias."""
    idx = []
    for i in range(order, len(values) - order):
        window = values[i - order : i + order + 1]
        if values[i] == window.min() and (window == values[i]).sum() == 1:
            idx.append(i)
    return idx


def _bullish_rsi_divergence(close, rsi, lookback: int) -> dict | None:
    """Divergência bullish clássica: preço faz uma mínima MAIS BAIXA que a
    mínima anterior, mas o RSI naquele ponto está MAIS ALTO — sinal de que
    o movimento de queda está perdendo força mesmo com o preço ainda
    caindo. Compara os 2 mínimos locais mais recentes dentro da janela."""
    import pandas as pd

    tail_close = close.iloc[-lookback:]
    tail_rsi = rsi.iloc[-lookback:]
    minima = _local_minima_idx(tail_close.values, order=3)
    if len(minima) < 2:
        return None
    i1, i2 = minima[-2], minima[-1]  # i1 mais antigo, i2 mais recente
    price1, price2 = float(tail_close.iloc[i1]), float(tail_close.iloc[i2])
    rsi1, rsi2 = float(tail_rsi.iloc[i1]), float(tail_rsi.iloc[i2])
    if pd.isna(rsi1) or pd.isna(rsi2):
        return None  # RSI ainda aquecendo (rolling(14)) -- sem dado confiavel pra comparar
    if price2 < price1 and rsi2 > rsi1:
        return {
            "date_low_anterior": str(tail_close.index[i1].date()),
            "date_low_recente": str(tail_close.index[i2].date()),
            "preco_low_anterior": round(price1, 2),
            "preco_low_recente": round(price2, 2),
            "rsi_low_anterior": round(rsi1, 2),
            "rsi_low_recente": round(rsi2, 2),
        }
    return None


@cached("squeeze_setup:{0}", ttl=1800)
def check_squeeze_setup(ticker: str, headlines: list | None = None) -> dict:
    """Detector combinado de "catalisador de squeeze + reversão técnica":
    só faz sentido combinar risco de short squeeze com sinais de reversão
    técnica de fundo — squeeze sozinho pode continuar caindo, reversão
    técnica sozinha pode ser só um repique comum sem o combustível extra
    de shorts sendo forçados a cobrir.

    Risco de squeeze (short_pct/days_to_cover via yfinance, borrow_fee via
    iBorrowDesk, short_volume_ratio via FINRA Reg SHO — todos grátis, sem
    API key; ver 'squeeze_risk_level'):
    - short_pct_of_float >= 20% = perigoso
    - days_to_cover >= 5 dias = perigoso
    - borrow_fee >= 30% ao ano = perigoso (None se o ticker não é
      negociado via IBKR ou a fonte estiver fora do ar no momento)
    - short_volume_ratio >= 50% do volume do pregão de ontem = perigoso
      (None se a FINRA ainda não publicou o arquivo do dia) -- diferente do
      short_pct_of_float (publicado só de 15 em 15 dias), esse é diário,
      mostra se a pressão vendedora está subindo ou caindo agora
    - "alto" = 2 ou mais dos sinais disponíveis perigosos; "moderado" = 1;
      "baixo" = nenhum

    Confirmações de reversão técnica (soma em 'reversal_confirmations',
    precisa de pelo menos 2 pra 'reversal_confirmed'=true — 1 sinal
    isolado é ruído comum demais):
    - candle bullish (Martelo, Martelo Invertido, Engolfo de Alta,
      Estrela da Manhã — via detect_candle_patterns) ou Doji (indecisão)
    - divergência bullish RSI (preço faz mínima mais baixa, RSI mais alto)
    - volume >= 150% da média de 20 dias E preço perto (≤5%) de um fundo
      de 50 dias ("volume de pânico no fundo")
    - toque no suporte: preço dentro de 5% da mínima de 50 OU 200 pregões

    Catalisador (opcional, só enriquece — não é obrigatório pro alerta):
    - técnico: rompimento da máxima de 20 pregões com volume >=3x a média
    - manchete: passe `headlines` (a lista do ticker dentro do resultado de
      get_news, ex.: get_news_result[ticker]) — bate contra as mesmas
      palavras-chave de upgrade/notícia positiva do resto do projeto
      (contrato, guidance elevado, aprovação, recorde, etc.)
    - macro: se hoje/amanhã cair num evento do calendário (FOMC/CPI/PPI/
      JOBS/PCE), sinaliza a janela mas NÃO sabe se o resultado vai surpreender
      pra cima ou pra baixo — isso não é um dado disponível via yfinance.
    - dark pool: prints recentes via Unusual Whales (mesma integração que já
      existe em get_alt_data.py) -- opcional de verdade, requer
      UNUSUAL_WHALES_API_KEY configurada; sem a chave, 'dark_pool_activity'
      vem None e 'dark_pool_note' explica o motivo (fail-open, não derruba
      o resto do detector).
    """
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        import pandas as pd

        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="1y", auto_adjust=False)
        if hist.empty or len(hist) < 60:
            return {"ticker": ticker, "error": "Dados insuficientes"}

        close, volume = hist["Close"], hist["Volume"]
        price = float(close.iloc[-1])

        # ── Risco de squeeze ──
        short_pct = info.get("shortPercentOfFloat")
        days_to_cover = info.get("shortRatio")
        borrow_fee, borrow_fee_note = _fetch_borrow_fee(ticker)
        short_volume_ratio, short_volume_note = _fetch_short_volume_ratio(ticker)
        short_pct_num = round(short_pct * 100, 2) if short_pct else None
        short_dangerous = bool(short_pct_num is not None and short_pct_num >= _SQUEEZE_SHORT_PCT_DANGER)
        dtc_dangerous = bool(days_to_cover is not None and days_to_cover >= _SQUEEZE_DTC_DANGER)
        borrow_fee_dangerous = bool(borrow_fee is not None and borrow_fee >= _SQUEEZE_BORROW_FEE_DANGER)
        short_volume_dangerous = bool(short_volume_ratio is not None and short_volume_ratio >= _SQUEEZE_SHORT_VOLUME_DANGER)
        # "alto" exige 2+ sinais perigosos entre os disponíveis -- com só
        # short_pct/days_to_cover (os demais indisponíveis) isso equivale a
        # "os dois juntos", igual ao critério original antes do iBorrowDesk.
        n_dangerous = sum((short_dangerous, dtc_dangerous, borrow_fee_dangerous, short_volume_dangerous))
        squeeze_risk_level = (
            "alto" if n_dangerous >= 2
            else "moderado" if n_dangerous == 1
            else "baixo"
        )

        # ── RSI (série completa, pra divergência) + candle patterns ──
        delta = close.diff()
        avg_gain = delta.clip(lower=0).rolling(14).mean()
        avg_loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, float("nan")))
        rsi = rsi.where(avg_loss != 0, 100.0)
        rsi_now = round(float(rsi.iloc[-1]), 2) if not pd.isna(rsi.iloc[-1]) else None

        patterns = _ma.detect_candle_patterns_in_hist(hist, lookback=3)
        bullish_candle = next((p for p in patterns if p["direction"] == "bullish"), None)
        doji_candle = next((p for p in patterns if p["pattern"] == "Doji"), None)

        divergence = _bullish_rsi_divergence(close, rsi, _DIVERGENCE_LOOKBACK_DAYS)

        # ── Suporte (mínima rolante, não média móvel) ──
        support = {}
        for window in (50, 200):
            if len(close) >= window:
                low = float(close.iloc[-window:].min())
                dist = round((price / low - 1) * 100, 2) if low else None
                support[f"low_{window}d"] = round(low, 2)
                support[f"dist_from_low_{window}d_pct"] = dist
            else:
                support[f"low_{window}d"] = None
                support[f"dist_from_low_{window}d_pct"] = None
        support_touch = any(
            support[f"dist_from_low_{w}d_pct"] is not None and support[f"dist_from_low_{w}d_pct"] <= _SUPPORT_TOUCH_PCT
            for w in (50, 200)
        )

        # ── Volume no fundo ──
        vol_avg20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else None
        vol_today = float(volume.iloc[-1])
        volume_mult_20d = round(vol_today / vol_avg20, 2) if vol_avg20 else None
        dist_low_50 = support.get("dist_from_low_50d_pct")
        volume_at_bottom = bool(
            volume_mult_20d is not None and volume_mult_20d >= _BOTTOM_VOLUME_MULT
            and dist_low_50 is not None and dist_low_50 <= _SUPPORT_TOUCH_PCT * 2
        )

        # ── Confirmações de reversão técnica (conta quantas bateram) ──
        confirmations = []
        if bullish_candle:
            confirmations.append(f"candle {bullish_candle['pattern']} ({bullish_candle['date']})")
        elif doji_candle:
            confirmations.append(f"Doji ({doji_candle['date']}) — indecisão, confirmar próximo candle")
        if divergence:
            confirmations.append("divergência bullish RSI")
        if volume_at_bottom:
            confirmations.append(f"volume {volume_mult_20d:.1f}x a média de 20d perto de um fundo")
        if support_touch:
            w = 50 if (support.get("dist_from_low_50d_pct") or 999) <= _SUPPORT_TOUCH_PCT else 200
            confirmations.append(f"toque no suporte de {w} pregões")

        # ── Catalisador (opcional) ──
        breakout = None
        if len(close) >= _BREAKOUT_LOOKBACK_DAYS + 21:
            prior_high = float(close.iloc[-_BREAKOUT_LOOKBACK_DAYS - 1 : -1].max())
            if price > prior_high and vol_avg20 and volume_mult_20d and volume_mult_20d >= _BREAKOUT_VOLUME_MULT:
                breakout = {
                    "prior_resistance": round(prior_high, 2),
                    "volume_mult_20d": volume_mult_20d,
                }

        headline_catalyst = None
        for h in _ma._normalize_headlines(headlines or []):
            low = h.lower()
            if any(kw in low for kw in _ma.POSITIVE_KW) or any(kw in low for kw in _ma.UPGRADE_KW):
                headline_catalyst = h.strip()[:160]
                break

        macro_window = [
            {"evento": tipo, "data": d}
            for tipo, datas in _ma.MACRO_EVENTS.items()
            for d in datas
            if 0 <= (pd.Timestamp(d).date() - pd.Timestamp.today().date()).days <= 1
        ]

        dark_pool_activity, dark_pool_note = _fetch_dark_pool_activity(ticker)

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "squeeze_risk": {
                "level": squeeze_risk_level,
                "short_pct_of_float": short_pct_num,
                "short_pct_danger_threshold": _SQUEEZE_SHORT_PCT_DANGER,
                "days_to_cover": round(days_to_cover, 2) if days_to_cover else None,
                "days_to_cover_danger_threshold": _SQUEEZE_DTC_DANGER,
                "borrow_fee": borrow_fee,
                "borrow_fee_danger_threshold": _SQUEEZE_BORROW_FEE_DANGER,
                "borrow_fee_note": borrow_fee_note,
                "short_volume_ratio": short_volume_ratio,
                "short_volume_danger_threshold": _SQUEEZE_SHORT_VOLUME_DANGER,
                "short_volume_note": short_volume_note,
            },
            "rsi_14": rsi_now,
            "reversal_confirmations": confirmations,
            "reversal_confirmed": len(confirmations) >= 2,
            "divergence": divergence,
            "support": support,
            "volume_mult_vs_20d_avg": volume_mult_20d,
            "catalyst": {
                "technical_breakout": breakout,
                "headline": headline_catalyst,
                "macro_calendar_window": macro_window or None,
                "dark_pool_activity": dark_pool_activity,
                "dark_pool_note": dark_pool_note,
            },
            "squeeze_setup_detected": squeeze_risk_level == "alto" and len(confirmations) >= 2,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Fontes adicionais (grátis / tier limitado) ────────────────────────────────
# Levantamento pedido pelo usuário: fontes novas ou pouco exploradas que
# agregam informação sem custo (ou com tier gratuito genuinamente utilizável)
# pro agente. Todas fail-open -- sem a env var configurada, ou se o provedor
# estourar o limite de requisições, a ferramenta devolve um dict com
# "configured": false ou "error" em vez de derrubar o restante do turno.

_FRED_SERIES = {
    "cpi_index": "CPIAUCSL",
    "unemployment_rate_pct": "UNRATE",
    "fed_funds_rate_pct": "FEDFUNDS",
    "yield_curve_10y_2y_pct": "T10Y2Y",
}


@cached("macro_indicators", ttl=21600)
def get_macro_indicators() -> dict:
    """Indicadores macro oficiais via FRED (Federal Reserve Economic Data,
    grátis, chave instantânea em fred.stlouisfed.org) -- CPI, desemprego,
    taxa de juros do Fed e o spread da curva de juros 10 anos - 2 anos
    (negativo = curva invertida, sinal clássico de recessão). Complementa o
    calendário de eventos macro (que só sabe a DATA, não o número real).
    Sem FRED_API_KEY configurada, ou se algum indicador falhar, o campo
    correspondente vem None sem derrubar os demais."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "FRED_API_KEY não configurada — cadastre-se em fred.stlouisfed.org (grátis, instantâneo) para ativar.",
        }
    result: dict = {"configured": True}
    for field, series_id in _FRED_SERIES.items():
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
                timeout=15,
            )
            r.raise_for_status()
            obs = (r.json() or {}).get("observations") or []
            if obs and obs[0].get("value") not in (None, "."):
                result[field] = float(obs[0]["value"])
                result[f"{field}_date"] = obs[0].get("date")
            else:
                result[field] = None
        except Exception as e:
            print(f"[tools] get_macro_indicators({series_id}): {e}", file=sys.stderr)
            result[field] = None
            result.setdefault("errors", []).append(f"{series_id}: {e}")
    return result


@cached("retail_sentiment:{0}", ttl=900)
def get_retail_sentiment(ticker: str) -> dict:
    """Ranking de menções no Reddit (WallStreetBets e afins) via ApeWisdom
    -- grátis, sem chave. Só contagem/ranking, sem análise de sentimento:
    serve de termômetro de "hype" de varejo (relevante em movimentos tipo
    meme-stock, onde o preço tem componente de manada além do fundamento).
    Procura o ticker nas primeiras páginas do ranking geral; se não estiver
    entre os mais mencionados agora, devolve found=false (não é erro)."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        for page in range(1, 6):
            r = requests.get(
                f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}",
                timeout=15,
            )
            r.raise_for_status()
            body = r.json() or {}
            for row in body.get("results") or []:
                if str(row.get("ticker", "")).upper() == ticker:
                    return {
                        "ticker": ticker,
                        "found": True,
                        "rank": row.get("rank"),
                        "mentions": row.get("mentions"),
                        "mentions_24h_ago": row.get("mentions_24h_ago"),
                        "upvotes": row.get("upvotes"),
                        "rank_24h_ago": row.get("rank_24h_ago"),
                    }
            if page >= (body.get("pages") or 1):
                break
        return {"ticker": ticker, "found": False, "note": "Não está entre os tickers mais mencionados no momento (ApeWisdom)."}
    except Exception as e:
        print(f"[tools] get_retail_sentiment({ticker}): {e}", file=sys.stderr)
        return {"ticker": ticker, "error": str(e)}


@cached("gamma_exposure:{0}", ttl=1800)
def get_gamma_exposure(ticker: str) -> dict:
    """Exposição de gamma dos market makers via FlashAlpha (GEX, paredes de
    call/put, gamma flip) -- tier grátis de só 5 requisições/DIA, por isso
    essa ferramenta só fica disponível no Chat (nunca nas varreduras
    automáticas de carteira/pré-mercado, que estourariam o limite num único
    ciclo). Sem FLASHALPHA_API_KEY configurada, ou se o limite diário já
    tiver acabado (HTTP 429), devolve o motivo em vez de erro."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    api_key = os.environ.get("FLASHALPHA_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "FLASHALPHA_API_KEY não configurada — cadastre-se em flashalpha.com (tier grátis, 5 requisições/dia) para ativar.",
        }
    try:
        r = requests.get(
            f"https://lab.flashalpha.com/v1/exposure/gex/{ticker}",
            headers={"X-Api-Key": api_key},
            timeout=15,
        )
        if r.status_code == 429:
            print(f"[tools] get_gamma_exposure({ticker}): HTTP 429, limite diário atingido", file=sys.stderr)
            return {"configured": True, "error": "Limite diário grátis da FlashAlpha (5 req/dia) já foi atingido hoje."}
        r.raise_for_status()
        data = r.json()
        data["configured"] = True
        return data
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")[:500]
        print(f"[tools] get_gamma_exposure({ticker}): {type(e).__name__}: {e} | body={body}", file=sys.stderr)
        return {"configured": True, "error": str(e)}


@cached("earnings_transcript:{0}", ttl=21600)
def get_earnings_transcript(ticker: str, max_chars: int = 6000) -> dict:
    """Transcrição completa da última teleconferência de resultados via Roic
    AI -- tier grátis (5 requisições/min, todos os tickers, 2 anos de
    histórico). Só disponível no Chat (o rate limit por minuto é apertado
    demais pra uma varredura automática chamando vários tickers em
    paralelo). Deixa o agente citar trecho real do guidance da diretoria em
    vez de só resumir a manchete de notícia sobre o resultado."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    api_key = os.environ.get("ROIC_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "ROIC_API_KEY não configurada — cadastre-se em roic.ai (tier grátis) para ativar.",
        }
    try:
        r = requests.get(
            f"https://api.roic.ai/v2/company/earnings-calls/latest/{ticker}",
            params={"apikey": api_key},
            timeout=20,
        )
        if r.status_code == 429:
            print(f"[tools] get_earnings_transcript({ticker}): HTTP 429, limite/minuto atingido", file=sys.stderr)
            return {"configured": True, "error": "Limite de requisições/minuto da Roic AI atingido -- tente de novo em instantes."}
        r.raise_for_status()
        data = r.json() or {}
        content = sanitize_for_llm(data.get("content") or "")
        truncated = len(content) > max_chars
        return {
            "configured": True,
            "ticker": ticker,
            "year": data.get("year"),
            "quarter": data.get("quarter"),
            "date": data.get("date"),
            "content": content[:max_chars] + (" [TRUNCADO]" if truncated else ""),
        }
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")[:500]
        print(f"[tools] get_earnings_transcript({ticker}): {type(e).__name__}: {e} | body={body}", file=sys.stderr)
        return {"configured": True, "error": str(e)}


@cached("fundamentals_valuation:{0}", ttl=21600)
def get_fundamentals_valuation(ticker: str) -> dict:
    """Valuation fundamentalista via Financial Modeling Prep -- tier grátis
    de 250 requisições/dia. DCF (valor justo estimado) + múltiplos TTM
    (P/L, P/VP, ROE, EV/EBITDA) -- nenhuma ferramenta do agente hoje calcula
    valor justo ou compara múltiplos de valuation, só técnicos/opções/
    sentimento. Complementa análises de longo prazo (o agente já cobre bem
    timing de curto prazo via técnicos, mas não "está caro ou barato?")."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        return {
            "configured": False,
            "message": "FMP_API_KEY não configurada — cadastre-se em financialmodelingprep.com (tier grátis, 250 req/dia) para ativar.",
        }
    try:
        dcf_resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/discounted-cash-flow/{ticker}",
            params={"apikey": api_key},
            timeout=15,
        )
        dcf_resp.raise_for_status()
        dcf_rows = dcf_resp.json() or []
        dcf = dcf_rows[0] if dcf_rows else {}

        metrics_resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/key-metrics-ttm/{ticker}",
            params={"apikey": api_key},
            timeout=15,
        )
        metrics_resp.raise_for_status()
        metrics_rows = metrics_resp.json() or []
        metrics = metrics_rows[0] if metrics_rows else {}

        dcf_value = dcf.get("dcf")
        stock_price = dcf.get("Stock Price")
        upside_pct = (
            round((dcf_value - stock_price) / stock_price * 100, 2)
            if dcf_value is not None and stock_price not in (None, 0)
            else None
        )
        return {
            "configured": True,
            "ticker": ticker,
            "current_price": stock_price,
            "dcf_fair_value": dcf_value,
            "dcf_implied_upside_pct": upside_pct,
            "pe_ratio_ttm": metrics.get("peRatioTTM"),
            "pb_ratio_ttm": metrics.get("pbRatioTTM"),
            "roe_ttm": metrics.get("roeTTM"),
            "ev_to_ebitda_ttm": metrics.get("evToEbitdaTTM"),
        }
    except Exception as e:
        # Corpo da resposta (se veio de um raise_for_status) costuma trazer o
        # motivo real da FMP (chave inválida, endpoint fora do plano grátis
        # etc.) -- str(e) sozinho só traz "403 Client Error" sem contexto.
        body = getattr(getattr(e, "response", None), "text", "")[:500]
        print(f"[tools] get_fundamentals_valuation({ticker}): {type(e).__name__}: {e} | body={body}", file=sys.stderr)
        return {"configured": True, "error": str(e)}


def get_insider_trades(ticker: str) -> dict:
    """Compra/venda de insiders da PRÓPRIA empresa (CEO, CFO, diretoria --
    Form 4 da SEC) via Form4API, tier grátis (15 mil requisições/mês, sem
    cartão). Diferente de dark pool / congress trading: aqui é quem dirige
    o negócio agindo com informação privilegiada de dentro -- insider
    comprando perto de um fundo é confirmação clássica de reversão."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    return _alt_data.insider_trades({ticker})


# ── Calendário de resultados ──────────────────────────────────────────────────


@cached("earnings_cal:{0}", ttl=3600)
def get_earnings_calendar(tickers: list[str] | None = None) -> list[dict]:
    """Retorna datas e estimativas de resultados dos tickers cobertos."""
    from . import config

    symbols = tickers or config.TICKERS
    results = []
    for sym in symbols:
        if config.has_no_earnings_data(sym):
            results.append({"ticker": sym, "next_earnings_date": None})
            continue
        try:
            t = yf.Ticker(sym)
            cal = t.calendar

            # yfinance returns dict or DataFrame depending on version
            if cal is None:
                results.append({"ticker": sym, "next_earnings_date": None})
                continue

            # Normalize to dict
            if hasattr(cal, "to_dict"):
                cal = cal.to_dict()

            dates = cal.get("Earnings Date") or []
            if not dates:
                results.append({"ticker": sym, "next_earnings_date": None})
                continue

            next_date = dates[0] if hasattr(dates, "__iter__") else dates
            date_str = (
                str(next_date.date()) if hasattr(next_date, "date") else str(next_date)
            )
            try:
                days_until = (
                    datetime.date.fromisoformat(date_str) - datetime.date.today()
                ).days
            except Exception:
                days_until = None

            def _first(key):
                v = cal.get(key)
                if v is None:
                    return None
                return v[0] if hasattr(v, "__iter__") and not isinstance(v, str) else v

            results.append(
                {
                    "ticker": sym,
                    "next_earnings_date": date_str,
                    "days_until_earnings": days_until,
                    "eps_estimate_avg": _first("Earnings Average"),
                    "eps_estimate_low": _first("Earnings Low"),
                    "eps_estimate_high": _first("Earnings High"),
                    "revenue_estimate": _first("Revenue Average"),
                    "imminent": days_until is not None and 0 <= days_until <= 14,
                }
            )
        except Exception as e:
            results.append({"ticker": sym, "error": str(e)})
    return results


# ── Fear & Greed Index ────────────────────────────────────────────────────────


@cached("fear_greed", ttl=900)
def get_fear_greed_index() -> dict:
    """Retorna o Índice Fear & Greed da CNN para sentimento de mercado."""
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PremarketAgent/1.0)",
            "Referer": "https://edition.cnn.com/",
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

        current = data.get("fear_and_greed", {})
        score = current.get("score")
        rating = current.get("rating", "")

        hist = data.get("fear_and_greed_historical", {})

        def _classify(s):
            if s is None:
                return "desconhecido"
            if s <= 25:
                return "medo extremo"
            if s <= 45:
                return "medo"
            if s <= 55:
                return "neutro"
            if s <= 75:
                return "ganância"
            return "ganância extrema"

        def _safe_score(obj):
            if isinstance(obj, dict):
                return obj.get("score")
            return None

        return {
            "score": round(score, 1) if score is not None else None,
            "rating_en": rating,
            "rating_pt": _classify(score),
            "prev_close": _safe_score(hist.get("previousClose")),
            "one_week_ago": _safe_score(hist.get("oneWeekAgo")),
            "one_month_ago": _safe_score(hist.get("oneMonthAgo")),
            "one_year_ago": _safe_score(hist.get("oneYearAgo")),
            "interpretation": (
                "Pânico — potencial oportunidade contrária"
                if score and score <= 25
                else "Medo predominante — cautela"
                if score and score <= 45
                else "Sentimento neutro"
                if score and score <= 55
                else "Mercado ganancioso — risco de reversão"
                if score and score <= 75
                else "Euforia — risco máximo de reversão"
            ),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Ratings de analistas ──────────────────────────────────────────────────────


@cached("analyst_ratings:{0}", ttl=3600)
def get_analyst_ratings(ticker: str) -> dict:
    """Retorna consenso, preços-alvo e upgrades/downgrades recentes de analistas."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        rec_key = info.get("recommendationKey", "")
        rec_mean = info.get("recommendationMean")
        num_analysts = info.get("numberOfAnalystOpinions")
        target_mean = info.get("targetMeanPrice")
        target_median = info.get("targetMedianPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")

        current_price = info.get("regularMarketPrice") or info.get("currentPrice")
        upside = None
        if target_mean and current_price and current_price > 0:
            upside = round((target_mean - current_price) / current_price * 100, 1)

        rec_labels = {
            "strongBuy": "compra forte",
            "buy": "compra",
            "hold": "manter",
            "sell": "venda",
            "strongSell": "venda forte",
        }

        upgrades_downgrades = []
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                for _, row in ud.head(10).reset_index().iterrows():
                    upgrades_downgrades.append(
                        {
                            "date": str(row.get("GradeDate", "")),
                            "firm": row.get("Firm", ""),
                            "from_grade": row.get("FromGrade", ""),
                            "to_grade": row.get("ToGrade", ""),
                            "action": row.get("Action", ""),
                        }
                    )
        except Exception:
            pass

        return {
            "ticker": ticker,
            "consensus": rec_labels.get(rec_key, rec_key),
            "recommendation_mean_1_5": round(rec_mean, 2) if rec_mean else None,
            "num_analysts": num_analysts,
            "target_mean": target_mean,
            "target_median": target_median,
            "target_high": target_high,
            "target_low": target_low,
            "upside_to_mean_pct": upside,
            "recent_upgrades_downgrades": upgrades_downgrades,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Contágio setorial ────────────────────────────────────────────────────────


def detect_sector_contagion(
    period: str = "5d",
    interval: str = "1d",
    trigger_pct: float | None = None,
    sympathy_pct: float | None = None,
) -> dict:
    """
    Detecta contágio setorial entre os grupos da cadeia de IA:
    Memória/Armazenamento, Interconexão/Servidores, Energia/Refrigeração e Fundição/Equipamentos.
    Retorna lista de alertas com líder, vizinhos confirmando e candidatos a catch-up.
    """
    try:
        kwargs: dict = {"period": period, "interval": interval}
        if trigger_pct is not None:
            kwargs["trigger_pct"] = trigger_pct
        if sympathy_pct is not None:
            kwargs["sympathy_pct"] = sympathy_pct

        alerts = _sc.detect_contagion(**kwargs)
        return {
            "total": len(alerts),
            "alerts": [a.to_dict() for a in alerts],
            "messages": [a.to_message() for a in alerts],
            "groups_monitored": {k: v["label"] for k, v in _sc.SECTOR_GROUPS.items()},
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "alerts": []}


# ── Análise de mercado (market_alerts) ───────────────────────────────────────


def get_global_market_snapshot() -> dict:
    """
    Variação % do último pregão disponível para os mercados que operam antes ou
    durante o pré-mercado da Nasdaq: Ásia overnight (Nikkei, KOSPI, Hang Seng),
    Europa em overlap direto (DAX, FTSE, CAC), EUR/USD e futuros de índice dos
    EUA (NQ, ES). Dado bruto de contexto — sem pontuação/composite embutido.
    """
    try:
        return _ma.get_global_market_snapshot()
    except Exception as e:
        return {"error": str(e), "items": []}


def get_europe_regime_signal() -> dict:
    """
    Sinal de regime validado por backtest real (PRs #54-#61): fora de
    tendência de alta clara na Nasdaq (^IXIC abaixo da própria SMA200), a
    média de variação diária de DAX+CAC+FTSE tem edge real, líquido de
    custo de transação, sobre o retorno abertura→fechamento da Nasdaq. Em
    tendência de alta, a estratégia perde do buy&hold puro — nesse caso o
    sinal fica "sem sinal ativo". Validado SOMENTE contra ^IXIC como alvo;
    NÃO usar como sinal de entrada/saída pra um ticker individual (MU,
    NVDA, SKHY etc.) sem validar isso separadamente.
    """
    try:
        return _ma.get_europe_regime_signal()
    except Exception as e:
        return {"error": str(e)}


def check_market_alerts(
    tickers: list[str] | None = None,
    headlines_by_ticker: dict[str, list] | None = None,
    filings_by_ticker: dict[str, list] | None = None,
    check_edgar: bool = True,
    check_halts: bool = True,
) -> dict:
    """
    Roda todos os checks do módulo market_alerts e retorna um relatório
    estruturado com alertas de setor, macro, técnicos, earnings, geopolítico,
    insider trading (Form 4) e circuit breakers. Inclui choque de alta no
    petróleo (WTI) e um alerta CRÍTICO de "regime de risco macro elevado"
    quando 2+ de {juro de 10y alto, choque de petróleo, manchete de conflito
    armado/estreito de Taiwan} estão ativos ao mesmo tempo -- combinação
    historicamente associada a pressão sobre growth/tech de múltiplo alto.

    tickers: lista de símbolos (default: MU, SMCI)
    headlines_by_ticker: manchetes coletadas por ticker (do get_news) -- usado
        também pro sinal combinado de regime macro acima, então vale passar
        manchetes de TODOS os tickers analisados, não só um
    filings_by_ticker: filings coletados por ticker (do search_edgar_filings);
        se omitido e check_edgar=True, busca direto na API da SEC
    check_edgar: habilita check de 8-K + Form 4 (padrão True)
    check_halts: habilita circuit breaker S&P + halt intraday (padrão True)
    """
    from . import config

    tickers = tickers or config.TICKERS
    headlines_by_ticker = headlines_by_ticker or {}
    filings_by_ticker = filings_by_ticker or {}

    try:
        alerts = _ma.run_all_alerts(
            tickers,
            headlines_by_ticker=headlines_by_ticker,
            filings_by_ticker=filings_by_ticker,
            check_edgar=check_edgar,
            check_halts=check_halts,
        )
        return {
            "total": len(alerts),
            "prompt_block": _ma.alerts_to_prompt_block(alerts),
            "alerts": [a.to_dict() for a in alerts],
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "alerts": []}


# ── Definição das ferramentas para a API da Anthropic ────────────────────────

TOOLS = [
    {
        "name": "get_stock_data",
        "description": "Retorna dados de cotação e pré-mercado de um ticker (preço, variação, volume, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Símbolo do ativo, ex: MU, SMCI",
                }
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_news",
        "description": (
            "Retorna as manchetes mais recentes de um ou mais tickers em UMA ÚNICA "
            "chamada. Sempre inclua TODOS os tickers que você precisa analisar nesta "
            "mesma chamada (lista), nunca um por vez em chamadas separadas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Lista de símbolos, ex: ["MU", "SMCI", "NVDA"]',
                },
                "max_items": {"type": "integer", "default": 8},
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "search_edgar_filings",
        "description": "Busca filings recentes do ticker na SEC EDGAR (8-K, 10-Q, 10-K, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "form_type": {"type": "string", "default": "8-K"},
                "count": {"type": "integer", "default": 5},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "read_filing",
        "description": "Lê o conteúdo de um filing da SEC a partir de uma URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "default": 4000},
            },
            "required": ["url"],
        },
    },
    {
        "name": "save_observation",
        "description": "Salva a observação do dia para um ativo na memória do agente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "Resumo factual em 2-4 frases",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                    "description": "Sentimento geral com base nos dados coletados",
                },
                "price": {
                    "type": "number",
                    "description": "Preço atual ou pré-mercado do ativo",
                },
            },
            "required": ["ticker", "summary", "sentiment"],
        },
    },
    {
        "name": "list_alerts",
        "description": (
            "Lista os alertas de preço cadastrados no sistema. "
            "Chame no início da análise para saber quais alertas já existem antes de criar novos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Filtrar por ticker (ex: MU). Omita para listar todos.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_alert",
        "description": (
            "Cria um novo alerta de preço baseado na análise do dia. "
            "Use quando identificar um nível técnico relevante (suporte, resistência), "
            "catalisador iminente (resultados, guidance), ou padrão de volume anormal "
            "que justifique monitoramento. Não duplique alertas já existentes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo do ativo, ex: MU"},
                "condition": {
                    "type": "string",
                    "enum": ["above", "below"],
                    "description": "'above' para alta acima do threshold, 'below' para queda abaixo",
                },
                "threshold_pct": {
                    "type": "number",
                    "description": (
                        "Variação percentual em relação ao fechamento anterior. "
                        "Negativo para quedas (ex: -7.5 = queda de 7,5%), "
                        "positivo para altas (ex: 4.0 = alta de 4%). "
                        "NÃO use um valor fixo igual para todos os ativos — "
                        "calibre pelo atr_pct de get_technical_indicators "
                        "(threshold_pct ≈ atr_pct * 1.5). Um ativo com atr_pct "
                        "alto (ex.: ARM ~8%) precisa de threshold bem maior que "
                        "um com atr_pct baixo (ex.: GOOGL ~2%), senão o alerta "
                        "dispara em ruído normal do dia a dia."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Justificativa técnica/fundamentalista para o alerta (1-2 frases).",
                },
            },
            "required": ["symbol", "condition", "threshold_pct", "reason"],
        },
    },
    {
        "name": "delete_alert",
        "description": (
            "Remove um alerta que não é mais relevante. "
            "Use quando um nível já foi superado, o contexto mudou significativamente, "
            "ou o alerta está duplicado/desatualizado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "integer",
                    "description": "ID do alerta a remover (obtido via list_alerts).",
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo da remoção.",
                },
            },
            "required": ["alert_id", "reason"],
        },
    },
    {
        "name": "get_options_data",
        "description": (
            "Retorna dados de opções do ticker: put/call ratio, IV ATM (%), "
            "calls e puts mais negociadas por volume. "
            "Put/call ratio > 1 indica viés bearish no mercado de opções; < 0.7 indica viés bullish. "
            "IV alta sinaliza expectativa de movimento brusco (ex: resultado, anúncio)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Símbolo do ativo, ex: MU"},
                "expiry": {
                    "type": "string",
                    "description": "Data de vencimento no formato YYYY-MM-DD. Omita para usar o mais próximo.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_technical_indicators",
        "description": (
            "Calcula indicadores técnicos para o ticker: RSI-14, MACD (12/26/9), "
            "Bollinger Bands (20/2), EMA 8/21, SMA 50/200, ATR-14 e ratio de "
            "volume (5d vs 20d). Use para avaliar condição técnica antes de "
            "criar alertas de preço. rsi_signal já vem calibrado pela "
            "volatilidade do ativo (bandas mais largas em ativos de ATR alto "
            "como ARM/SMCI, mais estreitas em big techs estáveis) — não "
            "reaplique 30/70 fixo por cima. ema8/ema21 são para timing de "
            "curto prazo; sma50/sma200/macro_trend_filter servem só de "
            "filtro de tendência maior."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {
                    "type": "string",
                    "description": "Período histórico: qualquer valor 'Nd'/'Nmo'/'Ny' (ex.: '5mo', '18mo', '2y') ou 'ytd'/'max'. Default: '6mo'.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "detect_candle_patterns",
        "description": (
            "Detecta padrões clássicos de candlestick (velas) nos candles diários "
            "recentes do ticker: Doji, Martelo/Enforcado, Estrela Cadente/Martelo "
            "Invertido, Engolfo de Alta/Baixa, Estrela da Manhã/Noite. Cruze a data "
            "de cada padrão encontrado com as manchetes de get_news do mesmo "
            "período — um padrão de reversão coincidindo com notícia relevante é "
            "sinal mais forte do que qualquer um isolado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {
                    "type": "string",
                    "description": "Período histórico buscado: qualquer valor 'Nd'/'Nmo'/'Ny' (ex.: '5mo', '18mo', '2y') ou 'ytd'/'max'. Default: '1mo'.",
                },
                "lookback": {
                    "type": "integer",
                    "description": (
                        "Quantos candles recentes (a partir do mais recente) escanear em "
                        "busca de padrões. Default: 5. Para cobrir o período inteiro "
                        "buscado (ex.: analisar o ano todo com period='1y'), use um "
                        "lookback alto o bastante para cobrir todos os candles do período "
                        "(aprox. 21 candles/mês: ~21 para '1mo', ~63 para '3mo', ~126 para "
                        "'6mo', ~252 para '1y')."
                    ),
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_sector_performance",
        "description": (
            "Retorna a performance do dia e do pré-mercado dos ETFs de setor: "
            "SMH, SOXX, XLK, QQQ, SPY, IWM, XLF, XLV, IBB. "
            "Use para contextualizar se uma queda/alta de um ativo é idiossincrática "
            "ou reflexo de movimento amplo do setor (semis: SMH/SOXX; saúde: XLV/IBB)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "etfs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista customizada de ETFs. Omita para usar os defaults (SMH, SOXX, XLK, QQQ, SPY, IWM, XLF).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_short_interest",
        "description": (
            "Retorna o short interest do ativo: % do float vendido a descoberto, "
            "days-to-cover e variação vs mês anterior. "
            "Short float > 20%: risco alto de short squeeze. "
            "Days-to-cover alto + catalisador positivo = potencial de squeeze acelerado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Símbolo do ativo"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_earnings_calendar",
        "description": (
            "Retorna as próximas datas de resultado (earnings) dos tickers cobertos, "
            "com estimativas de EPS (avg/low/high) e receita do consenso. "
            "O campo 'imminent' é true quando o resultado está em até 14 dias. "
            "Use para ajustar a análise de risco e criar alertas de volatilidade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de tickers. Omita para usar todos os tickers sob cobertura.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_fear_greed_index",
        "description": (
            "Retorna o Índice Fear & Greed da CNN (0–100): sentimento atual do mercado americano. "
            "0–25: medo extremo, 26–45: medo, 46–55: neutro, 56–75: ganância, 76–100: ganância extrema. "
            "Inclui histórico (fechamento anterior, 1 semana, 1 mês, 1 ano atrás). "
            "Medo extremo pode sinalizar fundo de curto prazo; ganância extrema, topo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_geopolitical_news",
        "description": (
            "Retorna manchetes recentes sobre temas macro/geopolíticos que impactam a bolsa: "
            "falas e decisões de chefes de estado (EUA e outros países) sobre tarifas/comércio, "
            "guerra, preço do petróleo, Big Techs (antitrust/regulação/IA) e controle de "
            "exportação de semicondutores (China/Taiwan). Não precisa de ticker — chame UMA vez "
            "por execução, já cobre todos os temas de uma vez. Complementa get_news (que é por "
            "ativo específico da carteira), não substitui."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_items": {
                    "type": "integer",
                    "description": "Máximo de manchetes por tema. Default: 6.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_analyst_ratings",
        "description": (
            "Retorna o consenso de analistas (compra forte / compra / manter / venda), "
            "número de analistas, preço-alvo médio/mediano/high/low, upside implícito "
            "e os 10 upgrades/downgrades mais recentes com firma e grau anterior/novo. "
            "Use para identificar mudanças recentes de rating que podem mover o preço."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Símbolo do ativo"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_global_market_snapshot",
        "description": (
            "Retorna a variação % do último pregão disponível para mercados que operam antes ou "
            "durante o pré-mercado da Nasdaq: Nikkei 225 e KOSPI (Ásia overnight), Hang Seng "
            "(Hong Kong), DAX/FTSE 100/CAC 40 (Europa, overlap direto com o pré-mercado dos EUA), "
            "EUR/USD e futuros de índice (Nasdaq 100 e S&P 500). "
            "É dado bruto de contexto, sem pontuação/composite embutido — não ajuste thresholds "
            "de compra/venda com base nisso sem validar via backtest primeiro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_europe_regime_signal",
        "description": (
            "Retorna um sinal de regime validado por backtest real (PRs #54-#61 -- ver memory "
            "doc skhy-ipo-monitoring.md): se a Nasdaq (^IXIC) está abaixo da própria SMA200 "
            "('correcao_lateral'), calcula a média de variação diária de DAX+CAC+FTSE e devolve "
            "um viés direcional (long/short) pro Nasdaq -- esse sinal só bate o buy&hold líquido "
            "de custo real nesse regime. Se a Nasdaq está acima da SMA200 ('alta'), devolve "
            "'sem sinal ativo', porque nesse regime a estratégia perde do buy&hold puro. "
            "Validado SOMENTE contra ^IXIC como alvo, em dois regimes históricos -- NÃO use como "
            "sinal de entrada/saída pra um ticker individual (MU, NVDA, SKHY etc.) sem validar "
            "isso separadamente antes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "detect_sector_contagion",
        "description": (
            "Detecta contágio setorial entre os grupos da cadeia de IA. "
            "Grupos monitorados: "
            "(1) Memória/Armazenamento — MU, SNDK, WDC; "
            "(2) Interconexão/Servidores — SMCI, ALAB, CRDO, ANET; "
            "(3) Energia/Refrigeração — VRT; "
            "(4) Fundição/Equipamentos — TSM, ASML. "
            "Para cada grupo, identifica o 'líder' (ticker com maior movimento) e classifica "
            "os vizinhos em 'confirming' (já acompanhando — o tema está ativo) ou "
            "'catch_up' (ainda parados — candidatos a seguir). "
            "Use no início da análise para priorizar quais ativos investigar com mais profundidade. "
            "Para pré-mercado/intradiário use period='1d' e interval='5m'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Período histórico: '5d' (default, dia a dia), '1d' (intradiário).",
                },
                "interval": {
                    "type": "string",
                    "description": "Intervalo das barras: '1d' (default), '5m' (pré-market/intradiário).",
                },
                "trigger_pct": {
                    "type": "number",
                    "description": "Movimento mínimo (%) para um ticker ser considerado líder. Default: 4.0.",
                },
                "sympathy_pct": {
                    "type": "number",
                    "description": "Movimento mínimo (%) para um vizinho ser 'confirming'. Default: 1.5.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_market_alerts",
        "description": (
            "Analisa o estado atual do mercado e retorna uma lista estruturada de sinais "
            "categorizados por severidade (critico / atencao / info). Inclui: "
            "(1) contágio de setor — bellwethers NVDA, AVGO, TSM, SOXX, SMH caindo >4%; "
            "(2) pares asiáticos de memória — SK Hynix e Samsung (sinal antecedente para MU); "
            "(3) gatilhos macro — FOMC, CPI, PCE (inflação preferida do Fed), PPI, JOBS (Payroll), "
            "juro de 10 anos, choque de petróleo, calendário 2026; "
            "(4) técnico por ativo — RSI sobrecomprado, distância da MM200, proximidade da máxima de 52s, "
            "spike de volume, gap de abertura; "
            "(5) earnings — alerta se resultado estiver em até 7 dias; "
            "(6) risco geopolítico/regulatório — controle de exportação/China, antitruste, tarifas; "
            "(7) circuit breaker de mercado — S&P 500 perto de -5/-7/-13/-20% + halt intraday da ação; "
            "(8) EDGAR — 8-K recente (evento material) + Form 4 com parse de compra/venda de insider "
            "em mercado aberto (valor em USD, nome do dirigente). "
            "Passe headlines_by_ticker com as manchetes do get_news para ativar checks (6), e "
            "filings_by_ticker com filings do search_edgar_filings para enriquecer o check (8); "
            "se filings_by_ticker for omitido, busca diretamente na API da SEC."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de tickers a analisar individualmente. Default: MU e SMCI.",
                },
                "headlines_by_ticker": {
                    "type": "object",
                    "description": (
                        "Manchetes coletadas por ticker — passe DIRETO o resultado de get_news "
                        "(já vem no formato {ticker: [manchetes]}). Ativa checks geopolítico, "
                        "downgrade e sell-the-news. "
                        'Ex: {"MU": ["Micron downgraded...", "Export controls..."], "SMCI": [...]}'
                    ),
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "filings_by_ticker": {
                    "type": "object",
                    "description": (
                        "Filings coletados por ticker (do search_edgar_filings). Enriquece o check de EDGAR. "
                        "Se omitido, busca direto na API da SEC. "
                        'Ex: {"MU": [{"form": "8-K", "date": "2026-06-01", ...}]}'
                    ),
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "check_edgar": {
                    "type": "boolean",
                    "description": "Habilitar check de 8-K e Form 4 via EDGAR. Default: true.",
                },
                "check_halts": {
                    "type": "boolean",
                    "description": "Habilitar circuit breaker do S&P 500 e halt intraday. Default: true.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_squeeze_setup",
        "description": (
            "Detector combinado de 'catalisador de squeeze + reversão técnica' pra um ticker: "
            "só faz sentido combinar risco de short squeeze com reversão técnica de fundo — "
            "squeeze sozinho pode continuar caindo, reversão sozinha pode ser só um repique comum. "
            "Risco de squeeze: short_pct_of_float >= 20% (yfinance), days_to_cover >= 5 dias "
            "(yfinance), borrow_fee >= 30% ao ano (iBorrowDesk, espelha dado público do IBKR, "
            "grátis — None se o ticker não é negociado via IBKR) e short_volume_ratio >= 50% do "
            "volume do pregão de ontem (FINRA Reg SHO, grátis, diário — mostra pressão vendedora "
            "atual, diferente do short_pct_of_float que só atualiza de 15 em 15 dias). "
            "'alto' = 2+ desses 4 sinais perigosos juntos; 'moderado' = só 1; 'baixo' = nenhum. "
            "Confirmações de reversão técnica (precisa de 2+ pra reversal_confirmed=true): candle "
            "bullish (Martelo/Engolfo de Alta/Estrela da Manhã) ou Doji, divergência bullish RSI "
            "(preço faz mínima mais baixa com RSI mais alto), volume >=150% da média de 20d perto "
            "de um fundo, toque na mínima de 50 ou 200 pregões (suporte real, não média móvel). "
            "Catalisador opcional (não obrigatório pro alerta): rompimento técnico de resistência "
            "com volume 3x, manchete positiva (passe headlines com a lista do ticker dentro do "
            "resultado de get_news), janela de evento "
            "macro (FOMC/CPI/PPI/JOBS/PCE) — o calendário não diz se o resultado vai surpreender "
            "pra cima ou pra baixo, só sinaliza a data — ou atividade de dark pool (Unusual "
            "Whales, opcional, requer UNUSUAL_WHALES_API_KEY). squeeze_setup_detected=true exige "
            "risco 'alto' E 2+ confirmações técnicas juntos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Símbolo do ativo, ex: MU."},
                "headlines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Manchetes recentes do ticker (a lista desse ticker dentro do resultado de get_news), pra tentar achar catalisador de notícia.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_macro_indicators",
        "description": (
            "Indicadores macro oficiais via FRED (Federal Reserve) -- CPI, taxa de desemprego, "
            "Fed funds rate e o spread da curva de juros 10 anos - 2 anos (negativo = curva "
            "invertida, sinal clássico de recessão). Sem tickers, é contexto macro geral. "
            "Requer FRED_API_KEY (grátis); sem a chave, volta configured=false."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_retail_sentiment",
        "description": (
            "Ranking de menções de um ticker no Reddit (WallStreetBets e afins) via ApeWisdom -- "
            "grátis, sem chave. Só contagem/ranking, sem sentimento: termômetro de hype de varejo, "
            "útil em movimentos com componente de manada (meme-stock)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Símbolo do ativo, ex: SMCI."}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_fundamentals_valuation",
        "description": (
            "Valuation fundamentalista via Financial Modeling Prep: valor justo estimado (DCF) e "
            "upside implícito vs. preço atual, mais múltiplos TTM (P/L, P/VP, ROE, EV/EBITDA). "
            "Nenhuma outra ferramenta calcula 'está caro ou barato'. Requer FMP_API_KEY (grátis, "
            "250 req/dia); sem a chave, volta configured=false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Símbolo do ativo, ex: NVDA."}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_insider_trades",
        "description": (
            "Compra/venda de insiders da PRÓPRIA empresa (CEO, CFO, diretoria -- Form 4 da SEC) "
            "via Form4API, últimos 90 dias. Diferente de dark pool ou congress trading: aqui é "
            "quem dirige o negócio. Requer FORM4API_KEY (grátis, 15k req/mês); sem a chave, volta "
            "configured=false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Símbolo do ativo, ex: MU."}},
            "required": ["ticker"],
        },
    },
]

# Ferramentas com tier grátis apertado demais pra varredura automática de
# carteira (5 req/dia ou 5 req/min) -- ficam FORA de TOOLS de propósito, só
# entram no schema do Chat (ver CHAT_ONLY_TOOLS em agent.py), pra nunca
# serem chamadas em paralelo pros N tickers de um scan e estourar o limite.
CHAT_ONLY_TOOLS = [
    {
        "name": "get_gamma_exposure",
        "description": (
            "Exposição de gamma dos market makers (GEX) via FlashAlpha -- paredes de call/put, "
            "nível de gamma flip, sinal institucional de suporte/resistência e risco de gamma "
            "squeeze. Tier grátis de só 5 req/DIA -- use com parcimônia, um ticker por vez. "
            "Requer FLASHALPHA_API_KEY; sem a chave, volta configured=false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Símbolo do ativo, ex: SPY."}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_earnings_transcript",
        "description": (
            "Transcrição completa da última teleconferência de resultados via Roic AI (truncada). "
            "Deixa citar trecho real do guidance da diretoria em vez de só resumir a manchete "
            "sobre o resultado. Tier grátis de 5 req/min -- use com parcimônia. Requer "
            "ROIC_API_KEY; sem a chave, volta configured=false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Símbolo do ativo, ex: TSLA."}},
            "required": ["ticker"],
        },
    },
]

DISPATCH = {
    "get_stock_data": get_stock_data,
    "get_news": get_news,
    "search_edgar_filings": search_edgar_filings,
    "read_filing": read_filing,
    "save_observation": save_observation,
    "list_alerts": list_alerts,
    "create_alert": create_alert,
    "delete_alert": delete_alert,
    "check_market_alerts": check_market_alerts,
    "get_options_data": get_options_data,
    "get_technical_indicators": get_technical_indicators,
    "detect_candle_patterns": detect_candle_patterns,
    "get_sector_performance": get_sector_performance,
    "get_short_interest": get_short_interest,
    "get_earnings_calendar": get_earnings_calendar,
    "get_fear_greed_index": get_fear_greed_index,
    "get_geopolitical_news": get_geopolitical_news,
    "get_analyst_ratings": get_analyst_ratings,
    "detect_sector_contagion": detect_sector_contagion,
    "get_global_market_snapshot": get_global_market_snapshot,
    "get_europe_regime_signal": get_europe_regime_signal,
    "check_squeeze_setup": check_squeeze_setup,
    "get_macro_indicators": get_macro_indicators,
    "get_retail_sentiment": get_retail_sentiment,
    "get_fundamentals_valuation": get_fundamentals_valuation,
    "get_insider_trades": get_insider_trades,
    "get_gamma_exposure": get_gamma_exposure,
    "get_earnings_transcript": get_earnings_transcript,
}
