import sys, json
import yfinance as yf
import pandas as pd

def run_backtest(ticker, start, end, strategy="rsi"):
    df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
    if df.empty:
        return {"error": "Sem dados para o período"}
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    close = df["Close"].dropna()
    if len(close) < 50:
        return {"error": "Dados insuficientes (mínimo 50 dias)"}

    if strategy == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float('nan'))
        rsi = 100 - (100 / (1 + rs))
        buy_signal = rsi < 30
        sell_signal = rsi > 70
    else:  # ma_cross
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        buy_signal = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
        sell_signal = (ma20 < ma50) & (ma20.shift(1) >= ma50.shift(1))

    capital = 10000.0
    position = 0.0
    entry_price = 0.0
    trades = []

    for i in range(len(close)):
        price = float(close.iloc[i])
        date = str(close.index[i])[:10]
        if buy_signal.iloc[i] and position == 0 and capital > 0:
            position = capital / price
            entry_price = price
            capital = 0.0
        elif sell_signal.iloc[i] and position > 0:
            capital = position * price
            pnl = (price - entry_price) / entry_price * 100
            trades.append({"date": date, "price": round(price, 2), "pnl": round(pnl, 2), "win": pnl > 0})
            position = 0.0

    final_value = capital + position * float(close.iloc[-1])
    total_return = (final_value - 10000) / 10000 * 100
    bh_return = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
    wins = [t for t in trades if t["win"]]

    return {
        "ticker": ticker, "strategy": strategy, "start": start, "end": end,
        "initialCapital": 10000, "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2), "buyAndHoldReturn": round(bh_return, 2),
        "totalTrades": len(trades), "winRate": round(len(wins)/len(trades)*100,1) if trades else 0,
        "trades": trades[-20:],
    }

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    result = run_backtest(args["ticker"], args["start"], args["end"], args.get("strategy","rsi"))
    print(json.dumps(result))
