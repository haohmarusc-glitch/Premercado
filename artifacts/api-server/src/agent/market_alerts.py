# market_alerts.py
# -----------------------------------------------------------------------------
# Modulo de alertas de mercado para o agente de monitoramento (NASDAQ).
# Encaixa no projeto existente: usa yfinance e aceita as manchetes do get_news.
#
# IMPORTANTE: isto e' uma ferramenta de MONITORAMENTO/observacao, nao um
# sistema de recomendacao ou execucao de ordens. Nada aqui e' conselho
# financeiro.
# -----------------------------------------------------------------------------

from __future__ import annotations

import datetime as dt
import json
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

import pandas as pd
import yfinance as yf


# =============================================================================
# CONFIGURACAO
# =============================================================================

BELLWETHERS = ["AVGO", "NVDA", "TSM", "SOXX", "SMH", "^IXIC"]
INTL_MEMORY_PEERS = ["000660.KS", "005930.KS"]
YIELD_TICKER = "^TNX"

# Calendario macro 2026. FOMC = datas OFICIAIS (dia da decisao, 2o dia da reuniao).
MACRO_EVENTS: dict[str, list[str]] = {
    "FOMC": [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    ],
    "CPI":  ["2026-06-10", "2026-07-14"],
    "JOBS": ["2026-07-02"],
    "PPI":  ["2026-07-15"],
}

# Palavras-chave geopoliticas / regulatorias
GEO_KEYWORDS: dict[str, list[str]] = {
    "controle de exportacao/China": [
        "export control", "export controls", "bis ", "commerce department",
        "h20", "h200", "blackwell", "china ban", "chip ban", "license required",
        "entity list", "smuggling", "controle de exportacao", "restricao",
    ],
    "antitruste/regulatorio": [
        "antitrust", "monopoly", "doj ", "ftc", "lawsuit", "probe", "investigation",
        "dma", "digital markets act", "fine", "breakup", "subpoena",
        "antitruste", "processo", "investigacao", "multa",
    ],
    "tarifas/comercio": [
        "tariff", "tariffs", "section 232", "trade war", "import duty",
        "sanction", "tarifa", "tarifas", "sancao",
    ],
}

# Circuit breakers (S&P 500)
CB_TICKER    = "^GSPC"
CB_LEVEL1    = -7.0
CB_LEVEL2    = -13.0
CB_LEVEL3    = -20.0
CB_APPROACH  = -5.0

# SEC / EDGAR
SEC_USER_AGENT    = "Jefferson Investor jefferson@example.com"
EDGAR_LOOKBACK_DAYS = 5
FORM4_CLUSTER     = 3
FORM4_PARSE_MAX   = 10
FORM4_BUY_CODE    = "P"
FORM4_SELL_CODE   = "S"
EDGAR_FORMS = {
    "8-K": "evento material (guidance, contrato, troca de executivo, etc.)",
    "4":   "transacao de insider (compra/venda de dirigente)",
}

# Limiares
PEER_DROP_PCT       = -4.0
INTL_DROP_PCT       = -4.0
YIELD_LEVEL         = 4.5
RSI_OVERBOUGHT      = 75.0
ABOVE_200DMA_PCT    = 25.0
NEAR_52W_HIGH_PCT   = 3.0
EARNINGS_WINDOW_DAYS = 7
VOLUME_SPIKE_MULT   = 2.0
GAP_PCT             = 5.0
MACRO_WINDOW_DAYS   = 1

DOWNGRADE_KW = [
    "downgrade", "cuts price target", "lowers price target", "lowered to",
    "underperform", "sell rating", "initiates sell", "rebaixa", "corta preco-alvo",
]
POSITIVE_KW = [
    "approval", "qualified", "supplier", "wins", "deal", "contract", "record",
    "beats", "raises guidance", "raised guidance", "upgrade", "qualificada",
]


# =============================================================================
# ESTRUTURA DO ALERTA
# =============================================================================

class Severity(str, Enum):
    INFO    = "info"
    ATENCAO = "atencao"
    CRITICO = "critico"


class Category(str, Enum):
    SETOR   = "setor"
    MACRO   = "macro"
    TECNICO = "tecnico"
    EMPRESA = "empresa"
    NOTICIA = "noticia"


