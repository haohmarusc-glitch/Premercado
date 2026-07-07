"""Macro snapshot: Fear & Greed index + sector ETF performance — standalone.

Input (stdin JSON):  {}  (no params needed)
Output (stdout JSON): {"fearGreed": {...}, "sectors": [{name, ticker, changePct}, ...]}
"""
import sys, json
import requests
import yfinance as yf

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
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        current = data.get("fear_and_greed", {})
        score = current.get("score")

        def classify(s):
            if s is None: return "desconhecido"
            if s <= 25: return "medo extremo"
            if s <= 45: return "medo"
            if s <= 55: return "neutro"
            if s <= 75: return "ganância"
            return "ganância extrema"

        def safe(v):
            return round(v, 1) if isinstance(v, (int, float)) else None

        # As comparações históricas vêm como campos soltos no próprio objeto
        # "fear_and_greed" (previous_close/previous_1_week/...), não dentro de
        # "fear_and_greed_historical" — esse é o timeseries completo (chave
        # "data"), formato diferente do que o nome sugere.
        return {
            "score": round(score, 1) if score is not None else None,
            "ratingEn": current.get("rating", ""),
            "ratingPt": classify(score),
            "prevClose": safe(current.get("previous_close")),
            "oneWeekAgo": safe(current.get("previous_1_week")),
            "oneMonthAgo": safe(current.get("previous_1_month")),
            "oneYearAgo": safe(current.get("previous_1_year")),
        }
    except Exception as e:
        return {"error": str(e)}

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
    print(json.dumps({"fearGreed": fear_greed(), "sectors": sectors()}))
