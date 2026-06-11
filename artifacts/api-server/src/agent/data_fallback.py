import os
import time
from functools import wraps

_cache = {}
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

def _cache_key(prefix, *args):
    return f"{prefix}:{':'.join(str(a) for a in args)}"

def get_from_cache(key, max_age_seconds=None):
    max_age = max_age_seconds or CACHE_TTL_SECONDS
    if key not in _cache:
        return None
    entry = _cache[key]
    if time.time() - entry["timestamp"] > max_age:
        del _cache[key]
        return None
    return entry["value"]

def save_to_cache(key, value, ttl_seconds=None):
    _cache[key] = {"value": value, "timestamp": time.time(), "ttl": ttl_seconds or CACHE_TTL_SECONDS}
    if len(_cache) > 1000:
        now = time.time()
        for k in list(_cache.keys()):
            if now - _cache[k]["timestamp"] > _cache[k].get("ttl", CACHE_TTL_SECONDS):
                del _cache[k]

def cached(ttl_seconds=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = _cache_key(func.__name__, *args, **kwargs)
            cached_value = get_from_cache(key, ttl_seconds)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            if not (isinstance(result, dict) and "error" in result):
                save_to_cache(key, result, ttl_seconds)
            return result
        return wrapper
    return decorator

def get_from_alpha_vantage(ticker, api_key=None):
    key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return {"ticker": ticker, "error": "Alpha Vantage API key nao configurada"}
    try:
        import requests
        url = "https://www.alphavantage.co/query"
        params = {"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": key}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        quote = data.get("Global Quote", {})
        if not quote:
            return {"ticker": ticker, "error": "Sem dados"}
        return {
            "ticker": ticker,
            "last_close": float(quote.get("08. previous close", 0)),
            "regular_market_price": float(quote.get("05. price", 0)),
            "change_pct": float(quote.get("10. change percent", "0%").replace("%", "")),
            "volume": int(quote.get("06. volume", 0)),
            "currency": "USD",
            "source": "alpha_vantage",
        }
    except Exception as e:
        return {"ticker": ticker, "error": f"Alpha Vantage falhou: {e}"}

def get_stock_data_with_fallback(ticker):
    cache_key = _cache_key("stock_data", ticker)
    cached = get_from_cache(cache_key, max_age_seconds=300)
    if cached is not None:
        return {**cached, "source": "cache"}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="5d")
        result = {
            "ticker": ticker,
            "last_close": float(hist["Close"].iloc[-1]) if not hist.empty else None,
            "pre_market_price": info.get("preMarketPrice"),
            "regular_market_price": info.get("regularMarketPrice") or info.get("currentPrice"),
            "change_pct": info.get("regularMarketChangePercent"),
            "volume": info.get("regularMarketVolume"),
            "avg_volume": info.get("averageVolume"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "currency": info.get("currency", "USD"),
            "source": "yfinance",
        }
        save_to_cache(cache_key, result, 300)
        return result
    except Exception:
        fallback = get_from_alpha_vantage(ticker)
        if "error" not in fallback:
            save_to_cache(cache_key, fallback, 300)
        return fallback

def clear_cache():
    _cache.clear()

def get_cache_stats():
    now = time.time()
    valid = sum(1 for v in _cache.values() if now - v["timestamp"] <= v.get("ttl", CACHE_TTL_SECONDS))
    return {"total_entries": len(_cache), "valid_entries": valid, "expired_entries": len(_cache) - valid}
