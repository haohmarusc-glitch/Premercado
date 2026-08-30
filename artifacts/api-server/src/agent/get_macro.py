"""Macro snapshot: Fear & Greed index + sector ETF performance — standalone.

Input (stdin JSON):  {}  (no params needed)
Output (stdout JSON): {"fearGreed": {...}, "sectors": [{name, ticker, changePct}, ...]}
"""
import sys, json
import yfinance as yf
from agent.security import friendly_error

from agent.http_retry import SESSION

from agent.sentimento import faixa as _faixa

# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py.
from agent import json_seguro

SECTOR_ETFS = [
    ("Tecnologia", "XLK"), ("Energia", "XLE"), ("Financeiro", "XLF"),
    ("Saúde", "XLV"), ("Consumo Disc.", "XLY"), ("Consumo Básico", "XLP"),
    ("Industrial", "XLI"), ("Materiais", "XLB"), ("Utilidades", "XLU"),
    ("Imobiliário", "XLRE"), ("Comunicação", "XLC"), ("Semicondutores", "SMH"),
]

def fear_greed() -> dict:
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PremarketAgent/1.0)",
            "Referer": "https://edition.cnn.com/",
        }
        r = SESSION.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        current = data.get("fear_and_greed", {})
        score = current.get("score")

        def safe(v):
            return round(v, 1) if isinstance(v, (int, float)) else None

        # As comparações históricas vêm como campos soltos no próprio objeto
        # "fear_and_greed" (previous_close/previous_1_week/...), não dentro de
        # "fear_and_greed_historical" — esse é o timeseries completo (chave
        # "data"), formato diferente do que o nome sugere.
        return {
            "score": round(score, 1) if score is not None else None,
            "ratingEn": current.get("rating", ""),
            # Mesma tabela de faixas do agente (agent/sentimento.py). Ate
            # 26/08/2026 eram duas copias identicas -- latente, nao ativo, mas
            # e' exatamente a forma do defeito da MM50.
            "ratingPt": _faixa(score)["rotulo"],
            # A leitura de 26/08 mostrou 54,9 na prosa e 55,2 no painel: os
            # dois rotulos certos, e 0,3 ponto de deriva atravessando a
            # fronteira dos 55 e trocando "neutro" por "ganância". A distancia
            # ate a borda viaja junto para a tela poder dizer isso.
            "faixa": _faixa(score),
            "prevClose": safe(current.get("previous_close")),
            "oneWeekAgo": safe(current.get("previous_1_week")),
            "oneMonthAgo": safe(current.get("previous_1_month")),
            "oneYearAgo": safe(current.get("previous_1_year")),
        }
    except Exception as e:
        print(f"[get_macro] fear_greed: {e}", file=sys.stderr)
        return {"error": friendly_error(e)}

def sectors() -> list:
    out = []
    tickers = [t for _, t in SECTOR_ETFS]
    try:
        data = yf.download(tickers, period="5d", interval="1d", progress=False, auto_adjust=True)
        closes = data["Close"] if "Close" in data else data
    except Exception:
        closes = None

    for name, tk in SECTOR_ETFS:
        change = None
        try:
            if closes is not None and tk in closes:
                series = closes[tk].dropna()
                if len(series) >= 2:
                    change = round((float(series.iloc[-1]) - float(series.iloc[-2])) / float(series.iloc[-2]) * 100, 2)
        except Exception:
            change = None
        out.append({"name": name, "ticker": tk, "changePct": change})
    out.sort(key=lambda x: (x["changePct"] is None, -(x["changePct"] or 0)))
    return out

if __name__ == "__main__":
    try:
        json.loads(sys.stdin.read() or "{}")
    except Exception:
        pass
    print(json_seguro.dumps({"fearGreed": fear_greed(), "sectors": sectors()}))
