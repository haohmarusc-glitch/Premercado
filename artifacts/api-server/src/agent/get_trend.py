"""Análise de tendência por confluência: técnico + estrutura + notícias.

Núcleo técnico (peso maior):
  - Cruzamento de médias (SMA20 vs SMA50, preço vs SMA200)
  - Estrutura de preço (Higher Highs/Higher Lows vs Lower Highs/Lower Lows)
  - MACD histogram e RSI (Wilder)
Camada de notícias (modificador):
  - Sentimento por palavras-chave nas headlines (yfinance), sem custo de LLM.
Filosofia: calculadora, não decisor — expõe os componentes, não dá ordem de trade.

Input (stdin JSON):  {"tickers": ["NVDA", "SMCI"]}
Output (stdout JSON): {"items": [{ticker, trend, score, components, news, confluence}, ...]}
"""
import sys, json, re, os, time
import requests
import yfinance as yf
import pandas as pd

# ── Cache em disco (autocontido: este script roda via spawn, fora do pacote,
#    então não pode importar agent/cache.py que usa import relativo).
#    Mesmo padrão: JSON em /tmp, falha aberta. TTL 30min — tendência sobre
#    candle diário não muda a cada minuto, e o Yahoo rate-limita IP do Replit.
_CACHE_PATH = os.environ.get("TREND_CACHE_PATH", "/tmp/premercado_trend_cache.json")
_TTL_SECONDS = int(os.environ.get("TREND_CACHE_TTL", "1800"))

def _cache_load() -> dict:
    try:
        if os.path.exists(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _cache_save(cache: dict) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass  # disco cheio/sem permissão: segue sem cache

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

# ── Sentimento por palavras-chave (headlines vêm em inglês do yfinance) ──────
POSITIVE = [
    "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
    "record", "upgrade", "upgraded", "outperform", "buy rating", "raises",
    "raised", "strong", "growth", "jumps", "gains", "tops", "exceeds",
    "bullish", "breakout", "wins", "award", "expands", "partnership",
]
NEGATIVE = [
    "miss", "misses", "plunge", "plunges", "sink", "sinks", "fall", "falls",
    "drop", "drops", "downgrade", "downgraded", "underperform", "sell rating",
    "cuts", "cut", "weak", "lawsuit", "probe", "investigation", "recall",
    "bearish", "warning", "warns", "layoffs", "slump", "tumbles", "fraud",
]

# ── Tradução via endpoint gratuito do Google Translate (sem API key) ─────────
# Mesma abordagem de get_news_feed.py — só as headlines destacadas (ao usuário)
# são traduzidas; a classificação de sentimento usa o título original em inglês.
def _translate_join(texts: list[str]) -> list[str]:
    if not texts:
        return texts
    joined = "\n".join(texts)
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "pt-BR", "dt": "t", "q": joined},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        translated = "".join(chunk[0] for chunk in data[0] if chunk and chunk[0])
        lines = translated.split("\n")
        if len(lines) == len(texts):
            return [ln.strip() for ln in lines]
    except Exception:
        pass
    return texts

def news_sentiment(ticker: str, max_items: int = 8) -> dict:
    try:
        news = yf.Ticker(ticker).news or []
    except Exception:
        news = []
    pos = neg = 0
    scored = []
    for item in news[:max_items]:
        content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
        raw_title = str(content.get("title", item.get("title", "")) or "")
        title = raw_title.lower()
        if not title:
            continue
        # Timestamp de publicação (ms) — usado pelos marcadores no gráfico de velas
        ts = None
        pub = content.get("pubDate") or item.get("pubDate") or item.get("providerPublishTime")
        try:
            if isinstance(pub, (int, float)):  # epoch em segundos
                ts = int(pub) * 1000
            elif isinstance(pub, str) and pub:  # ISO 8601, ex: 2026-07-02T14:30:00Z
                ts = int(pd.Timestamp(pub).timestamp() * 1000)
        except Exception:
            ts = None
        p = sum(1 for w in POSITIVE if w in title)
        n = sum(1 for w in NEGATIVE if w in title)
        if p > n:
            pos += 1
            scored.append({"title": raw_title[:120], "tone": "positivo", "ts": ts})
        elif n > p:
            neg += 1
            scored.append({"title": raw_title[:120], "tone": "negativo", "ts": ts})
    total = pos + neg
    if total == 0:
        label, score = "neutro", 0.0
    else:
        score = round((pos - neg) / total, 2)
        label = "positivo" if score > 0.25 else "negativo" if score < -0.25 else "misto"
    # Traduz só as headlines exibidas ao usuário (destaques), pt-BR
    destaques = scored[:4]
    if destaques:
        translated = _translate_join([d["title"] for d in destaques])
        for d, tr in zip(destaques, translated):
            d["title"] = tr
    return {"label": label, "score": score, "positivas": pos, "negativas": neg,
            "analisadas": len(news[:max_items]), "destaques": destaques}

