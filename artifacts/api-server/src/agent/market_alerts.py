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

from .cache import cached


# =============================================================================
# CONFIGURACAO
# =============================================================================

BELLWETHERS = ["AVGO", "NVDA", "TSM", "SOXX", "SMH", "^IXIC"]
INTL_MEMORY_PEERS = ["000660.KS", "005930.KS"]
YIELD_TICKER = "^TNX"

# Mercados que operam antes ou durante o pre-mercado da Nasdaq: Asia overnight,
# Europa em overlap direto, e futuros de indice dos EUA. So contexto -- ver
# get_global_market_snapshot() abaixo, que devolve dado bruto sem pontuacao.
GLOBAL_MARKETS: dict[str, str] = {
    "^N225":    "Nikkei 225 (Japao)",
    "^KS11":    "KOSPI Composite (Coreia)",
    "^HSI":     "Hang Seng (Hong Kong)",
    "^GDAXI":   "DAX (Alemanha)",
    "^FTSE":    "FTSE 100 (Reino Unido)",
    "^FCHI":    "CAC 40 (Franca)",
    "EURUSD=X": "EUR/USD",
    "NQ=F":     "Nasdaq 100 futuros",
    "ES=F":     "S&P 500 futuros",
}

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
    # Inclui terras raras/minerais criticos -- mesmo ator (China) e mesma
    # ferramenta (controle de exportacao), so muda o material. Relevante em
    # especial pra TSLA (imas de motor/baterias) alem de hardware em geral.
    "controle de exportacao/China": [
        "export control", "export controls", "bis ", "commerce department",
        "h20", "h200", "blackwell", "china ban", "chip ban", "license required",
        "entity list", "smuggling", "controle de exportacao", "restricao",
        "rare earth export", "rare earth ban", "china rare earth",
        "critical minerals export", "cobalt export", "lithium export ban",
        "terras raras", "minerais criticos",
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
    # Conflito armado/guerra genérico -- risk-off de curto prazo em toda a
    # cesta, historicamente pouco persistente por si só (ver categoria
    # abaixo pro caso especifico que MAIS importa pra esta cesta).
    "conflito armado/guerra": [
        "war", "invasion", "invades", "airstrike", "air strike", "missile strike",
        "military conflict", "military strike", "attack on", "blockade",
        "declares war", "troops", "guerra", "invasao", "ataque militar",
        "conflito armado", "bloqueio naval", "tropas",
    ],
    # Estreito de Taiwan -- distinto de "conflito armado" generico porque e'
    # risco de cadeia de suprimento DIRETO (nao so risk-off de sentimento)
    # pra qualquer ticker que dependa de fabs da TSMC: NVDA, AVGO, AMD, QCOM,
    # AAPL, ARM, ALAB, CRDO. Ver HARDWARE_EXPOSED_TICKERS em
    # confluence_engine.py pra a mesma lista aplicada ao motor de sinal.
    "estreito de taiwan/cadeia de semicondutores": [
        "taiwan strait", "estreito de taiwan", "invasion of taiwan",
        "invasao de taiwan", "taiwan blockade", "bloqueio de taiwan",
        "pla exercises near taiwan", "china military drill taiwan",
        "taiwan independence crisis",
    ],
    # Ira/Estreito de Ormuz -- distinto de "conflito armado" generico porque
    # e' o gatilho geopolitico mais direto pra CHOQUE DE PETROLEO que existe:
    # Ira e' produtor OPEP e controla o Estreito de Ormuz, por onde passa
    # ~20% do petroleo mundial. Qualquer escalada Ira-Israel/EUA e' o padrao
    # classico de choque de oferta -> inflacao -> juros -> pressao em
    # growth/tech (ver check_macro_regime_risk). Inclui os Houthis do Iemen
    # (apoiados pelo Ira) porque os ataques deles a navios no Mar Vermelho
    # desde 2023-2024 tem o MESMO efeito de risco de rota/seguro maritimo de
    # petroleo, mesmo sem envolver o Estreito de Ormuz diretamente.
    # "ira" sozinho NAO entra aqui de proposito -- ambiguo com "IRA" (conta
    # de aposentadoria americana) e "Irish Republican Army" em manchetes em
    # ingles. "iran" (sem til) e' seguro e especifico o suficiente sozinho.
    "ira/estreito de ormuz": [
        "iran", "strait of hormuz", "estreito de ormuz", "israel iran",
        "irgc", "houthi", "houthis", "red sea shipping attack",
        "ataque ao ira", "ataque ira", "guerra ira", "irã",
    ],
    # Coreia do Norte/peninsula coreana -- risco de cadeia de suprimento
    # DIRETO pra memoria: Samsung + SK Hynix (peers coreanos ja rastreados em
    # INTL_MEMORY_PEERS) sao ~70% do DRAM/NAND global. Uma escalada real na
    # peninsula ameaca essa producao de forma parecida ao risco de Taiwan
    # pra logica/GPU -- afeta MU/SNDK/WDC (concorrentes/expostos ao mesmo
    # mercado) mesmo sem exposicao direta a fab coreana.
    "coreia do norte/peninsula coreana": [
        "north korea", "dprk", "kim jong", "korean peninsula", "dmz",
        "38th parallel", "pyongyang", "north korean missile", "seoul strikes",
        "coreia do norte", "peninsula coreana", "missil norte-coreano",
    ],
    # Independencia do Fed/interferencia politica na politica monetaria --
    # distinto do calendario normal de FOMC/CPI (ja coberto em
    # check_macro_triggers): risco de juros IMPREVISIVEL, fora do processo
    # normal -- mesmo canal (juros) ja identificado como o de maior peso
    # pra growth/tech, mas gatilho que nenhum calendario capta.
    "independencia do fed/politica monetaria": [
        "fed independence", "fires fed chair", "fed chair fired",
        "remove the fed chair", "replace fed chair", "powell fired",
        "political pressure on the fed", "fed chair removal",
        "independencia do fed", "demite presidente do fed",
        "pressao politica sobre o fed",
    ],
    # Rating soberano dos EUA/teto da divida -- outro gatilho pro canal de
    # juros, mecanismo diferente do Fed: downgrade de credito (S&P 2011,
    # Fitch 2023) ou crise de teto da divida geram venda ampla de treasuries,
    # sobe yield, mesma pressao em growth/tech de multiplo alto.
    # Frases sempre qualificadas com "us"/"united states"/"eua" de proposito
    # -- "credit rating downgrade" ou "fitch downgrades" sozinhos bateriam em
    # rebaixamento de rating de QUALQUER empresa, nao so soberano dos EUA.
    "rating soberano eua/teto da divida": [
        "downgrades us credit", "downgrades united states credit",
        "debt ceiling crisis", "us default risk", "sovereign downgrade",
        "fitch downgrades united states", "fitch downgrades the us",
        "moody's downgrades united states", "moody's downgrades us",
        "s&p downgrades united states", "crise do teto da divida",
        "teto da divida americana", "rebaixamento do rating dos eua",
        "rebaixa rating dos estados unidos",
    ],
}

