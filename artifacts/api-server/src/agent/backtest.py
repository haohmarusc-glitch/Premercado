import sys, json, math
import yfinance as yf
import pandas as pd

# ── Estrutura de preço e RSI de Wilder: MESMA lógica de get_trend.py
# (price_structure/rsi_wilder) -- ver comentário em _confluence_signals sobre
# por que precisa ser reimplementada aqui em vez de importada.
def _price_structure_at(s: pd.Series, lookback: int = 60, window: int = 3) -> str:
    s = s.iloc[-lookback:].reset_index(drop=True)
    highs, lows = [], []
    for i in range(window, len(s) - window):
        seg = s.iloc[i - window:i + window + 1]
        if s.iloc[i] == seg.max():
            highs.append(float(s.iloc[i]))
        if s.iloc[i] == seg.min():
            lows.append(float(s.iloc[i]))
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1] > highs[-2], lows[-1] > lows[-2]
        lh, ll = highs[-1] < highs[-2], lows[-1] < lows[-2]
        if hh and hl:
            return "alta"
        if lh and ll:
            return "baixa"
    first_third = float(s.iloc[: len(s) // 3].mean())
    last_third = float(s.iloc[-(len(s) // 3):].mean())
    if first_third > 0:
        chg = (last_third - first_third) / first_third
        if chg > 0.04:
            return "alta"
        if chg < -0.04:
            return "baixa"
    return "indefinida"

def _rsi_wilder_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    # avg_loss == 0 (sem perdas no período) -> RSI 100, igual get_trend.py
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi

def _confluence_signals(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Reproduz dia-a-dia o score técnico de get_trend.py (SMA20x50, preço x
    SMA200, estrutura, MACD, ajuste de RSI) SEM a camada de notícias -- a
    fórmula real (`sinal`) só confirma compra/venda nos thresholds fortes
    (score >= 60 / <= -60) quando não há notícia pra confirmar os thresholds
    moderados (25/-25), então backtestar sem notícia é simplesmente aplicar a
    própria fórmula com news_dir neutro, não uma aproximação.

    Reimplementada aqui (não importada de get_trend.py) porque price_structure
    precisa rodar uma vez por dia sobre a janela de 60 pregões terminando
    naquele dia -- os outros indicadores (SMA/MACD/RSI) já vetorizam com
    pandas, mas a estrutura de pivôs não tem equivalente vetorizado simples.
    """
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_hist = ema12 - ema26 - (ema12 - ema26).ewm(span=9).mean()
    rsi = _rsi_wilder_series(close)

    scores = [0] * len(close)
    for i in range(len(close)):
        if i < 60 or pd.isna(sma50.iloc[i]):
            continue  # historico insuficiente pro score fazer sentido
        score = 25 if sma20.iloc[i] > sma50.iloc[i] else -25
        if not pd.isna(sma200.iloc[i]):
            score += 20 if close.iloc[i] > sma200.iloc[i] else -20
        structure = _price_structure_at(close.iloc[: i + 1])
        score += 30 if structure == "alta" else -30 if structure == "baixa" else 0
        score += 15 if macd_hist.iloc[i] > 0 else -15
        r = rsi.iloc[i]
        if not pd.isna(r):
            score += -5 if r > 70 else 5 if r < 30 else 0
        scores[i] = score

    score_series = pd.Series(scores, index=close.index)
    buy_signal = score_series >= 60
    sell_signal = score_series <= -60
    return buy_signal, sell_signal

def run_backtest(ticker, start, end, strategy="rsi",
                 position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005):
    # Busca com "aquecimento" (~320 dias corridos) antes de `start` pra
    # indicadores de janela longa (SMA200, estrutura de 60 pregões) já
    # estarem válidos no primeiro dia do período pedido -- sem isso, um
    # backtest de "últimos 6 meses" mal teria sinal de confluência (SMA200
    # sozinha já precisa de ~200 pregões de histórico).
    warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=320)).strftime("%Y-%m-%d")
    df = yf.Ticker(ticker).history(start=warmup_start, end=end, interval="1d", auto_adjust=True)
    if df.empty:
        return {"error": "Sem dados para o período"}
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    close_full = df["Close"].dropna()
    if len(close_full) < 50:
        return {"error": "Dados insuficientes (mínimo 50 dias)"}

    if strategy == "rsi":
        delta = close_full.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.where(loss != 0, other=float("nan"))
        rsi = 100 - (100 / (1 + rs))
        buy_signal_full = rsi.fillna(50) < 30
        sell_signal_full = rsi.fillna(50) > 70
    elif strategy == "confluencia":
        buy_signal_full, sell_signal_full = _confluence_signals(close_full)
    else:  # ma_cross
        ma20 = close_full.rolling(20).mean()
        ma50 = close_full.rolling(50).mean()
        buy_signal_full = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
        sell_signal_full = (ma20 < ma50) & (ma20.shift(1) >= ma50.shift(1))

    # Recorta pro período pedido -- os indicadores acima já usaram o
    # aquecimento, a simulação/relatório olha só [start, end].
    start_ts = pd.Timestamp(start)
    naive_index = close_full.index.tz_localize(None) if close_full.index.tz is not None else close_full.index
    mask = naive_index >= start_ts
    close = close_full.loc[mask]
    buy_signal = buy_signal_full.loc[mask]
    sell_signal = sell_signal_full.loc[mask]

    if len(close) < 20:
        return {"error": "Dados insuficientes no período pedido (mínimo 20 dias)"}

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

def run_basket_backtest(tickers, start, end, strategy="confluencia",
                        position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005):
    """Roda run_backtest pra cada ticker da cesta e agrega. Cada ticker usa seu
    próprio capital inicial de $10k independente (não é uma carteira única
    dividida entre eles) -- o objetivo é comparar a estratégia ticker a
    ticker, não simular alocação de portfólio."""
    results = []
    for t in tickers:
        r = run_backtest(t, start, end, strategy, position_fraction, commission_pct, slippage_pct)
        r["ticker"] = t
        results.append(r)

    ok = [r for r in results if "error" not in r]
    failed = [{"ticker": r["ticker"], "error": r["error"]} for r in results if "error" in r]

    if not ok:
        return {"strategy": strategy, "start": start, "end": end, "results": results, "failed": failed}

    total_trades = sum(r["totalTrades"] for r in ok)
    avg_return = sum(r["totalReturn"] for r in ok) / len(ok)
    avg_bh_return = sum(r["buyAndHoldReturn"] for r in ok) / len(ok)
    avg_win_rate = sum(r["winRate"] for r in ok if r["totalTrades"] > 0) / max(1, len([r for r in ok if r["totalTrades"] > 0]))
    beat_buy_hold = sum(1 for r in ok if r["totalReturn"] > r["buyAndHoldReturn"])

    return {
        "strategy": strategy, "start": start, "end": end,
        "tickersRequested": len(tickers), "tickersOk": len(ok),
        "aggregate": {
            "avgTotalReturn": round(avg_return, 2),
            "avgBuyAndHoldReturn": round(avg_bh_return, 2),
            "avgWinRate": round(avg_win_rate, 1),
            "totalTrades": total_trades,
            "beatBuyAndHoldCount": beat_buy_hold,
        },
        "results": sorted(ok, key=lambda r: -r["totalReturn"]),
        "failed": failed,
    }

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    tickers = args.get("tickers")
    if tickers:
        result = run_basket_backtest(
            tickers, args["start"], args["end"],
            args.get("strategy", "confluencia"),
            float(args.get("positionFraction", 1.0)),
            float(args.get("commissionPct", 0.001)),
            float(args.get("slippagePct", 0.0005)),
        )
    else:
        result = run_backtest(
            args["ticker"], args["start"], args["end"],
            args.get("strategy", "rsi"),
            float(args.get("positionFraction", 1.0)),
            float(args.get("commissionPct", 0.001)),
            float(args.get("slippagePct", 0.0005)),
        )
    print(json.dumps(result))