# ── Estrutura de preço: topos/fundos via pivôs simples ───────────────────────
def price_structure(close: pd.Series, lookback: int = 60, window: int = 3) -> str:
    """Detecta HH/HL (alta), LH/LL (baixa) ou indefinida nos últimos `lookback` pregões."""
    s = close.iloc[-lookback:].reset_index(drop=True)
    highs, lows = [], []
    for i in range(window, len(s) - window):
        seg = s.iloc[i - window:i + window + 1]
        if s.iloc[i] == seg.max():
            highs.append(float(s.iloc[i]))
        if s.iloc[i] == seg.min():
            lows.append(float(s.iloc[i]))
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1] > lows[-2]
        lh = highs[-1] < highs[-2]
        ll = lows[-1] < lows[-2]
        if hh and hl:
            return "alta"       # topos e fundos ascendentes
        if lh and ll:
            return "baixa"      # topos e fundos descendentes
    # Fallback: pivôs insuficientes/ambíguos → inclinação normalizada do período
    first_third = float(s.iloc[: len(s) // 3].mean())
    last_third = float(s.iloc[-(len(s) // 3):].mean())
    if first_third > 0:
        chg = (last_third - first_third) / first_third
        if chg > 0.04:
            return "alta"
        if chg < -0.04:
            return "baixa"
    return "indefinida"

# ── RSI de Wilder (igual metodologia já usada no projeto) ────────────────────
def rsi_wilder(close: pd.Series, period: int = 14) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:  # sem perdas no período → RSI máximo (evita divisão por zero)
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(float(100 - 100 / (1 + rs)), 2)

def for_ticker(ticker: str) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist.empty or len(hist) < 60:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.get_level_values(0)
        close = hist["Close"].dropna()
        price = float(close.iloc[-1])

        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_hist = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9).mean()).iloc[-1])

        rsi = rsi_wilder(close)
        structure = price_structure(close)

        # ── Pontuação técnica: -100 (baixa forte) a +100 (alta forte) ────────
        score = 0
        comp = {}

        comp["maCruzamento"] = "alta" if sma20 > sma50 else "baixa"
        score += 25 if sma20 > sma50 else -25

        if sma200 is not None:
            comp["precoVsSma200"] = "acima" if price > sma200 else "abaixo"
            score += 20 if price > sma200 else -20
        else:
            comp["precoVsSma200"] = None

        comp["estrutura"] = structure
        score += 30 if structure == "alta" else -30 if structure == "baixa" else 0

        comp["macd"] = "bullish" if macd_hist > 0 else "bearish"
        score += 15 if macd_hist > 0 else -15

        comp["rsi"] = rsi
        if rsi is not None:
            if rsi > 70:
                comp["rsiNota"] = "sobrecomprado — tendência de alta pode estar esticada"
                score -= 5
            elif rsi < 30:
                comp["rsiNota"] = "sobrevendido — tendência de baixa pode estar esticada"
                score += 5
            else:
                comp["rsiNota"] = "neutro"

        trend = ("alta forte" if score >= 60 else "alta" if score >= 25
                 else "baixa forte" if score <= -60 else "baixa" if score <= -25
                 else "lateral")

        # ── Notícias como modificador de confluência ─────────────────────────
        news = news_sentiment(ticker)
        tech_dir = 1 if score >= 25 else -1 if score <= -25 else 0
        news_dir = 1 if news["label"] == "positivo" else -1 if news["label"] == "negativo" else 0

        if tech_dir == 0:
            confluence = "sem tendência técnica definida"
        elif news_dir == 0:
            confluence = f"{trend} — notícias neutras/mistas (sem confirmação nem divergência)"
        elif tech_dir == news_dir:
            confluence = f"{trend} CONFIRMADA por fluxo de notícias {news['label']}"
        else:
            confluence = f"{trend} com DIVERGÊNCIA — notícias {news['label']} contradizem o técnico (cautela)"

        # ── Sinal objetivo (regras transparentes; ferramenta, não recomendação) ──
        # compra:   técnico de alta forte (>=60) sem notícias contra, ou alta (>=25) confirmada por notícias
        # venda:    espelho para baixa
        # aguardar: lateral, sinais fracos ou divergência técnico × notícias
        if score >= 60 and news_dir >= 0:
            sinal, sinal_motivo = "compra", "técnico de alta forte sem notícias contrárias"
        elif score >= 25 and news_dir > 0:
            sinal, sinal_motivo = "compra", "técnico de alta confirmado por notícias positivas"
        elif score <= -60 and news_dir <= 0:
            sinal, sinal_motivo = "venda", "técnico de baixa forte sem notícias favoráveis"
        elif score <= -25 and news_dir < 0:
            sinal, sinal_motivo = "venda", "técnico de baixa confirmado por notícias negativas"
        else:
            sinal, sinal_motivo = "aguardar", ("divergência técnico × notícias" if tech_dir != 0 and news_dir != 0 and tech_dir != news_dir else "sinais insuficientes")

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "trend": trend,
            "score": score,
            "components": comp,
            "news": news,
            "confluence": confluence,
            "sinal": sinal,
            "sinalMotivo": sinal_motivo,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    tickers = args.get("tickers", [])
    cache = _cache_load()
    now = time.time()
    items = []
    dirty = False
    for t in tickers:
        key = f"trend:{str(t).upper()}"
        entry = cache.get(key)
        # 1) Cache fresco → usa direto, sem tocar no Yahoo
        if entry and (now - entry[0]) < _TTL_SECONDS:
            items.append(entry[1])
            continue
        # 2) Busca ao vivo
        result = for_ticker(t)
        if "error" not in result:
            cache[key] = [now, result]
            dirty = True
            items.append(result)
        elif entry:
            # 3) Stale-if-error: Yahoo falhou (ex: rate limit) mas há resultado
            #    antigo → serve o antigo marcado como stale, melhor que erro.
            stale = dict(entry[1])
            stale["stale"] = True
            stale["staleAgeSeconds"] = int(now - entry[0])
            items.append(stale)
        else:
            items.append(result)
    if dirty:
        _cache_save(cache)
    print(json.dumps({"items": items}, ensure_ascii=False))