# Categorias de GEO_KEYWORDS com severidade acima do padrao (ATENCAO) -- sao
# riscos de cadeia de suprimento/oferta ou de juros DIRETOS pra esta cesta
# (Taiwan = semicondutores, Ira/Ormuz = petroleo, Coreia = memoria, Fed e
# rating soberano = juros fora do calendario normal), nao so sentimento de
# mercado generico.
GEO_CRITICAL_CATEGORIES = {
    "estreito de taiwan/cadeia de semicondutores",
    "ira/estreito de ormuz",
    "coreia do norte/peninsula coreana",
    "independencia do fed/politica monetaria",
    "rating soberano eua/teto da divida",
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
# Petroleo (WTI) -- nao rastreamos NIVEL de preco (correlacao fraca/instavel
# com Nasdaq), so CHOQUE DE ALTA rapido, que e' a assinatura de um choque de
# oferta (guerra, corte da OPEP) -- o canal real pra growth/tech e' indireto:
# choque de oferta -> pressao de inflacao -> Fed sobe juros -> comprime
# multiplo. Ver check_macro_regime_risk() pra a combinacao com o yield.
OIL_TICKER              = "CL=F"  # WTI futures -- mais liquido/confiavel no yfinance
OIL_SHOCK_LOOKBACK_DAYS = 10
OIL_SHOCK_PCT           = 15.0
RSI_OVERBOUGHT      = 75.0
ABOVE_200DMA_PCT    = 25.0
NEAR_52W_HIGH_PCT   = 3.0
EARNINGS_WINDOW_DAYS = 7
VOLUME_SPIKE_MULT   = 2.0
GAP_PCT             = 5.0  # fallback quando ATR não está disponível (histórico curto)
GAP_ATR_MULT        = 1.5  # gap só é alerta se >= 1.5x a volatilidade média (ATR%) do ativo
MACRO_WINDOW_DAYS   = 1
DEAD_CAT_LOOKBACK_DAYS = 5  # T-5: mesmo dia da semana anterior (5 pregões)
DEAD_CAT_T5_ATR_MULT   = 2.0  # volatilidade de ~5 pregões escala aprox. por sqrt(5) ~= 2.2x a diária

DOWNGRADE_KW = [
    "downgrade", "cuts price target", "lowers price target", "lowered to",
    "underperform", "sell rating", "initiates sell", "rebaixa", "corta preco-alvo",
]
UPGRADE_KW = [
    "upgrade", "raises price target", "raised price target", "raised to",
    "outperform", "buy rating", "initiates buy", "initiates outperform",
    "eleva recomendacao", "eleva preco-alvo",
]
POSITIVE_KW = [
    "approval", "qualified", "supplier", "wins", "deal", "contract", "record",
    "beats", "raises guidance", "raised guidance", "upgrade", "qualificada",
]

# Setup de swing HCC (Warrior Met Coal) -- definido em 01/07/2026
# entrada ~$82-83, stop tecnico $73, alvos $90.60 e $103
HCC_LEVELS: dict[str, tuple[float, str, str]] = {
    "reentry_zone": (
        81.0, "at_or_below",
        "HCC na zona MM200/suporte ($80-81) -- reavaliar entrada do swing.",
    ),
    "resistance_breakout": (
        90.60, "above",
        "HCC rompeu resistencia tecnica $90,60 -- confirmacao de forca, R:R melhora.",
    ),
    "bull_target": (
        103.0, "above",
        "HCC atingiu zona de consenso dos analistas (~$103) -- considerar realizacao parcial.",
    ),
    "technical_stop": (
        73.0, "below",
        "HCC rompeu stop tecnico ($73) -- invalida o setup, risco de continuacao para o cenario bear.",
    ),
}


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


def _pct_change_over(ticker: str, lookback_days: int) -> Optional[float]:
    """Variacao % entre o close de hoje e o close de `lookback_days` pregoes
    atras -- usado pro choque de petroleo (janela curta detecta choque de
    oferta rapido, diferente de _day_change_pct que so olha o ultimo pregao)."""
    df = _history(ticker, period="1mo")
    if df is None or len(df) < lookback_days + 1:
        return None
    then, now = df["Close"].iloc[-1 - lookback_days], df["Close"].iloc[-1]
    if then == 0:
        return None
    return round((now / then - 1) * 100, 2)


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


def _atr_pct(ticker: str, period: int = 14) -> Optional[float]:
    """ATR(14) como % do preço — volatilidade real do ativo, pra calibrar
    limiares de gap/movimento por ticker em vez de um % fixo pra todo mundo
    (ver GAP_ATR_MULT em check_volume_gap)."""
    df = _history(ticker, period="6mo")
    if df is None or len(df) < period + 1:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    last_price = close.iloc[-1]
    if pd.isna(atr) or not last_price:
        return None
    return round(float(atr) / float(last_price) * 100, 2)


def _rsi_overbought_threshold(ticker: str) -> float:
    """Banda de sobrecompra calibrada por volatilidade (ATR%) — mesma regra
    de tools.get_technical_indicators: ativos de ATR alto (NVDA/SMCI/ARM)
    ficam esticados por muito mais tempo que big techs estáveis antes de
    reverter de verdade, então usar RSI_OVERBOUGHT fixo (75) sinaliza
    reversão prematura demais nesses ativos."""
    atr_pct = _atr_pct(ticker)
    if atr_pct is None:
        return RSI_OVERBOUGHT
    return 80.0 if atr_pct >= 6.0 else 75.0


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


def detect_candle_patterns_in_hist(hist: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """Nucleo de deteccao de padroes de candlestick (Doji, Martelo/Enforcado,
    Estrela Cadente/Invertido, Engolfo, Estrela da Manha/Noite), separado de
    tools.detect_candle_patterns para poder ser reaproveitado por
    check_candle_patterns() sobre um historico ja baixado (sem round-trip de
    rede extra). Unica fonte de verdade das regras -- tools.py delega pra cá."""
    o, h, l, c = hist["Open"], hist["High"], hist["Low"], hist["Close"]
    n = len(hist)
    start = max(3, n - lookback)

    def body(i: int) -> float:
        return abs(c.iloc[i] - o.iloc[i])

    def rng(i: int) -> float:
        r = h.iloc[i] - l.iloc[i]
        return r if r > 0 else 1e-9

    def upper_wick(i: int) -> float:
        return h.iloc[i] - max(o.iloc[i], c.iloc[i])

    def lower_wick(i: int) -> float:
        return min(o.iloc[i], c.iloc[i]) - l.iloc[i]

    def is_bullish(i: int) -> bool:
        return c.iloc[i] > o.iloc[i]

    def is_bearish(i: int) -> bool:
        return c.iloc[i] < o.iloc[i]

    def trend_before(i: int) -> str:
        ref = max(0, i - 4)
        return "down" if c.iloc[i - 1] < c.iloc[ref] else "up"

    found: list[dict] = []
    for i in range(start, n):
        date = hist.index[i].strftime("%Y-%m-%d")
        b, r = body(i), rng(i)
        body_pct = b / r

        if body_pct < 0.1:
            found.append({
                "date": date, "pattern": "Doji", "direction": "neutral",
                "note": "Indecisão — corpo minúsculo, pode antecipar reversão.",
            })
        elif lower_wick(i) >= 2 * b and upper_wick(i) <= b * 0.5 and body_pct < 0.4:
            if trend_before(i) == "down":
                found.append({
                    "date": date, "pattern": "Martelo", "direction": "bullish",
                    "note": "Pavio inferior longo após queda — possível reversão para alta.",
                })
            else:
                found.append({
                    "date": date, "pattern": "Enforcado", "direction": "bearish",
                    "note": "Mesma forma do martelo, mas após alta — possível reversão para baixa; confirmar no próximo candle.",
                })
        elif upper_wick(i) >= 2 * b and lower_wick(i) <= b * 0.5 and body_pct < 0.4:
            if trend_before(i) == "up":
                found.append({
                    "date": date, "pattern": "Estrela Cadente", "direction": "bearish",
                    "note": "Pavio superior longo após alta — possível reversão para baixa.",
                })
            else:
                found.append({
                    "date": date, "pattern": "Martelo Invertido", "direction": "bullish",
                    "note": "Mesma forma da estrela cadente, mas após queda — possível reversão para alta; confirmar no próximo candle.",
                })

        if i >= 1:
            if is_bearish(i - 1) and is_bullish(i) and o.iloc[i] <= c.iloc[i - 1] and c.iloc[i] >= o.iloc[i - 1]:
                found.append({
                    "date": date, "pattern": "Engolfo de Alta", "direction": "bullish",
                    "note": "Corpo de alta engole o corpo de baixa do candle anterior — reversão forte.",
                })
            elif is_bullish(i - 1) and is_bearish(i) and o.iloc[i] >= c.iloc[i - 1] and c.iloc[i] <= o.iloc[i - 1]:
                found.append({
                    "date": date, "pattern": "Engolfo de Baixa", "direction": "bearish",
                    "note": "Corpo de baixa engole o corpo de alta do candle anterior — reversão forte.",
                })

        if i >= 2:
            long0 = body(i - 2) / rng(i - 2) > 0.5
            small1 = body(i - 1) / rng(i - 1) < 0.35
            mid0 = (o.iloc[i - 2] + c.iloc[i - 2]) / 2
            if long0 and is_bearish(i - 2) and small1 and is_bullish(i) and c.iloc[i] > mid0:
                found.append({
                    "date": date, "pattern": "Estrela da Manhã", "direction": "bullish",
                    "note": "3 candles: queda forte, indecisão, retomada de alta — reversão clássica de fundo.",
                })
            elif long0 and is_bullish(i - 2) and small1 and is_bearish(i) and c.iloc[i] < mid0:
                found.append({
                    "date": date, "pattern": "Estrela da Noite", "direction": "bearish",
                    "note": "3 candles: alta forte, indecisão, queda — reversão clássica de topo.",
                })

    return found


def check_candle_patterns(ticker: str, lookback: int = 3) -> list[Alert]:
    """Padroes de candlestick nos ultimos `lookback` candles, reaproveitando o
    historico de 6mo que check_overbought/_atr_pct ja deixam no _HIST_CACHE
    (zero chamadas de rede extras)."""
    df = _history(ticker, period="6mo")
    if df is None or len(df) < 10:
        return []
    found = detect_candle_patterns_in_hist(df, lookback=lookback)
    alerts: list[Alert] = []
    for p in found:
        sev = Severity.ATENCAO if p["direction"] in ("bullish", "bearish") else Severity.INFO
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=sev,
            title=f"Padrao de candle: {p['pattern']}",
            detail=f"{p['date']}: {p['note']}",
        ))
    return alerts