@dataclass
class Alert:
    ticker:    str
    category:  Category
    severity:  Severity
    title:     str
    detail:    str
    value:     Optional[float] = None
    timestamp: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d

    def __str__(self) -> str:
        return f"[{self.severity.value.upper():8}] {self.ticker:7} | {self.title} :: {self.detail}"


# =============================================================================
# HELPERS DE DADOS (cache por execucao)
# =============================================================================

_HIST_CACHE: dict[str, pd.DataFrame] = {}


def _history(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    key = f"{ticker}:{period}"
    if key in _HIST_CACHE:
        return _HIST_CACHE[key]
    try:
        df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        if df is None or df.empty:
            return None
        _HIST_CACHE[key] = df
        return df
    except Exception as e:
        print(f"[market_alerts] erro ao baixar {ticker}: {e}")
        return None


def _day_change_pct(ticker: str) -> Optional[float]:
    df = _history(ticker, period="5d")
    if df is None or len(df) < 2:
        return None
    prev, last = df["Close"].iloc[-2], df["Close"].iloc[-1]
    return round((last / prev - 1) * 100, 2)


def _gap_pct(ticker: str) -> Optional[float]:
    df = _history(ticker, period="5d")
    if df is None or len(df) < 2:
        return None
    prev_close, today_open = df["Close"].iloc[-2], df["Open"].iloc[-1]
    return round((today_open / prev_close - 1) * 100, 2)


def _rsi(ticker: str, period: int = 14) -> Optional[float]:
    df = _history(ticker, period="6mo")
    if df is None or len(df) < period + 1:
        return None
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, pd.NA)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return None if pd.isna(val) else round(float(val), 1)


def _dist_from_200dma_pct(ticker: str) -> Optional[float]:
    df = _history(ticker, period="1y")
    if df is None or len(df) < 200:
        return None
    ma200 = df["Close"].rolling(200).mean().iloc[-1]
    last  = df["Close"].iloc[-1]
    if pd.isna(ma200) or ma200 == 0:
        return None
    return round((last / ma200 - 1) * 100, 1)


def _dist_from_52w_high_pct(ticker: str) -> Optional[float]:
    df = _history(ticker, period="1y")
    if df is None or df.empty:
        return None
    high = df["High"].max()
    last = df["Close"].iloc[-1]
    if high == 0:
        return None
    return round((last / high - 1) * 100, 1)


def _volume_spike(ticker: str) -> Optional[float]:
    df = _history(ticker, period="2mo")
    if df is None or len(df) < 31:
        return None
    today_vol = df["Volume"].iloc[-1]
    avg_vol   = df["Volume"].iloc[-31:-1].mean()
    if avg_vol == 0:
        return None
    return round(today_vol / avg_vol, 2)


def _next_earnings_date(ticker: str) -> Optional[dt.date]:
    tk = yf.Ticker(ticker)
    try:
        ed = tk.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            today  = pd.Timestamp.now(tz=ed.index.tz)
            future = ed.index[ed.index >= today]
            if len(future) > 0:
                return future.min().date()
    except Exception:
        pass
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if val:
                d = val[0] if isinstance(val, (list, tuple)) else val
                if isinstance(d, dt.datetime):
                    return d.date()
                if isinstance(d, dt.date):
                    return d
    except Exception:
        pass
    return None


def _normalize_headlines(headlines) -> list[str]:
    """Aceita qualquer formato do get_news e devolve lista de strings."""
    if not headlines:
        return []
    if isinstance(headlines, str):
        return [headlines]
    out: list[str] = []
    for h in headlines:
        if isinstance(h, str):
            out.append(h)
        elif isinstance(h, dict):
            for k in ("title", "headline", "summary", "text", "titulo"):
                if h.get(k):
                    out.append(str(h[k]))
                    break
    return out


# --- SEC / EDGAR -------------------------------------------------------------

_CIK_CACHE: dict[str, str] = {}


