"""Risk management calculator — standalone subprocess (no project imports)."""
import sys, json, re
import yfinance as yf
import pandas as pd

def sanitize_ticker(t: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9.\-]", "", str(t)).upper()
    if len(clean) < 1 or len(clean) > 10:
        raise ValueError(f"Invalid ticker: {t!r}")
    return clean

def position_size(account_size: float, risk_pct: float, entry: float, stop: float) -> dict:
    if entry <= 0 or stop <= 0 or entry == stop:
        return {"error": "entry and stop must be positive and different"}
    risk_amount = account_size * (risk_pct / 100)
    risk_per_share = abs(entry - stop)
    shares = risk_amount / risk_per_share
    position_value = shares * entry
    return {
        "shares": round(shares, 4),
        "positionValue": round(position_value, 2),
        "riskAmount": round(risk_amount, 2),
        "riskPerShare": round(risk_per_share, 4),
        "accountPct": round(position_value / account_size * 100, 2),
    }

def risk_reward(entry: float, stop: float, target: float) -> dict:
    if entry <= 0 or stop <= 0 or target <= 0:
        return {"error": "all prices must be positive"}
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return {"error": "entry and stop cannot be equal"}
    ratio = reward / risk
    return {
        "risk": round(risk, 4),
        "reward": round(reward, 4),
        "ratio": round(ratio, 2),
        "favorable": ratio >= 2.0,
    }

def stop_distance(ticker: str, period: str = "3mo", atr_multiplier: float = 2.0) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if df.empty or len(df) < 15:
            return {"error": "Insufficient data"}
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        current_price = float(close.iloc[-1])
        stop = current_price - atr_multiplier * atr
        return {
            "ticker": ticker,
            "currentPrice": round(current_price, 2),
            "atr14": round(float(atr), 4),
            "atrMultiplier": atr_multiplier,
            "suggestedStop": round(float(stop), 2),
            "stopDistancePct": round(atr_multiplier * float(atr) / current_price * 100, 2),
        }
    except Exception as e:
        return {"error": str(e)}

def portfolio_exposure(positions: list) -> dict:
    total_invested = sum(float(p.get("investedAmount", 0)) for p in positions)
    tickers = [p["ticker"] for p in positions if p.get("ticker")]
    sector_map: dict[str, float] = {}
    ticker_pcts = []

    for p in positions:
        ticker = p.get("ticker", "")
        invested = float(p.get("investedAmount", 0))
        pct = (invested / total_invested * 100) if total_invested > 0 else 0
        ticker_pcts.append({
            "ticker": ticker,
            "investedAmount": invested,
            "pct": round(pct, 2),
        })

    max_single = max((t["pct"] for t in ticker_pcts), default=0)
    concentration_risk = "HIGH" if max_single > 30 else "MEDIUM" if max_single > 15 else "LOW"

    return {
        "totalPositions": len(positions),
        "totalInvested": round(total_invested, 2),
        "tickers": ticker_pcts,
        "maxSinglePositionPct": round(max_single, 2),
        "concentrationRisk": concentration_risk,
    }

def correlation(tickers: list, period: str = "6mo") -> dict:
    """Correlacao de Pearson entre os retornos diarios dos tickers informados.
    Objetivo: expor concentracao de risco "escondida" -- posicoes dolarizadas
    de forma diversificada podem estar todas apostando na mesma coisa se os
    retornos sao altamente correlacionados (comum numa cesta de
    semicondutores/IA)."""
    try:
        clean = []
        seen = set()
        for t in tickers:
            try:
                s = sanitize_ticker(t)
            except ValueError:
                continue
            if s not in seen:
                seen.add(s)
                clean.append(s)
        if len(clean) < 2:
            return {"error": "Precisa de pelo menos 2 tickers válidos"}

        data = yf.download(clean, period=period, interval="1d", auto_adjust=True, progress=False)
        closes = data["Close"] if "Close" in data else data
        if hasattr(closes, "columns") is False:
            return {"error": "Dados insuficientes"}

        returns = closes.pct_change().dropna(how="all")
        available = [t for t in clean if t in returns.columns and returns[t].notna().sum() >= 20]
        if len(available) < 2:
            return {"error": "Dados insuficientes para calcular correlação"}

        corr = returns[available].corr(min_periods=20)

        matrix = [[
            round(float(corr.loc[a, b]), 3) if not pd.isna(corr.loc[a, b]) else None
            for b in available
        ] for a in available]

        pairs = []
        for i, a in enumerate(available):
            for b in available[i + 1:]:
                v = corr.loc[a, b]
                if pd.isna(v):
                    continue
                pairs.append({"a": a, "b": b, "correlation": round(float(v), 3)})
        pairs.sort(key=lambda p: -abs(p["correlation"]))

        high = [p for p in pairs if abs(p["correlation"]) >= 0.8]

        skipped = [t for t in clean if t not in available]
        return {
            "tickers": available,
            "matrix": matrix,
            "pairs": pairs,
            "highCorrelationPairs": high,
            "skipped": skipped,
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    action = args.get("action")
    if action == "position_size":
        result = position_size(
            float(args["accountSize"]), float(args["riskPct"]),
            float(args["entry"]), float(args["stop"]),
        )
    elif action == "risk_reward":
        result = risk_reward(float(args["entry"]), float(args["stop"]), float(args["target"]))
    elif action == "stop_distance":
        result = stop_distance(
            args["ticker"],
            args.get("period", "3mo"),
            float(args.get("atrMultiplier", 2.0)),
        )
    elif action == "portfolio_exposure":
        result = portfolio_exposure(args.get("positions", []))
    elif action == "correlation":
        result = correlation(args.get("tickers", []), args.get("period", "6mo"))
    else:
        result = {"error": f"Unknown action: {action}"}
    print(json.dumps(result))
