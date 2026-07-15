"""Recent news headlines per ticker (traduzidas p/ pt-BR) — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA"], "maxItems": 5, "translate": true}
Output (stdout JSON): {"items": [ {ticker, news:[{title, published, summary, source}]}, ... ]}
"""
import sys, json, re
import requests
import yfinance as yf
from security import sanitize_ticker, friendly_error

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

# ── Tradução via endpoint gratuito do Google Translate (sem API key) ──────────
def _translate_join(texts: list[str]) -> list[str]:
    """Traduz uma lista de textos (en->pt-BR) numa única requisição.
    Retorna os originais se algo falhar."""
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

def translate_all(texts: list[str]) -> list[str]:
    """Traduz em lotes respeitando limite de tamanho da URL."""
    out: list[str] = []
    batch: list[str] = []
    size = 0
    for t in texts:
        if size + len(t) > 3500 and batch:
            out.extend(_translate_join(batch))
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    if batch:
        out.extend(_translate_join(batch))
    return out

def for_ticker(ticker: str, max_items: int) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        news = yf.Ticker(ticker).news or []
        out = []
        for item in news[:max_items]:
            content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
            summary = content.get("summary", item.get("summary", "")) or ""
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else ""
            out.append({
                "title": clean_text(content.get("title", item.get("title", ""))),
                "published": content.get("pubDate", item.get("providerPublishTime", "")),
                "summary": clean_text(summary[:280] + ("..." if len(summary) > 280 else "")),
                "source": source,
            })
        return {"ticker": ticker, "news": out}
    except Exception as e:
        print(f"[get_news_feed] {ticker}: {e}", file=sys.stderr)
        return {"ticker": ticker, "error": friendly_error(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    max_items = int(args.get("maxItems", 5))
    do_translate = args.get("translate", True)
    items = [for_ticker(t, max_items) for t in args.get("tickers", [])]

    if do_translate:
        # Junta todos os textos (title + summary) numa lista, traduz em lote e devolve.
        refs = []  # (item_dict, field)
        texts = []
        for it in items:
            for n in it.get("news", []):
                for field in ("title", "summary"):
                    if n.get(field):
                        refs.append((n, field))
                        texts.append(n[field])
        if texts:
            translated = translate_all(texts)
            for (n, field), tr in zip(refs, translated):
                n[field] = tr

    print(json.dumps({"items": items}))