def _ticker_to_cik(ticker: str, user_agent: str = SEC_USER_AGENT) -> Optional[str]:
    if not _CIK_CACHE:
        url = "https://www.sec.gov/files/company_tickers.json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for row in data.values():
                _CIK_CACHE[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
        except Exception as e:
            print(f"[market_alerts][edgar] erro ao baixar mapa CIK: {e}")
            return None
    return _CIK_CACHE.get(ticker.upper())


def _fetch_edgar_recent(ticker: str, user_agent: str = SEC_USER_AGENT) -> list[dict]:
    cik = _ticker_to_cik(ticker, user_agent)
    if cik is None:
        return []
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[market_alerts][edgar] erro ao buscar filings de {ticker}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms  = recent.get("form", [])
    dates  = recent.get("filingDate", [])
    accs   = recent.get("accessionNumber", [])
    docs   = recent.get("primaryDocument", [])
    out = []
    for i in range(len(forms)):
        out.append({
            "form":            forms[i],
            "filingDate":      dates[i] if i < len(dates) else "",
            "accessionNumber": accs[i]  if i < len(accs)  else "",
            "primaryDocument": docs[i]  if i < len(docs)  else "",
        })
    return out


def _normalize_filings(filings) -> list[dict]:
    out = []
    for f in filings or []:
        if not isinstance(f, dict):
            continue
        form = f.get("form") or f.get("form_type") or f.get("type") or ""
        date = (f.get("filingDate") or f.get("filing_date") or f.get("date") or "")
        doc  = (f.get("primaryDocument") or f.get("primary_document")
                or f.get("document") or "")
        out.append({"form": str(form), "filingDate": str(date),
                    "accessionNumber": f.get("accessionNumber", ""),
                    "primaryDocument": str(doc)})
    return out


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _first_value(elem) -> Optional[str]:
    for e in elem.iter():
        if _local(e.tag) == "value" and e.text:
            return e.text.strip()
    return None


def _form4_doc_url(cik: str, accession: str, primary_doc: str) -> Optional[str]:
    """Monta a URL do XML bruto do Form 4 no EDGAR.

    O campo primaryDocument às vezes vem com prefixo XSLT do viewer da SEC
    (ex: 'xslF345X06/primarydocument.xml'). Removemos para chegar ao XML puro.
    """
    if not (accession and primary_doc):
        return None
    doc_name   = primary_doc.split("/")[-1]   # strip XSLT/subdir prefix
    cik_int    = str(int(cik))
    acc_nodash = accession.replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
            f"{acc_nodash}/{doc_name}")


