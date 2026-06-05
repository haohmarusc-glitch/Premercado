"""
Ferramentas disponíveis para o agente de pré-mercado.
"""
import datetime
import json
import os

import requests
import yfinance as yf

# ── Cotações ──────────────────────────────────────────────────────────────────

def get_stock_data(ticker: str) -> dict:
    """Retorna dados de cotação e pré-mercado do ticker."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="5d")
        last_close = float(hist["Close"].iloc[-1]) if not hist.empty else None
        pre_market = info.get("preMarketPrice")
        regular_market = info.get("regularMarketPrice") or info.get("currentPrice")
        change_pct = info.get("regularMarketChangePercent")
        volume = info.get("regularMarketVolume")
        avg_volume = info.get("averageVolume")
        fifty_two_week_high = info.get("fiftyTwoWeekHigh")
        fifty_two_week_low = info.get("fiftyTwoWeekLow")

        return {
            "ticker": ticker,
            "last_close": last_close,
            "pre_market_price": pre_market,
            "regular_market_price": regular_market,
            "change_pct": change_pct,
            "volume": volume,
            "avg_volume": avg_volume,
            "52w_high": fifty_two_week_high,
            "52w_low": fifty_two_week_low,
            "currency": info.get("currency", "USD"),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Notícias ──────────────────────────────────────────────────────────────────

def get_news(ticker: str, max_items: int = 8) -> list[dict]:
    """Retorna manchetes recentes do ticker via yfinance."""
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        result = []
        for item in news[:max_items]:
            content = item.get("content", {})
            result.append({
                "title": content.get("title", item.get("title", "")),
                "published": content.get("pubDate", item.get("providerPublishTime", "")),
                "summary": content.get("summary", item.get("summary", "")),
                "url": content.get("canonicalUrl", {}).get("url", "") if isinstance(content.get("canonicalUrl"), dict) else item.get("link", ""),
                "source": content.get("provider", {}).get("displayName", "") if isinstance(content.get("provider"), dict) else "",
            })
        return result
    except Exception as e:
        return [{"error": str(e)}]


# ── SEC EDGAR ─────────────────────────────────────────────────────────────────

EDGAR_HEADERS = {
    "User-Agent": "PremarketAgent contact@example.com",
    "Accept": "application/json",
}

TICKER_TO_CIK = {
    "MU": "0000723125",
    "SMCI": "0000310158",
}


def search_edgar_filings(ticker: str, form_type: str = "8-K", count: int = 5) -> list[dict]:
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
        for i, (form, date, acc, doc) in enumerate(zip(forms, dates, accessions, descriptions)):
            if form_type and form != form_type:
                continue
            acc_clean = acc.replace("-", "")
            results.append({
                "form": form,
                "date": date,
                "accession": acc,
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc}",
            })
            if len(results) >= count:
                break
        return results or [{"info": f"Nenhum filing {form_type} recente encontrado para {ticker}"}]
    except Exception as e:
        return [{"error": str(e)}]


def read_filing(url: str, max_chars: int = 4000) -> str:
    """Lê o conteúdo de um filing da SEC (truncado)."""
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text
        # Strip HTML tags minimally
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] + (" [TRUNCADO]" if len(text) > max_chars else "")
    except Exception as e:
        return f"[erro ao ler filing: {e}]"


# ── Memória / observações ─────────────────────────────────────────────────────

def save_observation(ticker: str, summary: str, sentiment: str, price: float | None = None) -> dict:
    """
    Salva observação do dia via API interna.
    Retorna o resultado da gravação.
    """
    try:
        api_url = os.environ.get("INTERNAL_API_URL", "http://localhost:5000")
        today = datetime.date.today().isoformat()
        payload = {
            "ticker": ticker.upper(),
            "date": today,
            "summary": summary,
            "sentiment": sentiment.lower(),
            "priceAtObservation": price,
        }
        r = requests.post(
            f"{api_url}/api/observations/internal",
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return {"saved": True, "ticker": ticker, "sentiment": sentiment}
    except Exception as e:
        return {"saved": False, "error": str(e)}


# ── Definição das ferramentas para a API da Anthropic ────────────────────────

TOOLS = [
    {
        "name": "get_stock_data",
        "description": "Retorna dados de cotação e pré-mercado de um ticker (preço, variação, volume, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Símbolo do ativo, ex: MU, SMCI"}
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
                "summary": {"type": "string", "description": "Resumo factual em 2-4 frases"},
                "sentiment": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                    "description": "Sentimento geral com base nos dados coletados",
                },
                "price": {"type": "number", "description": "Preço atual ou pré-mercado do ativo"},
            },
            "required": ["ticker", "summary", "sentiment"],
        },
    },
]

DISPATCH = {
    "get_stock_data": get_stock_data,
    "get_news": get_news,
    "search_edgar_filings": search_edgar_filings,
    "read_filing": read_filing,
    "save_observation": save_observation,
}