def check_dead_cat_bounce(ticker: str, lookback: int = DEAD_CAT_LOOKBACK_DAYS) -> list[Alert]:
    """Compara o movimento de hoje (T-1, mesma janela que check_volume_gap/
    check_sell_the_news ja leem) contra o nivel de `lookback` pregoes atras
    (T-5, "mesmo dia da semana passada") pra distinguir reversao real de
    dead-cat-bounce: uma alta forte hoje que ainda deixa o papel bem abaixo
    do nivel de uma semana atras e' recuperacao dentro de uma queda maior,
    nao reversao de tendencia -- e o inverso pra uma queda forte dentro de
    uma alta maior. So dispara quando as duas janelas apontam em direcoes
    opostas com magnitude relevante (limiar calibrado pelo ATR% do ativo,
    mesma logica de check_volume_gap). Reaproveita o historico de 6mo ja
    cacheado por check_overbought/_atr_pct/check_candle_patterns -- zero
    chamada de rede extra."""
    df = _history(ticker, period="6mo")
    if df is None or len(df) < lookback + 2:
        return []
    close = df["Close"]
    now, t1, t5 = close.iloc[-1], close.iloc[-2], close.iloc[-1 - lookback]
    if t1 == 0 or t5 == 0:
        return []
    chg_t1 = round((now / t1 - 1) * 100, 2)
    chg_t5 = round((now / t5 - 1) * 100, 2)

    atr_pct = _atr_pct(ticker)
    t1_threshold = atr_pct if atr_pct is not None else GAP_PCT / GAP_ATR_MULT
    t5_threshold = t1_threshold * DEAD_CAT_T5_ATR_MULT

    if chg_t1 >= t1_threshold and chg_t5 <= -t5_threshold:
        return [Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
            title="Possivel dead-cat bounce",
            detail=f"Alta de {chg_t1:+.2f}% hoje, mas ainda {chg_t5:+.2f}% vs "
                   f"{lookback} pregoes atras (mesmo dia da semana passada). "
                   f"Pode ser recuperacao tecnica dentro de uma queda maior, nao "
                   f"reversao de tendencia -- cruzar com volume e noticias antes "
                   f"de tratar como confirmacao.",
            value=chg_t1,
        )]
    if chg_t1 <= -t1_threshold and chg_t5 >= t5_threshold:
        return [Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
            title="Possivel realizacao de lucro (nao reversao)",
            detail=f"Queda de {chg_t1:+.2f}% hoje, mas ainda {chg_t5:+.2f}% vs "
                   f"{lookback} pregoes atras (mesmo dia da semana passada). "
                   f"Pode ser realizacao de lucro dentro de uma alta maior, nao "
                   f"reversao de tendencia -- cruzar com volume e noticias antes "
                   f"de tratar como confirmacao.",
            value=chg_t1,
        )]
    return []


