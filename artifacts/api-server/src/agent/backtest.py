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

def _confluence_signals(close: pd.Series, score_threshold: float = 60.0) -> tuple[pd.Series, pd.Series]:
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
    buy_signal = score_series >= score_threshold
    sell_signal = score_series <= -score_threshold
    return buy_signal, sell_signal

def _fetch_warmed_close(ticker, start, end):
    """Busca o histórico com "aquecimento" (~320 dias corridos) antes de
    `start` pra indicadores de janela longa (SMA200, estrutura de 60
    pregões) já estarem válidos no primeiro dia do período pedido -- sem
    isso, um backtest de "últimos 6 meses" mal teria sinal de confluência
    (SMA200 sozinha já precisa de ~200 pregões de histórico). Separada de
    run_backtest pra análise de sensibilidade buscar os dados UMA vez e
    reusar em cada combinação de parâmetros testada."""
    warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=320)).strftime("%Y-%m-%d")
    df = yf.Ticker(ticker).history(start=warmup_start, end=end, interval="1d", auto_adjust=True)
    if df.empty:
        return None, "Sem dados para o período"
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    close_full = df["Close"].dropna()
    if len(close_full) < 50:
        return None, "Dados insuficientes (mínimo 50 dias)"
    return close_full, None

def _build_signals(close_full, strategy, rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    if strategy == "rsi":
        delta = close_full.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.where(loss != 0, other=float("nan"))
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50) < rsi_oversold, rsi.fillna(50) > rsi_overbought
    elif strategy == "confluencia":
        return _confluence_signals(close_full, score_threshold)
    else:  # ma_cross
        ma20 = close_full.rolling(20).mean()
        ma50 = close_full.rolling(50).mean()
        buy_signal_full = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
        sell_signal_full = (ma20 < ma50) & (ma20.shift(1) >= ma50.shift(1))
        return buy_signal_full, sell_signal_full

def _trim_to_window(close_full, buy_signal_full, sell_signal_full, start):
    # Recorta pro período pedido -- os indicadores já usaram o aquecimento,
    # a simulação/relatório olha só [start, end].
    start_ts = pd.Timestamp(start)
    naive_index = close_full.index.tz_localize(None) if close_full.index.tz is not None else close_full.index
    mask = naive_index >= start_ts
    return close_full.loc[mask], buy_signal_full.loc[mask], sell_signal_full.loc[mask]

def _simulate(ticker, strategy, start, end, close, buy_signal, sell_signal,
              position_fraction, commission_pct, slippage_pct, stop_loss_pct, take_profit_pct):
    if len(close) < 20:
        return {"error": "Dados insuficientes no período pedido (mínimo 20 dias)"}

    initial_capital = 10000.0
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_date = ""
    trades = []
    equity_curve = []  # mark-to-market diário: {date, equity, buyHoldEquity}
    bh_shares = initial_capital / float(close.iloc[0])

    def fill_price(price, is_buy):
        slip = price * slippage_pct * (1 if is_buy else -1)
        return price + slip

    def close_position(exec_price, date, reason):
        nonlocal capital, position, entry_price, entry_date
        proceeds = position * exec_price
        commission = proceeds * commission_pct
        net_proceeds = proceeds - commission
        pnl = (exec_price - entry_price) / entry_price * 100
        trades.append({
            "entryDate": entry_date, "exitDate": date,
            "entryPrice": round(entry_price, 2), "exitPrice": round(exec_price, 2),
            "pnl": round(pnl, 2), "win": pnl > 0, "closedOpen": reason == "period_end",
            "exitReason": reason,
        })
        capital += net_proceeds
        position = 0.0

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

        elif position > 0:
            # SL/TP checam ANTES do sinal (baseado no Close diário -- mesma
            # simplificação do resto do engine, que não usa High/Low
            # intradiário -- então um SL pode não ser pego se o preço só
            # tocou o nível intradia e fechou de volta acima dele).
            stop_hit = stop_loss_pct is not None and raw_price <= entry_price * (1 - stop_loss_pct)
            target_hit = take_profit_pct is not None and raw_price >= entry_price * (1 + take_profit_pct)
            if stop_hit:
                close_position(fill_price(raw_price, False), date, "stop_loss")
            elif target_hit:
                close_position(fill_price(raw_price, False), date, "take_profit")
            elif sell_signal.iloc[i]:
                close_position(fill_price(raw_price, False), date, "signal")

        equity = capital + position * raw_price
        equity_curve.append({
            "date": date,
            "equity": round(equity, 2),
            "buyHoldEquity": round(bh_shares * raw_price, 2),
        })

    # Close any open position at period end
    if position > 0:
        last_price = float(close.iloc[-1])
        close_position(fill_price(last_price, False), str(close.index[-1])[:10], "period_end")
        equity_curve[-1]["equity"] = round(capital, 2)

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

    # Sharpe/drawdown a partir da equity curve DA ESTRATÉGIA (não do
    # buy&hold do ticker -- as duas coisas coincidiam sempre que a
    # estratégia ficava 100% do tempo posicionada, mas divergem sempre que
    # ela fica fora do mercado por um período).
    equity_series = pd.Series([e["equity"] for e in equity_curve])
    daily_ret = equity_series.pct_change().dropna()
    sharpe = 0.0
    if len(daily_ret) > 0 and daily_ret.std() > 0:
        sharpe = round((daily_ret.mean() / daily_ret.std()) * math.sqrt(252), 2)

    rolling_max = equity_series.cummax()
    drawdown = (equity_series - rolling_max) / rolling_max
    max_drawdown = round(float(drawdown.min()) * 100, 2) if len(drawdown) else 0.0

    return {
        "ticker": ticker, "strategy": strategy, "start": start, "end": end,
        "initialCapital": initial_capital, "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2), "buyAndHoldReturn": round(bh_return, 2),
        "cagr": round(cagr, 2), "sharpe": sharpe, "maxDrawdown": max_drawdown,
        "totalTrades": len(trades), "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avgWin": round(avg_win, 2), "avgLoss": round(avg_loss, 2),
        "trades": trades[-30:],
        "equityCurve": equity_curve,
    }

