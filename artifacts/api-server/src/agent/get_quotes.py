#!/usr/bin/env python3
"""
Lightweight quote fetcher using yfinance fast_info.
Called as: python3 -m agent.get_quotes SYMBOL1 SYMBOL2 ...
Outputs a JSON array to stdout.

Além dos campos do pregão regular (via fast_info), tenta enriquecer com dados
de pré-mercado / after-hours via uma chamada batch ao endpoint de cotações do
Yahoo. Se essa chamada falhar (rate limit, bloqueio), os campos extended ficam
nulos e o restante continua funcionando (fail-open).
"""
import sys
import json
import urllib.request
import yfinance as yf


def _round(v, d=4):
    return round(v, d) if isinstance(v, (int, float)) else None


def fetch_extended(symbols: list[str]) -> dict:
    """Busca marketState e preços de pré/pós-mercado em uma única requisição."""
    out: dict = {}
    if not symbols:
        return out
    try:
        url = (
            "https://query1.finance.yahoo.com/v7/finance/quote?symbols="
            + ",".join(symbols)
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        for item in data.get("quoteResponse", {}).get("result", []):
            sym = item.get("symbol")
            if not sym:
                continue
            out[sym] = {
                "marketState": item.get("marketState"),
                "preMarketPrice": _round(item.get("preMarketPrice")),
                "preMarketChangePct": _round(item.get("preMarketChangePercent")),
                "postMarketPrice": _round(item.get("postMarketPrice")),
                "postMarketChangePct": _round(item.get("postMarketChangePercent")),
            }
    except Exception:
        pass  # fail-open: sem dados extended
    return out


def fetch_quote(symbol: str, ext: dict) -> dict:
    e = ext.get(symbol, {})
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info

        price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)
        open_ = getattr(fi, "open", None)
        day_high = getattr(fi, "day_high", None)
        day_low = getattr(fi, "day_low", None)
        volume = getattr(fi, "last_volume", None)
        market_cap = getattr(fi, "market_cap", None)

        change = None
        change_pct = None
        if price is not None and prev_close is not None and prev_close != 0:
            change = round(price - prev_close, 4)
            change_pct = round((price - prev_close) / prev_close * 100, 4)

        return {
            "symbol": symbol,
            "price": round(price, 4) if price is not None else None,
            "change": change,
            "changePct": change_pct,
            "open": round(open_, 4) if open_ is not None else None,
            "previousClose": round(prev_close, 4) if prev_close is not None else None,
            "dayHigh": round(day_high, 4) if day_high is not None else None,
            "dayLow": round(day_low, 4) if day_low is not None else None,
            "volume": int(volume) if volume is not None else None,
            "marketCap": int(market_cap) if market_cap is not None else None,
            "marketState": e.get("marketState"),
            "preMarketPrice": e.get("preMarketPrice"),
            "preMarketChangePct": e.get("preMarketChangePct"),
            "postMarketPrice": e.get("postMarketPrice"),
            "postMarketChangePct": e.get("postMarketChangePct"),
            "error": None,
        }
    except Exception as ex:
        return {
            "symbol": symbol,
            "price": None,
            "change": None,
            "changePct": None,
            "open": None,
            "previousClose": None,
            "dayHigh": None,
            "dayLow": None,
            "volume": None,
            "marketCap": None,
            "marketState": e.get("marketState"),
            "preMarketPrice": e.get("preMarketPrice"),
            "preMarketChangePct": e.get("preMarketChangePct"),
            "postMarketPrice": e.get("postMarketPrice"),
            "postMarketChangePct": e.get("postMarketChangePct"),
            "error": str(ex),
        }


if __name__ == "__main__":
    symbols = sys.argv[1:]
    if not symbols:
        print("[]")
        sys.exit(0)

    ext = fetch_extended(symbols)
    results = [fetch_quote(s, ext) for s in symbols]
    print(json.dumps(results))