@cached("next_earnings:{0}", ttl=21600)
def _next_earnings_date_iso(ticker: str) -> Optional[str]:
    """Retorna string ISO (cache em disco via JSON não preserva objetos date/datetime
    — ver cache.py). None é cacheado normalmente e cobre tanto "sem data futura
    conhecida" quanto tickers sem dados (delistados etc.), evitando repetir as
    chamadas lentas ao yfinance a cada turno/execucao para o mesmo ticker."""
    from . import config
    if config.has_no_earnings_data(ticker):
        return None
    tk = yf.Ticker(ticker)
    try:
        ed = tk.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            today  = pd.Timestamp.now(tz=ed.index.tz)
            future = ed.index[ed.index >= today]
            if len(future) > 0:
                return future.min().date().isoformat()
    except Exception:
        pass
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if val:
                d = val[0] if isinstance(val, (list, tuple)) else val
                if isinstance(d, dt.datetime):
                    return d.date().isoformat()
                if isinstance(d, dt.date):
                    return d.isoformat()
    except Exception:
        pass
    return None


def _next_earnings_date(ticker: str) -> Optional[dt.date]:
    iso = _next_earnings_date_iso(ticker)
    return dt.date.fromisoformat(iso) if iso else None


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
# CONTEXTO GLOBAL (dado bruto -- sem pontuacao/composite)
# =============================================================================

