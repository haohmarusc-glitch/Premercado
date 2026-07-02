"""t:
Ferramentas disponíveis para o agente de pré-mercado.
"""

import datetime
import json
import os

import requests
import yfinance as yf

from . import market_alerts as _ma
from . import sector_contagion as _sc
from .cache import cached
from .security import sanitize_for_llm, sanitize_ticker, sanitize_url

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


@cached("news:{0}:{1}", ttl=600)
def get_news(ticker: str, max_items: int = 6) -> list[dict]:
    """Retorna manchetes recentes do ticker via yfinance (resumo truncado para economizar tokens)."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return [{"error": str(e)}]
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
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
    except Exception as e:
        return [{"error": str(e)}]


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
    threshold_pct: variação percentual relativa ao fechamento anterior (ex: -8.0 para queda de 8%)
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
    """Calcula RSI-14, MACD, Bollinger Bands e médias móveis para o ticker."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}
    try:
        import pandas as pd

        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker, "error": "Dados insuficientes"}

        close = hist["Close"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])

        # RSI 14
        delta = close.diff()
        avg_gain = delta.clip(lower=0).rolling(14).mean()
        avg_loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal_line

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
            if rsi > 70
            else "sobrevendido"
            if rsi < 30
            else "neutro",
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
            "sma50": sma50,
            "sma200": sma200,
            "pct_above_sma50": _pct_diff(price, sma50),
            "pct_above_sma200": _pct_diff(price, sma200),
            "volume_ratio_5d_vs_20d": vol_ratio,
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


# ── Calendário de resultados ──────────────────────────────────────────────────


@cached("earnings_cal:{0}", ttl=3600)
def get_earnings_calendar(tickers: list[str] | None = None) -> list[dict]:
    """Retorna datas e estimativas de resultados dos tickers cobertos."""
    from . import config

    symbols = tickers or config.TICKERS
    results = []
    for sym in symbols:
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
    insider trading (Form 4) e circuit breakers.

    tickers: lista de símbolos (default: MU, SMCI)
    headlines_by_ticker: manchetes coletadas por ticker (do get_news)
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
        "description": "Retorna as manchetes mais recentes sobre um ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "max_items": {"type": "integer", "default": 8},
            },
            "required": ["ticker"],
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
                        "positivo para altas (ex: 4.0 = alta de 4%)."
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
            "Bollinger Bands (20/2), SMA 50 e SMA 200, e ratio de volume (5d vs 20d). "
            "Use para avaliar condição técnica antes de criar alertas de preço."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {
                    "type": "string",
                    "description": "Período histórico: '3mo', '6mo', '1y'. Default: '6mo'.",
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
            "(3) gatilhos macro — FOMC, CPI, PPI, JOBS (Payroll), juro de 10 anos, calendário 2026 completo; "
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
                        "Manchetes coletadas por ticker (do get_news). Ativa checks geopolítico, "
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
    "get_sector_performance": get_sector_performance,
    "get_short_interest": get_short_interest,
    "get_earnings_calendar": get_earnings_calendar,
    "get_fear_greed_index": get_fear_greed_index,
    "get_analyst_ratings": get_analyst_ratings,
    "detect_sector_contagion": detect_sector_contagion,
}
