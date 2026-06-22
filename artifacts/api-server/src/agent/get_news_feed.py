"""Recent news headlines per ticker — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA"], "maxItems": 5}
Output (stdout JSON): {"items": [ {ticker, news:[{title, published, summary, source}]}, ... ]}
"""
import sys, json, re
import yfinance as yf

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

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
        return {"ticker": ticker, "error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    max_items = int(args.get("maxItems", 5))
    items = [for_ticker(t, max_items) for t in args.get("tickers", [])]
    print(json.dumps({"items": items}))
