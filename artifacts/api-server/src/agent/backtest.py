import sys, json, math
import numpy as np
import yfinance as yf
import pandas as pd
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


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

# Cópia de get_trend.classificar_cruzamento -- este arquivo roda por spawn
# (routes/backtest.ts o chama por caminho) e importar get_trend traria yfinance,
# market_data_provider e o cache de tendência junto, por uma função pura de dez
# linhas. test_backtest_confluencia.py garante que as duas cópias não divirjam;
# o porquê da regra está no comentário longo em get_trend.py.
CRUZAMENTO_JANELA = 5


def _classificar_cruzamento(sma20, sma50, sma20_antes, sma50_antes):
    acima = sma20 > sma50
    tem_antes = (
        sma20_antes is not None and sma50_antes is not None
        and sma20_antes == sma20_antes and sma50_antes == sma50_antes
        and sma50_antes != 0
    )
    if not tem_antes:
        return ("alta" if acima else "baixa", None, 25 if acima else -25)

    gap = (sma20 - sma50) / sma50
    gap_antes = (sma20_antes - sma50_antes) / sma50_antes
    sobe20 = sma20 > sma20_antes

    if acima:
        if not sobe20 and gap < gap_antes:
            return ("alta",
                    "cruzamento de alta ENFRAQUECENDO — MM20 caindo e encostando "
                    "na MM50; nível e direção discordam",
                    0)
        return ("alta", None, 25)

    if sobe20 and gap > gap_antes:
        return ("baixa",
                "cruzamento de baixa EM REVERSÃO — MM20 abaixo da MM50 mas subindo "
                "e fechando a distância; defasagem da queda anterior, não "
                "confirmação de baixa",
                0)
    return ("baixa", None, -25)


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
    """Reproduz dia-a-dia o score técnico de get_trend.py (SMA20x50 COM
    direção, preço x SMA200, estrutura, MACD, ajuste de RSI) SEM a camada de
    notícias -- a
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
        i_antes = i - CRUZAMENTO_JANELA
        s20a = sma20.iloc[i_antes] if i_antes >= 0 else None
        s50a = sma50.iloc[i_antes] if i_antes >= 0 else None
        _, _, score = _classificar_cruzamento(sma20.iloc[i], sma50.iloc[i], s20a, s50a)
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

def _fetch_warmed_ohlc(ticker, start, end):
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
    # OHLC inteiro, não só Close: desde 20/08/2026 a simulação executa no
    # OPEN do pregão seguinte ao sinal e checa stop/target contra High/Low.
    # Só com Close, o backtest executava num fechamento que ainda não
    # conhecia (look-ahead) e não via stop tocado intradia.
    ohlc_full = df[["Open", "High", "Low", "Close"]].rename(columns=str.lower).dropna(subset=["close"])
    if len(ohlc_full) < 50:
        return None, "Dados insuficientes (mínimo 50 dias)"
    # Open/High/Low podem faltar em dado degradado; caem pro Close do dia --
    # fill mais tardio e sem checagem intradia, mas ainda sem look-ahead.
    for col in ("open", "high", "low"):
        ohlc_full[col] = ohlc_full[col].fillna(ohlc_full["close"])
    return ohlc_full, None

def _build_signals(close_full, strategy, rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    if strategy == "rsi":
        # _rsi_wilder_series, a MESMA conta que a estratégia "confluencia"
        # logo abaixo já usava -- e que get_trend/get_technicals/tools usam ao
        # vivo. Era `rolling(14).mean()` (Cutler) aqui: um backtest da
        # estratégia de RSI media um indicador que o sistema não opera, então
        # o resultado não modelava a estratégia real. Dentro deste arquivo as
        # duas estratégias também discordavam entre si.
        rsi = _rsi_wilder_series(close_full)
        return rsi.fillna(50) < rsi_oversold, rsi.fillna(50) > rsi_overbought
    elif strategy == "confluencia":
        return _confluence_signals(close_full, score_threshold)
    else:  # ma_cross
        ma20 = close_full.rolling(20).mean()
        ma50 = close_full.rolling(50).mean()
        buy_signal_full = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
        sell_signal_full = (ma20 < ma50) & (ma20.shift(1) >= ma50.shift(1))
        return buy_signal_full, sell_signal_full

def _trim_to_window(ohlc_full, buy_signal_full, sell_signal_full, start):
    # Recorta pro período pedido -- os indicadores já usaram o aquecimento,
    # a simulação/relatório olha só [start, end].
    start_ts = pd.Timestamp(start)
    naive_index = ohlc_full.index.tz_localize(None) if ohlc_full.index.tz is not None else ohlc_full.index
    mask = naive_index >= start_ts
    return ohlc_full.loc[mask], buy_signal_full.loc[mask], sell_signal_full.loc[mask]

# ── Métricas de auditoria (20/08/2026) ───────────────────────────────────────
# totalReturn/winRate sozinhos não separam sorte de edge: um retorno bonito
# pode ser um único trade gigante (profit factor conta isso), e um win rate de
# 60% em 8 trades não sustenta nada (o bootstrap conta isso). São métricas do
# RELATÓRIO da régua -- nenhuma entra em decisão de sinal.

_BOOTSTRAP_AMOSTRAS = 2000
_BOOTSTRAP_MIN_TRADES = 10


def _metricas_de_trades(trades: list) -> dict:
    """Profit factor, expectancy e payoff sobre o pnl (%) dos trades fechados.

    profitFactor None quando não há perdas: o valor seria infinito, e
    "infinito" numa tabela vira confiança -- a ausência de perdas em amostra
    pequena é justamente o caso em que menos se sabe."""
    pnls = [t["pnl"] for t in trades]
    if not pnls:
        return {"profitFactor": None, "expectancy": None, "payoff": None}
    ganhos = [p for p in pnls if p > 0]
    perdas = [p for p in pnls if p <= 0]
    soma_perdas = abs(sum(perdas))
    media_ganho = sum(ganhos) / len(ganhos) if ganhos else None
    media_perda = abs(sum(perdas) / len(perdas)) if perdas else None
    return {
        "profitFactor": round(sum(ganhos) / soma_perdas, 2) if soma_perdas > 0 else None,
        "expectancy": round(sum(pnls) / len(pnls), 2),
        "payoff": (round(media_ganho / media_perda, 2)
                   if media_ganho is not None and media_perda not in (None, 0) else None),
    }


def _bootstrap_dos_trades(pnls: list, amostras: int = _BOOTSTRAP_AMOSTRAS,
                          semente: int = 0) -> dict:
    """IC de 95% por bootstrap do composto dos trades e do win rate.

    Responde "quanto deste número é sorte de sequência?": reamostra os trades
    com reposição e olha a distribuição do composto. Semente FIXA de
    propósito -- o IC precisa ser reproduzível para ser auditável (duas
    rodadas com ICs diferentes viram discussão sobre o gerador, não sobre a
    estratégia). O IC é sobre o composto dos pnls por trade (o sinal);
    equity com fração de capital é outra pergunta. Com menos de
    _BOOTSTRAP_MIN_TRADES devolve aviso em vez de um intervalo que fingiria
    sustentação estatística."""
    n = len(pnls)
    if n < _BOOTSTRAP_MIN_TRADES:
        return {"aviso": (f"{n} trades -- amostra pequena demais para intervalo "
                          f"de confiança (mínimo {_BOOTSTRAP_MIN_TRADES})")}
    rng = np.random.default_rng(semente)
    p = np.asarray(pnls, dtype=float) / 100.0
    reamostras = p[rng.integers(0, n, size=(amostras, n))]
    compostos = (np.prod(1.0 + reamostras, axis=1) - 1.0) * 100.0
    win_rates = (reamostras > 0).mean(axis=1) * 100.0

    def _ic(valores):
        lo, hi = np.percentile(valores, [2.5, 97.5])
        return [round(float(lo), 2), round(float(hi), 2)]

    return {"nTrades": n, "amostras": amostras,
            "compostoIc95": _ic(compostos), "winRateIc95": _ic(win_rates)}


def _metricas_da_curva(equity_valores: list) -> dict:
    """Sharpe, Sortino e max drawdown da equity dia a dia -- compartilhada
    entre o backtest por ticker e o de carteira: a régua tem UMA definição de
    cada métrica (o auditor a rederiva de forma independente; duplicar a
    fórmula aqui criaria duas verdades para o mesmo nome)."""
    serie = pd.Series(equity_valores, dtype=float)
    ret = serie.pct_change().dropna()
    sharpe = 0.0
    if len(ret) > 0 and ret.std() > 0:
        sharpe = round((ret.mean() / ret.std()) * math.sqrt(252), 2)
    # Sortino: como o Sharpe, mas punindo só a volatilidade de queda -- uma
    # estratégia que oscila para cima não é "arriscada" no sentido que
    # importa. None quando não houve dia negativo: dividir por zero aqui
    # viraria um número gigante lido como excelência.
    negativos = ret[ret < 0]
    sortino = None
    if len(ret) > 0 and len(negativos) > 0 and negativos.std() > 0:
        sortino = round((ret.mean() / negativos.std()) * math.sqrt(252), 2)
    topo = serie.cummax()
    dd = (serie - topo) / topo
    max_drawdown = round(float(dd.min()) * 100, 2) if len(dd) else 0.0
    return {"sharpe": sharpe, "sortino": sortino, "maxDrawdown": max_drawdown}


def _simulate(ticker, strategy, start, end, ohlc, buy_signal, sell_signal,
              position_fraction, commission_pct, slippage_pct, stop_loss_pct, take_profit_pct):
    if len(ohlc) < 20:
        return {"error": "Dados insuficientes no período pedido (mínimo 20 dias)"}

    closes = ohlc["close"]
    initial_capital = 10000.0
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_date = ""
    trades = []
    equity_curve = []  # mark-to-market diário: {date, equity, buyHoldEquity}
    bh_shares = initial_capital / float(closes.iloc[0])

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

    # O sinal do candle D só é conhecido no FECHAMENTO de D -- até 20/08/2026
    # a simulação comprava nesse mesmo fechamento, ou seja, executava num
    # preço que ainda não existia na hora da decisão (look-ahead, apontado
    # por auditoria externa e confirmado aqui). Agora a decisão de D vira
    # ordem na ABERTURA de D+1, e o sinal do último candle nunca executa --
    # em operação real ele seria a ordem de amanhã.
    pendente = None  # "buy" | "sell", carregado do candle anterior
    for i in range(len(ohlc)):
        open_ = float(ohlc["open"].iloc[i])
        high = float(ohlc["high"].iloc[i])
        low = float(ohlc["low"].iloc[i])
        raw_close = float(closes.iloc[i])
        date = str(ohlc.index[i])[:10]

        # 1) A decisão de ontem executa na abertura de hoje. A saída por
        #    sinal também sai no open -- decidida ontem, executada no
        #    primeiro preço disponível.
        if pendente == "sell" and position > 0:
            close_position(fill_price(open_, False), date, "signal")
        elif pendente == "buy" and position == 0 and capital > 0:
            exec_price = fill_price(open_, True)
            invest = capital * position_fraction
            commission = invest * commission_pct
            position = (invest - commission) / exec_price
            entry_price = exec_price
            entry_date = date
            capital -= invest
        pendente = None

        # 2) Stop/target contra o pregão INTEIRO -- inclusive no próprio dia
        #    da entrada (a entrada é no open; o resto do pregão pode violar o
        #    stop). Gap de abertura através do nível não tem fill no nível:
        #    sai no open. Stop e target tocados no MESMO candle: o OHLC não
        #    diz qual veio primeiro, então assume o STOP -- a política
        #    otimista inflaria o resultado exatamente nos dias mais voláteis.
        if position > 0:
            stop_level = entry_price * (1 - stop_loss_pct) if stop_loss_pct is not None else None
            target_level = entry_price * (1 + take_profit_pct) if take_profit_pct is not None else None
            if stop_level is not None and open_ <= stop_level:
                close_position(fill_price(open_, False), date, "stop_loss")
            elif target_level is not None and open_ >= target_level:
                close_position(fill_price(open_, False), date, "take_profit")
            elif stop_level is not None and low <= stop_level:
                close_position(fill_price(stop_level, False), date, "stop_loss")
            elif target_level is not None and high >= target_level:
                close_position(fill_price(target_level, False), date, "take_profit")

        # 3) O sinal de hoje (calculado no fechamento) vira a ordem de amanhã.
        if buy_signal.iloc[i] and position == 0:
            pendente = "buy"
        elif sell_signal.iloc[i] and position > 0:
            pendente = "sell"

        equity = capital + position * raw_close
        equity_curve.append({
            "date": date,
            "equity": round(equity, 2),
            "buyHoldEquity": round(bh_shares * raw_close, 2),
        })

    # Close any open position at period end
    if position > 0:
        last_price = float(closes.iloc[-1])
        close_position(fill_price(last_price, False), str(ohlc.index[-1])[:10], "period_end")
        equity_curve[-1]["equity"] = round(capital, 2)

    final_value = capital
    total_return = (final_value - initial_capital) / initial_capital * 100
    bh_return = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0]) * 100

    days = (ohlc.index[-1] - ohlc.index[0]).days or 1
    years = days / 365.25
    cagr = ((final_value / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    # Sharpe/Sortino/drawdown a partir da equity curve DA ESTRATÉGIA (não do
    # buy&hold do ticker -- as duas coisas coincidiam sempre que a
    # estratégia ficava 100% do tempo posicionada, mas divergem sempre que
    # ela fica fora do mercado por um período).
    curva_m = _metricas_da_curva([e["equity"] for e in equity_curve])
    max_drawdown = curva_m["maxDrawdown"]

    # Calmar: CAGR / |max drawdown| -- retorno por unidade da pior dor.
    calmar = round(cagr / abs(max_drawdown), 2) if max_drawdown < 0 else None

    return {
        "ticker": ticker, "strategy": strategy, "start": start, "end": end,
        "initialCapital": initial_capital, "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2), "buyAndHoldReturn": round(bh_return, 2),
        "cagr": round(cagr, 2), "sharpe": curva_m["sharpe"], "maxDrawdown": max_drawdown,
        "sortino": curva_m["sortino"], "calmar": calmar,
        **_metricas_de_trades(trades),
        "bootstrap": _bootstrap_dos_trades([t["pnl"] for t in trades]),
        "totalTrades": len(trades), "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avgWin": round(avg_win, 2), "avgLoss": round(avg_loss, 2),
        "trades": trades[-30:],
        "equityCurve": equity_curve,
    }

def run_backtest(ticker, start, end, strategy="rsi",
                 position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                 stop_loss_pct=None, take_profit_pct=None,
                 rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0):
    ohlc_full, error = _fetch_warmed_ohlc(ticker, start, end)
    if error:
        return {"error": error}
    buy_signal_full, sell_signal_full = _build_signals(
        ohlc_full["close"], strategy, rsi_oversold, rsi_overbought, score_threshold
    )
    ohlc, buy_signal, sell_signal = _trim_to_window(ohlc_full, buy_signal_full, sell_signal_full, start)
    return _simulate(ticker, strategy, start, end, ohlc, buy_signal, sell_signal,
                     position_fraction, commission_pct, slippage_pct, stop_loss_pct, take_profit_pct)

_SENSITIVITY_METRICS = ["totalReturn", "buyAndHoldReturn", "cagr", "sharpe", "maxDrawdown",
                        "totalTrades", "winRate", "profitFactor", "expectancy"]

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
    históricos UMA única vez (via _fetch_warmed_ohlc) e reaproveita entre
    todas as combinações testadas."""
    ohlc_full, error = _fetch_warmed_ohlc(ticker, start, end)
    if error:
        return {"error": error}

    def run_with(*, rsi_oversold=rsi_oversold, rsi_overbought=rsi_overbought,
                score_threshold=score_threshold, stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct):
        buy_full, sell_full = _build_signals(ohlc_full["close"], strategy, rsi_oversold, rsi_overbought, score_threshold)
        ohlc, buy_signal, sell_signal = _trim_to_window(ohlc_full, buy_full, sell_full, start)
        result = _simulate(ticker, strategy, start, end, ohlc, buy_signal, sell_signal,
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

# ============================================================================
# CARTEIRA (modo B) -- capital ÚNICO
# ============================================================================
#
# O modo cesta responde "a estratégia tem edge NESTE papel?" dando $10k
# independentes a cada ticker. Esta função responde a pergunta que aquele
# modo estruturalmente não alcança: "o sistema melhora uma CARTEIRA?" --
# com caixa compartilhado, uma entrada só acontece se sobrar capital, e
# cinco compras correlacionadas deixam de ser cinco apostas independentes
# para virar concentração visível (auditoria de 20/08/2026, prioridade 3).
#
# Regras deliberadamente simples e auditáveis (nada de otimização de
# covariância -- sofisticação de sizing sobre sinal sem edge medido é o que
# o arquivamento das specs rejeitou):
#   - cota-alvo por entrada = patrimônio/n, marcado no ÚLTIMO fechamento
#     conhecido (a cota não pode usar o fechamento de hoje, que não existe
#     na hora da abertura); caixa insuficiente entra com o que há; caixa
#     zero derruba a ordem (não fica pendurada).
#   - empate por caixa escasso no mesmo dia: ordem ALFABÉTICA. Arbitrária,
#     mas determinística -- o auditor reproduz.
#   - benchmark: buy & hold equal-weight (1/n no primeiro fechamento de
#     cada ticker na janela, sem rebalanceamento).
#   - execução por ticker: o MESMO contrato do _simulate (D+1 no open,
#     stop/target contra o pregão inteiro, ambos no mesmo candle assume o
#     stop). Com n=1 esta função tem que reproduzir o _simulate -- e o
#     _simulate é auditado pela referência independente: a amarra fecha a
#     cadeia (test_backtest_carteira.py).

_CARTEIRA_CAPITAL = 100_000.0
_CARTEIRA_MAX_TRADES_PAYLOAD = 50


def run_portfolio_backtest(tickers, start, end, strategy="confluencia",
                           commission_pct=0.001, slippage_pct=0.0005,
                           stop_loss_pct=None, take_profit_pct=None,
                           rsi_oversold=30.0, rsi_overbought=70.0,
                           score_threshold=60.0, initial_capital=_CARTEIRA_CAPITAL,
                           _dados=None):
    """`_dados` é a costura de teste: {ticker: (ohlc, buy, sell)} pula o
    fetch/sinais e deixa a suíte exercitar SÓ a mecânica de carteira, sem
    rede -- o caminho real (VPS) sempre passa por _fetch_warmed_ohlc."""
    dados = {}
    failed = []
    if _dados is not None:
        dados = dict(_dados)
    else:
        for t in tickers:
            ohlc_full, erro = _fetch_warmed_ohlc(t, start, end)
            if erro:
                failed.append({"ticker": t, "error": erro})
                continue
            buy_f, sell_f = _build_signals(ohlc_full["close"], strategy,
                                           rsi_oversold, rsi_overbought, score_threshold)
            ohlc, buy, sell = _trim_to_window(ohlc_full, buy_f, sell_f, start)
            if len(ohlc) < 20:
                failed.append({"ticker": t, "error": "Dados insuficientes no período pedido (mínimo 20 dias)"})
                continue
            dados[t] = (ohlc, buy, sell)

    if not dados:
        return {"error": "Nenhum ticker com dados suficientes no período", "failed": failed}

    ordem = sorted(dados)
    n = len(ordem)
    calendario = sorted({d for t in ordem for d in dados[t][0].index})
    linhas = {t: {d: i for i, d in enumerate(dados[t][0].index)} for t in ordem}

    caixa = initial_capital
    pos = {t: {"acoes": 0.0, "preco": None, "dia": None, "aporte": 0.0} for t in ordem}
    pendente = {t: None for t in ordem}
    ultimo_close = {t: None for t in ordem}
    trades = []
    curva = []
    ocupacao = []      # (n_abertas, {tickers abertos}) por dia do calendário
    exposicao_pct = []  # investido/patrimônio por dia

    def _vender(t, preco_bruto, dia, motivo):
        nonlocal caixa
        p = pos[t]
        exec_price = preco_bruto * (1 - slippage_pct)
        bruto = p["acoes"] * exec_price
        liquido = bruto - bruto * commission_pct
        pnl_pct = (exec_price - p["preco"]) / p["preco"] * 100
        trades.append({
            "ticker": t, "entryDate": p["dia"], "exitDate": dia,
            "entryPrice": round(p["preco"], 2), "exitPrice": round(exec_price, 2),
            "pnl": round(pnl_pct, 2), "win": pnl_pct > 0,
            # aporte/recebido em DÓLARES: é o que permite atribuir a
            # contribuição de cada ticker ao resultado da carteira sem
            # reconstruir a sequência de caixa.
            "aporte": round(p["aporte"], 2), "recebido": round(liquido, 2),
            "closedOpen": motivo == "period_end", "exitReason": motivo,
        })
        caixa += liquido
        pos[t] = {"acoes": 0.0, "preco": None, "dia": None, "aporte": 0.0}

    for data in calendario:
        dia = str(data)[:10]
        # Patrimônio de referência da cota, ANTES de qualquer execução do
        # dia, marcado nos fechamentos de ontem.
        marcado = caixa + sum(pos[t]["acoes"] * ultimo_close[t]
                              for t in ordem if pos[t]["acoes"] > 0)
        for t in ordem:
            i = linhas[t].get(data)
            if i is None:
                continue  # ticker sem pregão nesta data: posição fica como está
            ohlc, buy, sell = dados[t]
            abre = float(ohlc["open"].iloc[i])
            alta = float(ohlc["high"].iloc[i])
            baixa = float(ohlc["low"].iloc[i])
            fecha = float(ohlc["close"].iloc[i])

            if pendente[t] == "vender" and pos[t]["acoes"] > 0:
                _vender(t, abre, dia, "signal")
            elif pendente[t] == "comprar" and pos[t]["acoes"] == 0 and caixa > 0:
                aporte = min(caixa, marcado / n)
                if aporte > 0:
                    exec_price = abre * (1 + slippage_pct)
                    pos[t] = {"acoes": (aporte - aporte * commission_pct) / exec_price,
                              "preco": exec_price, "dia": dia, "aporte": aporte}
                    caixa -= aporte
            pendente[t] = None

            if pos[t]["acoes"] > 0:
                piso = pos[t]["preco"] * (1 - stop_loss_pct) if stop_loss_pct is not None else None
                teto = pos[t]["preco"] * (1 + take_profit_pct) if take_profit_pct is not None else None
                if piso is not None and abre <= piso:
                    _vender(t, abre, dia, "stop_loss")
                elif teto is not None and abre >= teto:
                    _vender(t, abre, dia, "take_profit")
                elif piso is not None and baixa <= piso:
                    _vender(t, piso, dia, "stop_loss")
                elif teto is not None and alta >= teto:
                    _vender(t, teto, dia, "take_profit")

            if bool(buy.iloc[i]) and pos[t]["acoes"] == 0:
                pendente[t] = "comprar"
            elif bool(sell.iloc[i]) and pos[t]["acoes"] > 0:
                pendente[t] = "vender"

            ultimo_close[t] = fecha

        investido = sum(pos[t]["acoes"] * ultimo_close[t]
                        for t in ordem if pos[t]["acoes"] > 0)
        patrimonio = caixa + investido
        ocupacao.append((sum(1 for t in ordem if pos[t]["acoes"] > 0),
                         {u for u in ordem if pos[u]["acoes"] > 0}))
        exposicao_pct.append(investido / patrimonio * 100 if patrimonio > 0 else 0.0)
        curva.append({"date": dia, "equity": round(patrimonio, 2)})

    for t in ordem:
        if pos[t]["acoes"] > 0:
            _vender(t, ultimo_close[t], str(calendario[-1])[:10], "period_end")
    curva[-1]["equity"] = round(caixa, 2)

    # Benchmark equal-weight: antes do primeiro pregão de um ticker na
    # janela, a fatia dele conta como caixa parado.
    alocacao = initial_capital / n
    acoes_bh = {t: alocacao / float(dados[t][0]["close"].iloc[0]) for t in ordem}
    ult_bh = {t: None for t in ordem}
    for k, data in enumerate(calendario):
        soma = 0.0
        for t in ordem:
            i = linhas[t].get(data)
            if i is not None:
                ult_bh[t] = float(dados[t][0]["close"].iloc[i])
            soma += acoes_bh[t] * ult_bh[t] if ult_bh[t] is not None else alocacao
        curva[k]["buyHoldEquity"] = round(soma, 2)

    final_value = caixa
    total_return = (final_value - initial_capital) / initial_capital * 100
    bh_final = curva[-1]["buyHoldEquity"]
    bh_return = (bh_final - initial_capital) / initial_capital * 100
    dias_corridos = (calendario[-1] - calendario[0]).days or 1
    anos = dias_corridos / 365.25
    cagr = ((final_value / initial_capital) ** (1 / anos) - 1) * 100 if anos > 0 and final_value > 0 else 0
    curva_m = _metricas_da_curva([p["equity"] for p in curva])
    calmar = round(cagr / abs(curva_m["maxDrawdown"]), 2) if curva_m["maxDrawdown"] < 0 else None

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    contribuicao = {}
    for tr in trades:
        contribuicao[tr["ticker"]] = contribuicao.get(tr["ticker"], 0.0) + (tr["recebido"] - tr["aporte"])
    por_ticker = sorted(
        [{"ticker": t,
          "trades": sum(1 for x in trades if x["ticker"] == t),
          "contribuicaoPct": round(contribuicao.get(t, 0.0) / initial_capital * 100, 2)}
         for t in ordem],
        key=lambda r: -r["contribuicaoPct"])

    abertas = [a for a, _ in ocupacao]
    exposicao = {
        "pctDiasSemPosicao": round(100 * sum(1 for a in abertas if a == 0) / len(abertas), 1),
        "mediaPosicoesAbertas": round(sum(abertas) / len(abertas), 2),
        "maxPosicoesSimultaneas": max(abertas),
        "picoExposicaoPct": round(max(exposicao_pct), 1) if exposicao_pct else 0.0,
    }

    # Concentração setorial FACTUAL: quantos dias a carteira segurou 2+
    # posições do mesmo grupo -- o mesmo trade contado duas vezes, na
    # métrica que o validador do Veredito já usa para vetar compra dupla.
    grupos = {}
    for t in ordem:
        grupos.setdefault(_sector_key_for(t), []).append(t)
    por_setor = []
    for chave, ts in sorted(grupos.items()):
        if len(ts) < 2:
            continue
        conta = [sum(1 for t in ts if t in abertos) for _, abertos in ocupacao]
        por_setor.append({
            "sector": chave, "label": _SECTOR_LABEL_BY_KEY.get(chave, chave),
            "tickers": ts, "maxSimultaneas": max(conta),
            "pctDiasCom2ouMais": round(100 * sum(1 for c in conta if c >= 2) / len(conta), 1),
        })

    return {
        "mode": "portfolio", "strategy": strategy, "start": start, "end": end,
        "tickersRequested": len(tickers), "tickersOk": n, "failed": failed,
        "initialCapital": initial_capital, "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2), "buyAndHoldReturn": round(bh_return, 2),
        "cagr": round(cagr, 2), "sharpe": curva_m["sharpe"],
        "sortino": curva_m["sortino"], "calmar": calmar,
        "maxDrawdown": curva_m["maxDrawdown"],
        **_metricas_de_trades(trades),
        "bootstrap": _bootstrap_dos_trades([t["pnl"] for t in trades]),
        "totalTrades": len(trades),
        "winRate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avgWin": round(avg_win, 2), "avgLoss": round(avg_loss, 2),
        "trades": trades[-_CARTEIRA_MAX_TRADES_PAYLOAD:],
        "equityCurve": curva,
        "exposicao": exposicao, "porTicker": por_ticker, "porSetor": por_setor,
    }


# ============================================================================
# WALK-FORWARD / OUT-OF-SAMPLE
# ============================================================================
#
# Por que existe: run_sensitivity_analysis varia os parâmetros SOBRE O MESMO
# período em que mede o resultado. Isso responde "quão sensível o resultado é
# ao parâmetro?", mas é rotineiramente lido como se respondesse "esta
# estratégia funciona?" -- e não responde: escolher o parâmetro que foi melhor
# no histórico e depois avaliá-lo nesse mesmo histórico mede a capacidade de
# ajustar ruído, não de prever.
#
# Aqui a série é partida em janelas: o parâmetro é escolhido na janela de
# TREINO e o resultado é medido só na janela de TESTE seguinte, que o
# otimizador nunca viu. O número que sai é comparável ao que se esperaria ao
# operar de verdade.
#
# O que mais informa não é o retorno out-of-sample isolado, e sim:
#   - a DEGRADAÇÃO (in-sample menos out-of-sample): o quanto do backtest
#     original era ajuste de ruído;
#   - a ESTABILIDADE do parâmetro escolhido entre janelas: se o "melhor"
#     RSI muda a cada trimestre, não existe parâmetro certo -- a busca está
#     perseguindo ruído;
#   - o confronto com buy & hold NAS MESMAS janelas de teste: sem isso, um
#     retorno positivo pode ser só o mercado subindo.

# ~1 ano de treino, ~1 trimestre de teste, em PREGÕES (não dias corridos --
# mesma convenção do resto do repo, evita feriado/fim de semana bagunçar a
# contagem).
_WF_TREINO_PREGOES = 252
_WF_TESTE_PREGOES = 63
# Pregões DESCARTADOS entre o fim do treino e o início do teste. A fronteira
# treino|teste não é limpa: os últimos dias do treino e os primeiros do teste
# compartilham as mesmas janelas de indicador (SMA, RSI, estrutura de 60
# pregões, cruzamento de 5), então o parâmetro escolhido "no treino" foi, em
# parte, escolhido olhando dias cujo desdobramento imediato cai dentro do
# teste -- vazamento suave que infla o out-of-sample. 5 = CRUZAMENTO_JANELA,
# o horizonte do sinal mais lento a reagir. 0 desliga (útil em pesquisa
# comparativa).
_WF_EMBARGO_PREGOES = 5
# Piso do _simulate; janela menor que isso não produz simulação válida.
_WF_MIN_PREGOES = 20


def _janelas_walk_forward(n: int, treino: int, teste: int,
                          embargo: int = _WF_EMBARGO_PREGOES) -> list[tuple[int, int, int, int]]:
    """Janelas (ini_treino, fim_treino, ini_teste, fim_teste) por POSIÇÃO,
    com `embargo` pregões descartados entre fim_treino e ini_teste.

    Avança um bloco de teste por vez, então as janelas de teste NÃO se
    sobrepõem -- se sobrepusessem, o mesmo pregão entraria várias vezes no
    resultado agregado e inflaria a confiança."""
    janelas = []
    ini = 0
    while ini + treino + embargo + teste <= n:
        janelas.append((ini, ini + treino,
                        ini + treino + embargo, ini + treino + embargo + teste))
        ini += teste
    return janelas


def _combos_de_params(strategy: str) -> list[dict]:
    """Grade de parâmetros a otimizar por estratégia.

    ma_cross não tem parâmetro exposto: devolve uma combinação vazia, e o
    walk-forward vira medição out-of-sample pura (sem otimização) -- ainda
    útil, e sem fingir que houve escolha."""
    if strategy == "rsi":
        # ob > o: combinação com sobrecomprado <= sobrevendido não descreve
        # nada e só suja a grade.
        return [{"rsi_oversold": o, "rsi_overbought": ob}
                for o in _RSI_OVERSOLD_GRID for ob in _RSI_OVERBOUGHT_GRID if ob > o]
    if strategy == "confluencia":
        return [{"score_threshold": v} for v in _SCORE_THRESHOLD_GRID]
    return [{}]


def _metrica_objetivo(resultado: dict, objetivo: str) -> float | None:
    """Valor a maximizar no treino. None quando a janela não produziu
    negócio nenhum -- sem trade não há o que otimizar, e tratar isso como
    zero faria a busca escolher um parâmetro qualquer por empate."""
    if "error" in resultado or not resultado.get("totalTrades"):
        return None
    valor = resultado.get(objetivo)
    return float(valor) if isinstance(valor, (int, float)) else None


def run_walk_forward(ticker, start, end, strategy="rsi",
                     position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                     stop_loss_pct=None, take_profit_pct=None,
                     rsi_oversold=30.0, rsi_overbought=70.0, score_threshold=60.0,
                     treino_pregoes=_WF_TREINO_PREGOES, teste_pregoes=_WF_TESTE_PREGOES,
                     embargo_pregoes=_WF_EMBARGO_PREGOES, objetivo="sharpe"):
    """Otimiza na janela de treino, mede na de teste, avança e repete.

    stop-loss/take-profit ficam FIXOS no que o usuário configurou: entram na
    grade só se o número de combinações justificasse, e cada eixo a mais
    multiplica o risco de garimpar ruído -- o próprio problema que este
    modo existe pra medir."""
    ohlc_full, error = _fetch_warmed_ohlc(ticker, start, end)
    if error:
        return {"error": error}

    combos = _combos_de_params(strategy)
    # Sinais são construídos UMA vez por combinação sobre a série inteira
    # (com aquecimento) e depois fatiados por janela -- reconstruir por fold
    # multiplicaria o custo sem mudar resultado nenhum. O OHLC recortado é o
    # MESMO para todas as combinações; só os sinais variam.
    ohlc_ref = None
    sinais = {}
    for i, params in enumerate(combos):
        buy_full, sell_full = _build_signals(
            ohlc_full["close"], strategy,
            params.get("rsi_oversold", rsi_oversold),
            params.get("rsi_overbought", rsi_overbought),
            params.get("score_threshold", score_threshold),
        )
        ohlc_ref, buy, sell = _trim_to_window(ohlc_full, buy_full, sell_full, start)
        sinais[i] = (buy, sell)

    n = len(ohlc_ref)
    janelas = _janelas_walk_forward(n, treino_pregoes, teste_pregoes, embargo_pregoes)
    if not janelas:
        return {"error": (f"Período curto demais: {n} pregões para treino de "
                          f"{treino_pregoes} + embargo de {embargo_pregoes} + "
                          f"teste de {teste_pregoes}. "
                          f"Use um intervalo maior ou janelas menores.")}

    def simular(idx_combo, ini, fim, rotulo):
        buy, sell = sinais[idx_combo]
        c, b, s = ohlc_ref.iloc[ini:fim], buy.iloc[ini:fim], sell.iloc[ini:fim]
        if len(c) < _WF_MIN_PREGOES:
            return {"error": "janela curta demais"}
        return _simulate(ticker, strategy, str(c.index[0].date()), str(c.index[-1].date()),
                         c, b, s, position_fraction, commission_pct, slippage_pct,
                         stop_loss_pct, take_profit_pct)

    folds = []
    for ini_tr, fim_tr, ini_te, fim_te in janelas:
        melhor_idx, melhor_valor, melhor_res = None, None, None
        for i in range(len(combos)):
            res = simular(i, ini_tr, fim_tr, "treino")
            valor = _metrica_objetivo(res, objetivo)
            if valor is None:
                continue
            if melhor_valor is None or valor > melhor_valor:
                melhor_idx, melhor_valor, melhor_res = i, valor, res

        if melhor_idx is None:
            # Nenhuma combinação negociou no treino: registrar e seguir, em
            # vez de escolher uma à toa e chamar de "otimizada".
            folds.append({
                "treinoInicio": str(ohlc_ref.index[ini_tr].date()),
                "treinoFim": str(ohlc_ref.index[fim_tr - 1].date()),
                "testeInicio": str(ohlc_ref.index[ini_te].date()),
                "testeFim": str(ohlc_ref.index[fim_te - 1].date()),
                "semSinalNoTreino": True,
            })
            continue

        res_teste = simular(melhor_idx, ini_te, fim_te, "teste")
        folds.append({
            "treinoInicio": str(ohlc_ref.index[ini_tr].date()),
            "treinoFim": str(ohlc_ref.index[fim_tr - 1].date()),
            "testeInicio": str(ohlc_ref.index[ini_te].date()),
            "testeFim": str(ohlc_ref.index[fim_te - 1].date()),
            "melhorParams": combos[melhor_idx],
            "inSample": {k: melhor_res.get(k) for k in _SENSITIVITY_METRICS},
            "outOfSample": ({k: res_teste.get(k) for k in _SENSITIVITY_METRICS}
                            if "error" not in res_teste else {"error": res_teste["error"]}),
        })

    return {
        "ticker": ticker, "strategy": strategy, "objetivo": objetivo,
        "treinoPregoes": treino_pregoes, "testePregoes": teste_pregoes,
        "embargoPregoes": embargo_pregoes,
        "combinacoesTestadas": len(combos),
        "folds": folds,
        "resumo": _resumo_walk_forward(folds),
    }


def _media(valores):
    vals = [v for v in valores if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def _resumo_walk_forward(folds: list[dict]) -> dict:
    validos = [f for f in folds
               if f.get("inSample") and isinstance(f.get("outOfSample"), dict)
               and "error" not in f["outOfSample"]]
    sem_sinal = sum(1 for f in folds if f.get("semSinalNoTreino"))
    if not validos:
        # Continua reportando o CONTADOR de janelas sem sinal: "nenhum
        # resultado" com o motivo escondido faria o operador procurar bug
        # onde só houve estratégia que nunca dispara com aqueles parâmetros.
        return {"nFolds": 0, "foldsSemSinalNoTreino": sem_sinal,
                "aviso": ("nenhuma janela produziu resultado out-of-sample"
                          + (f" -- {sem_sinal} janela(s) sem nenhum negócio no treino"
                             if sem_sinal else ""))}

    ret_is = _media(f["inSample"].get("totalReturn") for f in validos)
    ret_oos = _media(f["outOfSample"].get("totalReturn") for f in validos)
    bh_oos = _media(f["outOfSample"].get("buyAndHoldReturn") for f in validos)
    positivos = sum(1 for f in validos
                    if isinstance(f["outOfSample"].get("totalReturn"), (int, float))
                    and f["outOfSample"]["totalReturn"] > 0)
    venceu_bh = sum(1 for f in validos
                    if isinstance(f["outOfSample"].get("totalReturn"), (int, float))
                    and isinstance(f["outOfSample"].get("buyAndHoldReturn"), (int, float))
                    and f["outOfSample"]["totalReturn"] > f["outOfSample"]["buyAndHoldReturn"])

    # Estabilidade: quantos conjuntos DISTINTOS de parâmetro venceram. Um
    # parâmetro diferente por janela é sinal de que a busca está achando
    # ruído, não regularidade -- vale mais que qualquer retorno bonito.
    assinaturas = [tuple(sorted((f.get("melhorParams") or {}).items())) for f in validos]
    distintos = len(set(assinaturas))

    return {
        "nFolds": len(validos),
        "retornoMedioInSample": ret_is,
        "retornoMedioOutOfSample": ret_oos,
        # A degradação é o número central: quanto do resultado do backtest
        # tradicional era ajuste ao próprio período de avaliação.
        "degradacao": (round(ret_is - ret_oos, 2)
                       if isinstance(ret_is, (int, float)) and isinstance(ret_oos, (int, float))
                       else None),
        "sharpeMedioOutOfSample": _media(f["outOfSample"].get("sharpe") for f in validos),
        "maxDrawdownMedioOutOfSample": _media(f["outOfSample"].get("maxDrawdown") for f in validos),
        "buyAndHoldMedioOutOfSample": bh_oos,
        "foldsPositivos": positivos,
        "foldsQueVenceramBuyHold": venceu_bh,
        "parametrosDistintosEscolhidos": distintos,
        "parametroEstavel": distintos == 1,
        "foldsSemSinalNoTreino": sem_sinal,
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
    if args.get("mode") == "walkforward":
        # embargo aceita 0 explícito (desligar é escolha legítima de
        # pesquisa), então o teste é contra None/"" e não truthiness.
        _embargo = args.get("embargoPregoes")
        result = run_walk_forward(
            args["ticker"], args["start"], args["end"], args.get("strategy", "rsi"),
            treino_pregoes=int(args.get("treinoPregoes") or _WF_TREINO_PREGOES),
            teste_pregoes=int(args.get("testePregoes") or _WF_TESTE_PREGOES),
            embargo_pregoes=int(_embargo) if _embargo not in (None, "") else _WF_EMBARGO_PREGOES,
            objetivo=args.get("objetivo") or "sharpe",
            **common)
    elif args.get("mode") == "sensitivity":
        result = run_sensitivity_analysis(args["ticker"], args["start"], args["end"], args.get("strategy", "rsi"), **common)
    elif args.get("mode") == "portfolio":
        if not tickers:
            result = {"error": "portfolio exige a lista de tickers"}
        else:
            # A carteira não tem position_fraction: a fração é a cota
            # patrimônio/n, decidida pelo próprio motor.
            sem_fracao = {k: v for k, v in common.items() if k != "position_fraction"}
            result = run_portfolio_backtest(
                tickers, args["start"], args["end"], args.get("strategy", "confluencia"), **sem_fracao)
    elif tickers:
        result = run_basket_backtest(tickers, args["start"], args["end"], args.get("strategy", "confluencia"), **common)
    else:
        result = run_backtest(args["ticker"], args["start"], args["end"], args.get("strategy", "rsi"), **common)
    print(json_seguro.dumps(result))