def get_global_market_snapshot() -> dict:
    """
    Variacao % do ultimo pregao disponivel pros mercados listados em
    GLOBAL_MARKETS. Retorna so os numeros -- nao ha pontuacao/composite nem
    classificacao de "risk-on/off" aqui; a leitura de contexto fica a cargo
    de quem consome o retorno. Nao ajuste thresholds de sinal com base nisso
    sem validar via backtest primeiro (mesma licao do ConfluenceEngine).
    """
    items = [
        {"ticker": ticker, "label": label, "changePct": _day_change_pct(ticker)}
        for ticker, label in GLOBAL_MARKETS.items()
    ]
    return {"items": items}


# Validado por backtest real (PRs #54-#61, ver memory doc skhy-ipo-monitoring.md):
# a media DAX+CAC+FTSE (Europa) so bate o buy&hold da Nasdaq liquido de custo
# quando o mercado NAO esta em tendencia de alta clara. O filtro de regime
# (Close vs. propria SMA200) domina SMA100 nos dois regimes testados. Ásia e
# EUR/USD foram testados e descartados (sem sinal, ou pior que ruido).
REGIME_TARGET_TICKER = "^IXIC"
REGIME_SMA_WINDOW = 200
EUROPE_SIGNAL_TICKERS: dict[str, str] = {"^GDAXI": "DAX", "^FCHI": "CAC 40", "^FTSE": "FTSE 100"}


