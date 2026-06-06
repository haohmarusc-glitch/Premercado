"""
Ferramentas disponíveis para o agente de pré-mercado.
"""
import datetime
import json
import os

import requests
import yfinance as yf

from . import market_alerts as _ma

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


# ── Gerenciamento de alertas ──────────────────────────────────────────────────

def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


def list_alerts(symbol: str | None = None) -> list[dict]:
    """
    Lista os alertas de preço ativos no sistema.
    Filtra por símbolo se informado.
    """
    try:
        r = requests.get(f"{_api_url()}/api/alerts", timeout=10)
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


def create_alert(symbol: str, condition: str, threshold_pct: float, reason: str) -> dict:
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
            "thresholdPct": str(threshold_pct),
        }
        r = requests.post(f"{_api_url()}/api/alerts", json=payload, timeout=10)
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
        r = requests.delete(f"{_api_url()}/api/alerts/{alert_id}", timeout=10)
        if r.status_code == 204:
            return {"deleted": True, "id": alert_id, "reason": reason}
        return {"deleted": False, "id": alert_id, "status": r.status_code}
    except Exception as e:
        return {"deleted": False, "error": str(e)}


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
    filings_by_ticker   = filings_by_ticker   or {}

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
}