def _parse_form4(url: str, user_agent: str = SEC_USER_AGENT) -> Optional[dict]:
    """Le o XML do Form 4 e resume compras (P) e vendas (S) em mercado aberto."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[market_alerts][form4] erro ao parsear {url}: {e}")
        return None

    res = {"buy_shares": 0.0, "buy_value": 0.0,
           "sell_shares": 0.0, "sell_value": 0.0,
           "owner": None, "codes": Counter()}

    for e in root.iter():
        if _local(e.tag) == "rptOwnerName" and e.text:
            res["owner"] = e.text.strip()
            break

    for tx in root.iter():
        if _local(tx.tag) != "nonDerivativeTransaction":
            continue
        code = shares = price = None
        for sub in tx.iter():
            name = _local(sub.tag)
            if name == "transactionCode" and sub.text:
                code = sub.text.strip().upper()
            elif name == "transactionShares":
                v = _first_value(sub)
                shares = float(v) if v else None
            elif name == "transactionPricePerShare":
                v = _first_value(sub)
                price = float(v) if v else None
        if not code:
            continue
        res["codes"][code] += 1
        val = (shares or 0) * (price or 0)
        if code == FORM4_BUY_CODE:
            res["buy_shares"] += (shares or 0)
            res["buy_value"]  += val
        elif code == FORM4_SELL_CODE:
            res["sell_shares"] += (shares or 0)
            res["sell_value"]  += val
    return res


# =============================================================================
# CHECKS
# =============================================================================

def check_peer_contagion() -> list[Alert]:
    alerts: list[Alert] = []
    for t in BELLWETHERS:
        chg = _day_change_pct(t)
        if chg is not None and chg <= PEER_DROP_PCT:
            alerts.append(Alert(
                ticker=t, category=Category.SETOR, severity=Severity.CRITICO,
                title="Contagio de setor",
                detail=f"{t} caiu {chg}% hoje. Risco sistemico para os ativos de chips/IA.",
                value=chg,
            ))
    return alerts


def check_intl_peers() -> list[Alert]:
    alerts: list[Alert] = []
    for t in INTL_MEMORY_PEERS:
        chg = _day_change_pct(t)
        if chg is not None and chg <= INTL_DROP_PCT:
            alerts.append(Alert(
                ticker=t, category=Category.SETOR, severity=Severity.ATENCAO,
                title="Pressao no pregao asiatico (memoria)",
                detail=f"{t} caiu {chg}%. Possivel pressao em MU no pre-mercado dos EUA.",
                value=chg,
            ))
    return alerts


def check_macro_triggers(today: Optional[dt.date] = None) -> list[Alert]:
    today = today or dt.date.today()
    alerts: list[Alert] = []
    ja_avisado: set[tuple[str, str]] = set()

    descr = {
        "FOMC": "Decisao de juros do Fed. Maior gatilho de volatilidade do mes; "
                "evitar conclusoes fortes.",
        "CPI":  "Inflacao ao consumidor. Surpresa alta -> juros sobem -> pressao "
                "em nomes de multiplo alto.",
        "PPI":  "Inflacao ao produtor. Antecede pressao de custos/margens.",
        "JOBS": "Relatorio de empregos. Payroll forte -> juros sobem -> risco para "
                "acoes de crescimento.",
    }
    sev = {"FOMC": Severity.CRITICO, "CPI": Severity.ATENCAO,
           "PPI":  Severity.ATENCAO, "JOBS": Severity.ATENCAO}

    for tipo, datas in MACRO_EVENTS.items():
        for ds in datas:
            try:
                d = dt.date.fromisoformat(ds)
            except ValueError:
                continue
            delta = (d - today).days
            if 0 <= delta <= MACRO_WINDOW_DAYS:
                quando = "hoje" if delta == 0 else f"em {delta} dia(s)"
                alerts.append(Alert(
                    ticker="MACRO", category=Category.MACRO,
                    severity=sev.get(tipo, Severity.ATENCAO),
                    title=f"Evento macro: {tipo}",
                    detail=f"{tipo} {quando} ({ds}). {descr.get(tipo, '')}",
                ))
                ja_avisado.add((tipo, ds))

    # Payroll por heuristica (1a sexta do mes)
    if today.weekday() == 4 and today.day <= 7:
        if not any(t == "JOBS" for t, _ in ja_avisado):
            alerts.append(Alert(
                ticker="MACRO", category=Category.MACRO, severity=Severity.ATENCAO,
                title="Evento macro: JOBS",
                detail=f"Provavel dia de Payroll ({today.isoformat()}). {descr['JOBS']}",
            ))

    df = _history(YIELD_TICKER, period="5d")
    if df is not None and not df.empty:
        y = float(df["Close"].iloc[-1])
        if y > 20:
            y = y / 10
        if y >= YIELD_LEVEL:
            alerts.append(Alert(
                ticker=YIELD_TICKER, category=Category.MACRO, severity=Severity.ATENCAO,
                title="Juro de 10 anos elevado",
                detail=f"10y em ~{y:.2f}% (limiar {YIELD_LEVEL}%). Pressiona valuation de "
                       f"acoes de crescimento/multiplo alto.",
                value=round(y, 2),
            ))
    return alerts


def check_overbought(ticker: str) -> list[Alert]:
    alerts: list[Alert] = []

    rsi = _rsi(ticker)
    if rsi is not None and rsi >= RSI_OVERBOUGHT:
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
            title="Sobrecomprado (RSI)",
            detail=f"RSI(14) = {rsi}. Esticado; risco de realizacao de lucro.",
            value=rsi,
        ))

    dist200 = _dist_from_200dma_pct(ticker)
    if dist200 is not None and dist200 >= ABOVE_200DMA_PCT:
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.INFO,
            title="Muito acima da media de 200d",
            detail=f"Preco {dist200}% acima da MM200. Distancia historicamente insustentavel.",
            value=dist200,
        ))

    dist52 = _dist_from_52w_high_pct(ticker)
    if dist52 is not None and dist52 >= -NEAR_52W_HIGH_PCT:
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.INFO,
            title="Proximo da maxima de 52 semanas",
            detail=f"A {abs(dist52)}% da maxima de 52s. Pouca margem; sensivel a noticia ruim.",
            value=dist52,
        ))
    return alerts


def check_volume_gap(ticker: str) -> list[Alert]:
    alerts: list[Alert] = []

    vmult = _volume_spike(ticker)
    if vmult is not None and vmult >= VOLUME_SPIKE_MULT:
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
            title="Volume anomalo",
            detail=f"Volume {vmult}x a media de 30d. Investigar noticia/filing.",
            value=vmult,
        ))

    gap = _gap_pct(ticker)
    if gap is not None and abs(gap) >= GAP_PCT:
        sev = Severity.CRITICO if abs(gap) >= GAP_PCT * 1.5 else Severity.ATENCAO
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=sev,
            title="Gap de abertura",
            detail=f"Abriu {gap:+}% vs fechamento anterior. Reacao a evento.",
            value=gap,
        ))
    return alerts


def check_earnings_proximity(ticker: str, today: Optional[dt.date] = None) -> list[Alert]:
    today = today or dt.date.today()
    d = _next_earnings_date(ticker)
    if d is None:
        return []
    delta = (d - today).days
    if 0 <= delta <= EARNINGS_WINDOW_DAYS:
        return [Alert(
            ticker=ticker, category=Category.EMPRESA, severity=Severity.ATENCAO,
            title="Resultado proximo",
            detail=f"Earnings em {delta} dia(s) ({d.isoformat()}). Volatilidade alta; "
                   f"evitar conclusoes fortes ate o report.",
            value=float(delta),
        )]
    return []


def check_analyst_changes(ticker: str, headlines) -> list[Alert]:
    alerts: list[Alert] = []
    for h in _normalize_headlines(headlines):
        low = h.lower()
        if any(kw in low for kw in DOWNGRADE_KW):
            alerts.append(Alert(
                ticker=ticker, category=Category.NOTICIA, severity=Severity.CRITICO,
                title="Mudanca de rating (negativa)",
                detail=f'Manchete: "{h.strip()[:120]}"',
            ))
    return alerts


def check_sell_the_news(ticker: str, headlines,
                         day_change_pct: Optional[float] = None) -> list[Alert]:
    """Noticia positiva + preco caindo = 'priced in' / exaustao."""
    if day_change_pct is None:
        day_change_pct = _day_change_pct(ticker)
    if day_change_pct is None or day_change_pct >= 0:
        return []
    for h in _normalize_headlines(headlines):
        low = h.lower()
        if any(kw in low for kw in POSITIVE_KW):
            return [Alert(
                ticker=ticker, category=Category.NOTICIA, severity=Severity.ATENCAO,
                title="Sell-the-news (noticia boa, preco caindo)",
                detail=f"Preco {day_change_pct}% apesar de manchete positiva: "
                       f'"{h.strip()[:100]}". Possivel exaustao / ja precificado.',
                value=day_change_pct,
            )]
    return []


def check_geopolitical_news(ticker: str, headlines) -> list[Alert]:
    """Controle de exportacao/China, antitruste/regulatorio, tarifas."""
    alerts: list[Alert] = []
    for h in _normalize_headlines(headlines):
        low = h.lower()
        for categoria, kws in GEO_KEYWORDS.items():
            if any(kw in low for kw in kws):
                alerts.append(Alert(
                    ticker=ticker, category=Category.NOTICIA, severity=Severity.ATENCAO,
                    title=f"Risco geopolitico/regulatorio ({categoria})",
                    detail=f'Manchete: "{h.strip()[:120]}"',
                ))
                break
    return alerts


def check_trading_halt(ticker: str, include_market: bool = True) -> list[Alert]:
    """Circuit breaker de mercado (S&P) e possivel halt da acao."""
    alerts: list[Alert] = []

    chg = _day_change_pct(CB_TICKER) if include_market else None
    if chg is not None:
        if chg <= CB_LEVEL3:
            nivel, txt = 3, "fecha o pregao pelo resto do dia"
            sev = Severity.CRITICO
        elif chg <= CB_LEVEL2:
            nivel, txt = 2, "halt de 15 min (se antes das 15h25 ET)"
            sev = Severity.CRITICO
        elif chg <= CB_LEVEL1:
            nivel, txt = 1, "halt de 15 min (se antes das 15h25 ET)"
            sev = Severity.CRITICO
        elif chg <= CB_APPROACH:
            nivel, txt = 0, "aproximando-se do Nivel 1 (-7%)"
            sev = Severity.ATENCAO
        else:
            nivel = None
        if nivel is not None:
            rotulo = "aproximacao" if nivel == 0 else f"Nivel {nivel}"
            alerts.append(Alert(
                ticker=CB_TICKER, category=Category.MACRO, severity=sev,
                title=f"Circuit breaker de mercado ({rotulo})",
                detail=f"S&P 500 em {chg}% no dia. {txt}. Precos podem nao refletir "
                       f"negociacao real durante halts.",
                value=chg,
            ))

    try:
        intraday = yf.Ticker(ticker).history(period="1d", interval="1m")
        if intraday is not None and len(intraday) >= 3:
            ult = intraday.tail(3)
            vol_zero      = (ult["Volume"] == 0).all()
            preco_travado = ult["Close"].nunique() == 1
            if vol_zero and preco_travado:
                alerts.append(Alert(
                    ticker=ticker, category=Category.TECNICO, severity=Severity.CRITICO,
                    title="Possivel halt da acao",
                    detail="Ultimos minutos sem volume e com preco travado. Negociacao "
                           "pode estar pausada (LULD). Nao tratar o ultimo preco como real.",
                ))
    except Exception as e:
        print(f"[market_alerts] intraday {ticker} indisponivel: {e}")

    return alerts


def check_edgar_events(ticker: str, filings=None, today: Optional[dt.date] = None,
                        lookback_days: int = EDGAR_LOOKBACK_DAYS,
                        parse_form4: bool = True,
                        user_agent: str = SEC_USER_AGENT) -> list[Alert]:
    """8-K novo (evento material) e Form 4 (compra vs venda de insider)."""
    today  = today or dt.date.today()
    cutoff = today - dt.timedelta(days=lookback_days)
    cik    = _ticker_to_cik(ticker, user_agent)

    if filings is not None:
        registros = _normalize_filings(filings)
    else:
        registros = _fetch_edgar_recent(ticker, user_agent)

    alerts:     list[Alert] = []
    form4_refs: list[dict]  = []

    for r in registros:
        form     = (r.get("form") or "").upper()
        date_str = r.get("filingDate") or ""
        try:
            fdate = dt.date.fromisoformat(date_str[:10])
        except (ValueError, TypeError):
            continue
        if fdate < cutoff:
            continue

        if form == "8-K":
            alerts.append(Alert(
                ticker=ticker, category=Category.EMPRESA, severity=Severity.CRITICO,
                title="Novo 8-K (evento material)",
                detail=f"8-K protocolado em {fdate.isoformat()}: {EDGAR_FORMS['8-K']} Ler o conteudo.",
            ))
        elif form == "4":
            form4_refs.append({
                "date":      fdate.isoformat(),
                "accession": r.get("accessionNumber", ""),
                "doc":       r.get("primaryDocument", ""),
            })

    if not form4_refs:
        return alerts

    parsed = []
    if parse_form4 and cik:
        for ref in form4_refs[:FORM4_PARSE_MAX]:
            url = _form4_doc_url(cik, ref["accession"], ref["doc"])
            if not url:
                continue
            p = _parse_form4(url, user_agent)
            if p:
                parsed.append(p)

    if parsed:
        buy_val  = sum(p["buy_value"]  for p in parsed)
        sell_val = sum(p["sell_value"] for p in parsed)
        buyers   = sorted({p["owner"] for p in parsed if p["buy_value"]  > 0 and p["owner"]})
        sellers  = sorted({p["owner"] for p in parsed if p["sell_value"] > 0 and p["owner"]})

        if buy_val > 0:
            sev = Severity.CRITICO if (len(buyers) >= 2 or buy_val >= 1_000_000) \
                  else Severity.ATENCAO
            alerts.append(Alert(
                ticker=ticker, category=Category.EMPRESA, severity=sev,
                title="Compra de insider (Form 4) - sinal positivo",
                detail=f"~US$ {buy_val:,.0f} comprados em mercado aberto por "
                       f"{len(buyers)} dirigente(s): {', '.join(buyers[:3])}. "
                       f"Compra de insider e' historicamente um sinal de convicao.",
                value=round(buy_val, 0),
            ))
        if sell_val > 0:
            sev = Severity.ATENCAO if len(sellers) >= FORM4_CLUSTER else Severity.INFO
            alerts.append(Alert(
                ticker=ticker, category=Category.EMPRESA, severity=sev,
                title="Venda de insider (Form 4)",
                detail=f"~US$ {sell_val:,.0f} vendidos por {len(sellers)} dirigente(s): "
                       f"{', '.join(sellers[:3])}. Venda pode ser rotineira (plano "
                       f"programado); cluster amplo e' que pesa.",
                value=round(sell_val, 0),
            ))
        if buy_val == 0 and sell_val == 0:
            alerts.append(Alert(
                ticker=ticker, category=Category.EMPRESA, severity=Severity.INFO,
                title="Form 4 sem compra/venda em mercado aberto",
                detail=f"{len(parsed)} Form 4 recente(s), mas so com transacoes "
                       f"rotineiras (premios/exercicios/impostos).",
                value=float(len(parsed)),
            ))
        return alerts

    # Fallback: nao conseguiu parsear
    datas = [ref["date"] for ref in form4_refs]
    if len(datas) >= FORM4_CLUSTER:
        alerts.append(Alert(
            ticker=ticker, category=Category.EMPRESA, severity=Severity.ATENCAO,
            title="Cluster de Form 4 (insiders)",
            detail=f"{len(datas)} transacoes de insider nos ultimos {lookback_days} "
                   f"dias ({', '.join(datas[:5])}). Nao foi possivel classificar "
                   f"compra/venda; abrir o filing.",
            value=float(len(datas)),
        ))
    else:
        alerts.append(Alert(
            ticker=ticker, category=Category.EMPRESA, severity=Severity.INFO,
            title="Movimentacao de insider (Form 4)",
            detail=f"{len(datas)} Form 4 recente(s): {', '.join(datas)}.",
            value=float(len(datas)),
        ))
    return alerts


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def run_all_alerts(tickers: list[str],
                   headlines_by_ticker: Optional[dict[str, list]] = None,
                   filings_by_ticker:   Optional[dict[str, list]] = None,
                   check_edgar: bool = True,
                   check_halts: bool = True,
                   today: Optional[dt.date] = None) -> list[Alert]:
    """Roda todos os checks e devolve a lista ordenada por severidade.

    headlines_by_ticker : dict {ticker: [manchetes...]} do get_news.
    filings_by_ticker   : dict {ticker: [filings...]}  do search_edgar_filings.
                          Se None e check_edgar=True, busca direto na API da SEC.
    check_edgar         : ligar/desligar o check de EDGAR.
    check_halts         : ligar/desligar circuit breaker + halt intraday.
    """
    headlines_by_ticker = headlines_by_ticker or {}
    filings_by_ticker   = filings_by_ticker   or {}
    today = today or dt.date.today()
    alerts: list[Alert] = []

    alerts += check_peer_contagion()
    alerts += check_intl_peers()
    alerts += check_macro_triggers(today)

    for i, t in enumerate(tickers):
        heads = headlines_by_ticker.get(t, [])
        alerts += check_overbought(t)
        alerts += check_volume_gap(t)
        alerts += check_earnings_proximity(t, today)
        alerts += check_analyst_changes(t, heads)
        alerts += check_sell_the_news(t, heads)
        alerts += check_geopolitical_news(t, heads)
        if check_halts:
            alerts += check_trading_halt(t, include_market=(i == 0))
        if check_edgar:
            alerts += check_edgar_events(t, filings=filings_by_ticker.get(t), today=today)

    order = {Severity.CRITICO: 0, Severity.ATENCAO: 1, Severity.INFO: 2}
    alerts.sort(key=lambda a: order[a.severity])
    return alerts


def alerts_to_prompt_block(alerts: list[Alert]) -> str:
    """Formata os alertas como texto para injetar no system prompt do Claude."""
    if not alerts:
        return "ALERTAS DE MERCADO: nenhum gatilho ativo no momento."
    linhas = ["ALERTAS DE MERCADO ATIVOS (considere ao analisar cada ativo):"]
    for a in alerts:
        linhas.append(f"- [{a.severity.value}/{a.category.value}] {a.ticker}: "
                      f"{a.title} -- {a.detail}")
    return "\n".join(linhas)