def get_europe_regime_signal() -> dict:
    """
    Sinal validado por backtest real: fora de tendencia de alta (Nasdaq
    abaixo da propria SMA200), a media de variacao diaria de DAX+CAC+FTSE
    tem edge real (liquido de custo de transacao) sobre o retorno
    abertura->fechamento da Nasdaq. Em tendencia de alta, a estrategia perde
    do buy&hold puro -- entao aqui ela fica desligada e a recomendacao vira
    "sem sinal ativo".

    Validado SOMENTE contra ^IXIC (Nasdaq Composite) como alvo, em dois
    regimes historicos (rali 2024-2026 e correcao 2022-2023). NAO foi
    testado em tickers individuais (MU, NVDA, SKHY etc.) -- nao usar como
    sinal de entrada/saida pra uma posicao especifica sem validar antes.
    """
    try:
        df = _history(REGIME_TARGET_TICKER, period="2y")
        if df is None or len(df) < REGIME_SMA_WINDOW + 5:
            return {"error": f"historico insuficiente pra SMA{REGIME_SMA_WINDOW}"}

        sma = df["Close"].rolling(REGIME_SMA_WINDOW).mean()
        last_close = float(df["Close"].iloc[-1])
        last_sma = float(sma.iloc[-1])
        if pd.isna(last_sma):
            return {"error": f"SMA{REGIME_SMA_WINDOW} ainda nao disponivel"}

        in_uptrend = last_close > last_sma
        result: dict = {
            "target": REGIME_TARGET_TICKER,
            "lastClose": round(last_close, 2),
            f"sma{REGIME_SMA_WINDOW}": round(last_sma, 2),
            "regime": "alta" if in_uptrend else "correcao_lateral",
        }

        if in_uptrend:
            result["europeSignal"] = None
            result["recommendation"] = (
                "sem sinal ativo -- mercado em tendencia de alta; buy&hold simples "
                "ja bate essa estrategia nesse regime (ver memory doc)"
            )
            return result

        details = {}
        changes = []
        for ticker, label in EUROPE_SIGNAL_TICKERS.items():
            chg = _day_change_pct(ticker)
            if chg is not None:
                details[label] = chg
                changes.append(chg)
        if not changes:
            result["error"] = "nao consegui buscar nenhum ticker europeu"
            return result

        europe_signal = sum(changes) / len(changes)
        result["europeSignal"] = round(europe_signal, 3)
        result["europeDetails"] = details
        result["recommendation"] = (
            "vies comprado (long) no Nasdaq" if europe_signal > 0
            else "vies vendido (short) no Nasdaq" if europe_signal < 0
            else "sinal neutro -- sem vies"
        )
        return result
    except Exception as e:
        return {"error": str(e)}


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


