import sys, json, math
import yfinance as yf
import pandas as pd

def run_backtest(ticker, start, end, strategy="rsi",
                 position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005):
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
        rs = gain / loss.where(loss != 0, other=float("nan"))
        rsi = 100 - (100 / (1 + rs))
        buy_signal = rsi.fillna(50) < 30
        sell_signal = rsi.fillna(50) > 70
    else:  # ma_cross
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        buy_signal = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
        sell_signal = (ma20 < ma50) & (ma20.shift(1) >= ma50.shift(1))

    initial_capital = 10000.0
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_date = ""
    trades = []

    def fill_price(price, is_buy):
        slip = price * slippage_pct * (1 if is_buy else -1)
        return price + slip

    for i in range(len(close)):
        raw_price = float(close.iloc[i])
        date = str(close.index[i])[:10]

        if buy_signal.iloc[i] and position == 0 and capital > 0:
            exec_price = fill_price(raw_price, True)
            invest = capital * position_fraction
            commission = invest * commission_pct
            position = (invest - commission) / exec_price
            entry_price = exec_price
            entry_date = date
            capital -= invest

        elif sell_signal.iloc[i] and position > 0:
            exec_price = fill_price(raw_price, False)
            proceeds = position * exec_price
            commission = proceeds * commission_pct
            net_proceeds = proceeds - commission
            pnl = (exec_price - entry_price) / entry_price * 100
            trades.append({
                "entryDate": entry_date, "exitDate": date,
                "entryPrice": round(entry_price, 2), "exitPrice": round(exec_price, 2),
                "pnl": round(pnl, 2), "win": pnl > 0, "closedOpen": False,
            })
            capital += net_proceeds
            position = 0.0

    # Close any open position at period end
    if position > 0:
        last_price = float(close.iloc[-1])
        exec_price = fill_price(last_price, False)
        proceeds = position * exec_price
        commission = proceeds * commission_pct
        net_proceeds = proceeds - commission
        pnl = (exec_price - entry_price) / entry_price * 100
        trades.append({
            "entryDate": entry_date, "exitDate": str(close.index[-1])[:10],
            "entryPrice": round(entry_price, 2), "exitPrice": round(exec_price, 2),
            "pnl": round(pnl, 2), "win": pnl > 0, "closedOpen": True,
        })
        capital += net_proceeds
        position = 0.0

    final_value = capital
    total_return = (final_value - initial_capital) / initial_capital * 100
    bh_return = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100

    days = (close.index[-1] - close.index[0]).days or 1
    years = days / 365.25
    cagr = ((final_value / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    daily_ret = close.pct_change().dropna()
    sharpe = 0.0
    if daily_ret.std() > 0:
        sharpe = round((daily_ret.mean() / daily_ret.std()) * math.sqrt(252), 2)

    cum = (1 + daily_ret).cumprod()
    rolling_max = cum.cummax()
    drawdown = (cum - rolling_max) / rolling_max
    max_drawdown = round(float(drawdown.min()) * 100, 2)

    return {
        "ticker": ticker, "strategy": strategy, "start": start, "end": end,
        "initialCapital": initial_capital, "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2), "buyAndHoldReturn": round(bh_return, 2),
        "cagr": round(cagr, 2), "sharpe": sharpe, "maxDrawdown": max_drawdown,
        "totalTrades": len(trades), "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avgWin": round(avg_win, 2), "avgLoss": round(avg_loss, 2),
        "trades": trades[-30:],
    }

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    result = run_backtest(
        args["ticker"], args["start"], args["end"],
        args.get("strategy", "rsi"),
        float(args.get("positionFraction", 1.0)),
        float(args.get("commissionPct", 0.001)),
        float(args.get("slippagePct", 0.0005)),
    )
    print(json.dumps(result))