def run_backtest(ticker, start, end, strategy="rsi",
                 position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                 stop_loss_pct=None, take_profit_pct=None,
                 rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    close_full, error = _fetch_warmed_close(ticker, start, end)
    if error:
        return {"error": error}
    buy_signal_full, sell_signal_full = _build_signals(
        close_full, strategy, rsi_oversold, rsi_overbought, score_threshold
    )
    close, buy_signal, sell_signal = _trim_to_window(close_full, buy_signal_full, sell_signal_full, start)
    return _simulate(ticker, strategy, start, end, close, buy_signal, sell_signal,
                     position_fraction, commission_pct, slippage_pct, stop_loss_pct, take_profit_pct)

_SENSITIVITY_METRICS = ["totalReturn", "buyAndHoldReturn", "cagr", "sharpe", "maxDrawdown", "totalTrades", "winRate"]

# Faixas testadas na análise de sensibilidade -- um parâmetro de cada vez a
# partir da configuração base do usuário (não um grid cartesiano completo,
# que explodiria em combinações pra pouco ganho de informação).
_RSI_OVERSOLD_GRID = (20.0, 25.0, 30.0, 35.0, 40.0)
_RSI_OVERBOUGHT_GRID = (60.0, 65.0, 70.0, 75.0, 80.0)
_SCORE_THRESHOLD_GRID = (40.0, 50.0, 60.0, 70.0, 80.0)
_STOP_LOSS_GRID = (0.03, 0.05, 0.08, 0.10, 0.15)
_TAKE_PROFIT_GRID = (0.05, 0.08, 0.10, 0.15, 0.20)

def run_sensitivity_analysis(ticker, start, end, strategy="rsi",
                             position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                             stop_loss_pct=None, take_profit_pct=None,
                             rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    """Testa como o resultado muda ao variar RSI oversold/overbought (ou o
    score threshold, se a estratégia for confluencia) e stop-loss/take-profit,
    UM parâmetro por vez a partir da configuração base -- busca os dados
    históricos UMA única vez (via _fetch_warmed_close) e reaproveita entre
    todas as combinações testadas."""
    close_full, error = _fetch_warmed_close(ticker, start, end)
    if error:
        return {"error": error}

    def run_with(*, rsi_oversold=rsi_oversold, rsi_overbought=rsi_overbought,
                score_threshold=score_threshold, stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct):
        buy_full, sell_full = _build_signals(close_full, strategy, rsi_oversold, rsi_overbought, score_threshold)
        close, buy_signal, sell_signal = _trim_to_window(close_full, buy_full, sell_full, start)
        result = _simulate(ticker, strategy, start, end, close, buy_signal, sell_signal,
                           position_fraction, commission_pct, slippage_pct, stop_loss_pct, take_profit_pct)
        if "error" in result:
            return result
        return {k: result[k] for k in _SENSITIVITY_METRICS}

    variations = []
    if strategy == "rsi":
        for v in _RSI_OVERSOLD_GRID:
            variations.append({"param": "rsiOversold", "value": v, **run_with(rsi_oversold=v)})
        for v in _RSI_OVERBOUGHT_GRID:
            variations.append({"param": "rsiOverbought", "value": v, **run_with(rsi_overbought=v)})
    elif strategy == "confluencia":
        for v in _SCORE_THRESHOLD_GRID:
            variations.append({"param": "scoreThreshold", "value": v, **run_with(score_threshold=v)})

    for v in _STOP_LOSS_GRID:
        variations.append({"param": "stopLossPct", "value": v, **run_with(stop_loss_pct=v)})
    for v in _TAKE_PROFIT_GRID:
        variations.append({"param": "takeProfitPct", "value": v, **run_with(take_profit_pct=v)})

    return {
        "ticker": ticker, "strategy": strategy, "start": start, "end": end,
        "baseline": run_with(),
        "variations": variations,
    }

# Grupos setoriais da cesta -- espelha SECTOR_GROUPS de dashboard.tsx
# (que por sua vez espelha sector_contagion.py). Reimplementado aqui em vez de
# importado porque backtest.py roda como script standalone via subprocess
# (sem contexto de pacote pra um import relativo funcionar), mesma razão de
# _price_structure_at/_rsi_wilder_series acima.
SECTOR_GROUPS = [
    {"key": "memory",       "label": "Memória",      "tickers": ["MU", "SNDK", "WDC"]},
    {"key": "interconnect", "label": "Interconexão", "tickers": ["SMCI", "ALAB", "CRDO", "ANET"]},
    {"key": "power",        "label": "Energia",      "tickers": ["VRT"]},
    {"key": "foundry",      "label": "Fundição",     "tickers": ["TSM", "ASML"]},
]
_SECTOR_KEY_BY_TICKER = {t: g["key"] for g in SECTOR_GROUPS for t in g["tickers"]}
_SECTOR_LABEL_BY_KEY = {g["key"]: g["label"] for g in SECTOR_GROUPS}
_SECTOR_LABEL_BY_KEY["other"] = "Outros"

def _sector_key_for(ticker: str) -> str:
    return _SECTOR_KEY_BY_TICKER.get(ticker, "other")

def _aggregate_results(rs: list) -> dict:
    with_trades = [r for r in rs if r["totalTrades"] > 0]
    return {
        "tickerCount": len(rs),
        "avgTotalReturn": round(sum(r["totalReturn"] for r in rs) / len(rs), 2),
        "avgBuyAndHoldReturn": round(sum(r["buyAndHoldReturn"] for r in rs) / len(rs), 2),
        "avgWinRate": round(sum(r["winRate"] for r in with_trades) / len(with_trades), 1) if with_trades else 0,
        "totalTrades": sum(r["totalTrades"] for r in rs),
        "beatBuyAndHoldCount": sum(1 for r in rs if r["totalReturn"] > r["buyAndHoldReturn"]),
    }

def run_basket_backtest(tickers, start, end, strategy="confluencia",
                        position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                        stop_loss_pct=None, take_profit_pct=None,
                        rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    """Roda run_backtest pra cada ticker da cesta e agrega (geral e por setor).
    Cada ticker usa seu próprio capital inicial de $10k independente (não é
    uma carteira única dividida entre eles) -- o objetivo é comparar a
    estratégia ticker a ticker, não simular alocação de portfólio."""
    results = []
    for t in tickers:
        r = run_backtest(t, start, end, strategy, position_fraction, commission_pct, slippage_pct,
                          stop_loss_pct, take_profit_pct, rsi_oversold, rsi_overbought, score_threshold)
        r["ticker"] = t
        results.append(r)

    ok = [r for r in results if "error" not in r]
    failed = [{"ticker": r["ticker"], "error": r["error"]} for r in results if "error" in r]

    if not ok:
        return {"strategy": strategy, "start": start, "end": end, "results": results, "failed": failed}

    by_sector_groups: dict = {}
    for r in ok:
        by_sector_groups.setdefault(_sector_key_for(r["ticker"]), []).append(r)
    by_sector = sorted(
        [
            {"sector": key, "label": _SECTOR_LABEL_BY_KEY.get(key, key), **_aggregate_results(rs)}
            for key, rs in by_sector_groups.items()
        ],
        key=lambda s: -s["avgTotalReturn"],
    )

    return {
        "strategy": strategy, "start": start, "end": end,
        "tickersRequested": len(tickers), "tickersOk": len(ok),
        "aggregate": _aggregate_results(ok),
        "bySector": by_sector,
        "results": sorted(ok, key=lambda r: -r["totalReturn"]),
        "failed": failed,
    }

def _optional_float(args, key):
    v = args.get(key)
    return float(v) if v not in (None, "") else None

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    tickers = args.get("tickers")
    common = dict(
        position_fraction=float(args.get("positionFraction", 1.0)),
        commission_pct=float(args.get("commissionPct", 0.001)),
        slippage_pct=float(args.get("slippagePct", 0.0005)),
        stop_loss_pct=_optional_float(args, "stopLossPct"),
        take_profit_pct=_optional_float(args, "takeProfitPct"),
        rsi_oversold=float(args.get("rsiOversold", 30.0)),
        rsi_overbought=float(args.get("rsiOverbought", 70.0)),
        score_threshold=float(args.get("scoreThreshold", 60.0)),
    )
    if args.get("mode") == "sensitivity":
        result = run_sensitivity_analysis(args["ticker"], args["start"], args["end"], args.get("strategy", "rsi"), **common)
    elif tickers:
        result = run_basket_backtest(tickers, args["start"], args["end"], args.get("strategy", "confluencia"), **common)
    else:
        result = run_backtest(args["ticker"], args["start"], args["end"], args.get("strategy", "rsi"), **common)
    print(json.dumps(result))