def check_krx_lead_signal(ticker: str = "SKHY", krx_ticker: str = "000660.KS") -> list[Alert]:
    """SK Hynix negocia na Korea Exchange sob 000660.KS -- a acao original por
    tras do ADR (SKHY). O pregao coreano roda enquanto os EUA dormem, entao o
    movimento overnight dela tende a antecipar o gap de abertura da SKHY na
    Nasdaq (ver .agents/memory/skhy-ipo-monitoring.md). Diferente do
    check_intl_peers (so' baixa, sinal generico de "pressao asiatica" sem
    ligar ao ADR) e do indice amplo KOSPI (get_global_market_snapshot), aqui
    o alerta e' bidirecional e nomeado explicitamente pra SKHY. Chamado so'
    quando o ticker monitorado for SKHY (ver run_all_alerts).

    Limiar calibrado pelo ATR% do proprio 000660.KS (mesma logica de
    GAP_ATR_MULT em check_volume_gap) em vez de um % fixo emprestado de
    INTL_DROP_PCT -- a SK Hynix Korea historicamente tem volatilidade bem
    acima da media (ver memory doc: ~640% em 12 meses ate o pico pre-IPO),
    entao um limiar fixo de 4% tanto sobre-dispara em dias normais quanto
    sub-reage num ativo que rotineiramente se move mais que isso. _atr_pct
    exige _history(krx_ticker, period="6mo"), uma chamada de rede a mais
    (nao coberta pelo cache de 5d que check_intl_peers ja deixa pronto) --
    mas e' uma unica chamada extra por run, so' quando SKHY esta na lista
    monitorada, entao o custo e' pequeno e limitado."""
    chg = _day_change_pct(krx_ticker)
    if chg is None:
        return []
    atr_pct = _atr_pct(krx_ticker)
    threshold = atr_pct * GAP_ATR_MULT if atr_pct is not None else abs(INTL_DROP_PCT)
    if abs(chg) < threshold:
        return []
    direcao = "alta" if chg > 0 else "queda"
    return [Alert(
        ticker=ticker, category=Category.SETOR, severity=Severity.ATENCAO,
        title=f"SK Hynix Korea (KRX) em {direcao} forte",
        detail=f"{krx_ticker} variou {chg:+.2f}% no ultimo pregao coreano "
               f"(limiar {threshold:.1f}%). Por ser a acao original por tras do ADR, "
               f"esse movimento historicamente antecipa o gap de abertura da SKHY "
               f"na Nasdaq -- nao e' garantia, mas e' sinal mais direto que indices "
               f"amplos (KOSPI).",
        value=chg,
    )]


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

    oil_chg = _pct_change_over(OIL_TICKER, OIL_SHOCK_LOOKBACK_DAYS)
    if oil_chg is not None and oil_chg >= OIL_SHOCK_PCT:
        alerts.append(Alert(
            ticker=OIL_TICKER, category=Category.MACRO, severity=Severity.ATENCAO,
            title="Choque de alta no petroleo (WTI)",
            detail=f"WTI +{oil_chg:.1f}% em {OIL_SHOCK_LOOKBACK_DAYS} pregoes "
                   f"(limiar {OIL_SHOCK_PCT}%) -- assinatura tipica de choque de oferta "
                   f"(guerra, corte da OPEP). Canal indireto pra growth/tech: pressiona "
                   f"inflacao -> Fed reage com juros -> comprime multiplo.",
            value=oil_chg,
        ))
    return alerts


def check_macro_regime_risk(headlines_by_ticker: Optional[dict[str, list]] = None) -> list[Alert]:
    """Sinal COMBINADO: juro de 10y elevado + choque no petroleo + manchete de
    conflito armado/geopolitica. Isolado, cada sinal e' ruido comum (o app ja
    alerta cada um separado em check_macro_triggers/check_geopolitical_news) --
    mas a CONVERGENCIA de 2 ou mais ao mesmo tempo e' o padrao que
    historicamente precede correcoes fortes em growth/tech de multiplo alto
    (ex.: 2022, juros subindo + choque de commodities pos-invasao da
    Ucrania). Nao e' recomendacao de posicao, so contexto de regime pro
    agente ponderar."""
    headlines_by_ticker = headlines_by_ticker or {}
    sinais: list[str] = []

    df_y = _history(YIELD_TICKER, period="5d")
    if df_y is not None and not df_y.empty:
        y = float(df_y["Close"].iloc[-1])
        if y > 20:
            y = y / 10
        if y >= YIELD_LEVEL:
            sinais.append(f"juro de 10y em ~{y:.2f}% (limiar {YIELD_LEVEL}%)")

    oil_chg = _pct_change_over(OIL_TICKER, OIL_SHOCK_LOOKBACK_DAYS)
    if oil_chg is not None and oil_chg >= OIL_SHOCK_PCT:
        sinais.append(f"WTI +{oil_chg:.1f}% em {OIL_SHOCK_LOOKBACK_DAYS} pregoes")

    # Guerra generica + as categorias criticas (Taiwan, Ira/Ormuz) -- usa
    # GEO_CRITICAL_CATEGORIES em vez de listar de novo pra nao desalinhar
    # quando uma categoria critica nova for adicionada no futuro.
    geo_categorias_relevantes = GEO_CRITICAL_CATEGORIES | {"conflito armado/guerra"}
    geo_hit: Optional[str] = None
    for heads in headlines_by_ticker.values():
        for h in _normalize_headlines(heads):
            low = h.lower()
            for categoria in geo_categorias_relevantes:
                if any(kw in low for kw in GEO_KEYWORDS[categoria]):
                    geo_hit = f'{categoria} -- "{h.strip()[:90]}"'
                    break
            if geo_hit:
                break
        if geo_hit:
            break
    if geo_hit:
        sinais.append(geo_hit)

    if len(sinais) >= 2:
        return [Alert(
            ticker="MACRO", category=Category.MACRO, severity=Severity.CRITICO,
            title="Regime de risco macro elevado (juros + petroleo + geopolitica)",
            detail="Sinais simultaneos: " + "; ".join(sinais) + ". Combinacao "
                   "historicamente associada a pressao sobre acoes de crescimento/"
                   "multiplo alto -- nao e' recomendacao de posicao, so contexto "
                   "de regime pra ponderar junto com o resto da analise.",
        )]
    return []


def check_hcc_setup(ticker: str = "HCC") -> list[Alert]:
    """Checa os niveis do setup de swing definido para HCC contra o ultimo close.

    So dispara UM alerta por direcao (o nivel mais extremo atingido), para
    evitar alertas duplicados/conflitantes quando o preco rompe mais de um
    nivel de uma vez (ex: gap que passa direto do stop pela zona de reentrada).

    Sem dedup propria (mesmo padrao dos outros checks deste modulo): re-dispara
    a cada execucao enquanto a condicao continuar verdadeira.
    """
    df = _history(ticker, period="5d")
    if df is None or df.empty:
        return []
    last = float(df["Close"].iloc[-1])

    out: list[Alert] = []

    # Lado de baixo: do mais severo (stop) para o menos severo (reentrada)
    downside_order = ["technical_stop", "reentry_zone"]
    for key in downside_order:
        level, cond, msg = HCC_LEVELS[key]
        hit = (
            (cond == "below" and last < level)
            or (cond == "at_or_below" and last <= level)
        )
        if hit:
            out.append(Alert(
                ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
                title=f"Setup HCC: {key}", detail=msg, value=round(last, 2),
            ))
            break  # so o mais extremo do lado de baixo

    # Lado de cima: do mais extremo (alvo bull) para o menos extremo (rompimento)
    upside_order = ["bull_target", "resistance_breakout"]
    for key in upside_order:
        level, cond, msg = HCC_LEVELS[key]
        if cond == "above" and last > level:
            out.append(Alert(
                ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
                title=f"Setup HCC: {key}", detail=msg, value=round(last, 2),
            ))
            break  # so o mais extremo do lado de cima

    return out


def check_overbought(ticker: str) -> list[Alert]:
    alerts: list[Alert] = []

    rsi = _rsi(ticker)
    rsi_threshold = _rsi_overbought_threshold(ticker)
    if rsi is not None and rsi >= rsi_threshold:
        alerts.append(Alert(
            ticker=ticker, category=Category.TECNICO, severity=Severity.ATENCAO,
            title="Sobrecomprado (RSI)",
            detail=f"RSI(14) = {rsi} (limiar {rsi_threshold:.0f}). Esticado; risco de realizacao de lucro.",
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
    if gap is not None:
        atr_pct = _atr_pct(ticker)
        # Limiar por volatilidade real do ativo (ATR%); cai pro fixo só se
        # não houver histórico suficiente pra calcular ATR.
        threshold = atr_pct * GAP_ATR_MULT if atr_pct is not None else GAP_PCT
        if abs(gap) >= threshold:
            sev = Severity.CRITICO if abs(gap) >= threshold * 1.5 else Severity.ATENCAO
            alerts.append(Alert(
                ticker=ticker, category=Category.TECNICO, severity=sev,
                title="Gap de abertura",
                detail=(
                    f"Abriu {gap:+}% vs fechamento anterior "
                    f"(limiar {threshold:.1f}%, ATR {atr_pct:.1f}%). Reacao a evento."
                    if atr_pct is not None
                    else f"Abriu {gap:+}% vs fechamento anterior. Reacao a evento."
                ),
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
        elif any(kw in low for kw in UPGRADE_KW):
            alerts.append(Alert(
                ticker=ticker, category=Category.NOTICIA, severity=Severity.INFO,
                title="Mudanca de rating (positiva)",
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
    """Controle de exportacao/China, antitruste/regulatorio, tarifas, conflito
    armado/guerra e estreito de Taiwan (esta ultima com severidade CRITICO --
    ver GEO_CRITICAL_CATEGORIES)."""
    alerts: list[Alert] = []
    for h in _normalize_headlines(headlines):
        low = h.lower()
        for categoria, kws in GEO_KEYWORDS.items():
            if any(kw in low for kw in kws):
                sev = Severity.CRITICO if categoria in GEO_CRITICAL_CATEGORIES else Severity.ATENCAO
                alerts.append(Alert(
                    ticker=ticker, category=Category.NOTICIA, severity=sev,
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
    alerts += check_macro_regime_risk(headlines_by_ticker)

    for i, t in enumerate(tickers):
        heads = headlines_by_ticker.get(t, [])
        alerts += check_overbought(t)
        alerts += check_volume_gap(t)
        alerts += check_candle_patterns(t)
        alerts += check_dead_cat_bounce(t)
        alerts += check_earnings_proximity(t, today)
        if t == "HCC":
            alerts += check_hcc_setup(t)
        if t == "SKHY":
            alerts += check_krx_lead_signal(t)
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
