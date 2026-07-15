"""ConfluenceEngine — motor de sinais por consenso multi-indicador, standalone subprocess.

Só dispara "buy"/"sell" quando pelo menos min_votes de total_signals concordam
na mesma direção (trend, momentum, volatility, volume, sector). O catalyst
(evento de calendário conhecido — earnings de pares, lockup de IPO etc.) NÃO
é um voto: é um veto — perto do evento, força "flat" independente dos outros
sinais, em vez de contar como mais uma opinião na votação.

RSI de Wilder reimplementado aqui (não importado de get_trend.py/backtest.py)
porque este arquivo roda como script standalone via subprocess, sem contexto
de pacote pra import relativo funcionar — mesmo motivo documentado em
backtest.py. Fórmula e caso especial (avg_loss==0 -> RSI=100) idênticos aos
já usados no projeto.

Input (stdin JSON, modo endpoint): {"symbol": "MU", "period": "18mo"}
Output (stdout JSON): {symbol, asOf, action, confidence, votes, catalystVeto,
                       macroRiskVeto, macroRiskSignals}
"""
import os, sys, json, warnings, logging

# ── Redireciona fd-1 -> fd-2 ANTES de importar yfinance/pandas, senão um
# print/warning de dentro dessas libs pode vazar pro pipe que o Node lê como
# JSON e quebrar o JSON.parse (mesmo padrão de get_technicals.py). Só se
# aplica no modo "rodado como subprocess"; scripts de pesquisa (scripts/
# backtest_confluence.py) importam as funções deste módulo sem passar por
# aqui, então não pagam esse custo.
if __name__ == "__main__":
    _real_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = open(os.devnull, "w")
    warnings.filterwarnings("ignore")
    logging.disable(logging.CRITICAL)

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from security import sanitize_ticker


# ---------------------------------------------------------------------------
# Indicadores base
# ---------------------------------------------------------------------------

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi_wilder(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder — mesma fórmula/caso especial de get_trend.py e backtest.py:
    avg_loss==0 (só alta no período) -> RSI=100, não NaN/50."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bandwidth(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return (upper - lower) / mid


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def vwap_rolling(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """VWAP em janela rolante (não cumulativo desde o início do histórico —
    um VWAP acumulado ao longo de 2+ anos vira uma média de longuíssimo prazo
    que quase não se move e deixa de ser um sinal útil de fluxo recente)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = (typical_price * df["volume"]).rolling(window).sum()
    v = df["volume"].rolling(window).sum()
    return pv / v


# ---------------------------------------------------------------------------
# Camada de sinais (cada um retorna +1 compra / -1 venda / 0 neutro)
# ---------------------------------------------------------------------------

def signal_trend(df: pd.DataFrame) -> np.ndarray:
    e9, e21, e50 = ema(df["close"], 9), ema(df["close"], 21), ema(df["close"], 50)
    _, _, hist = macd(df["close"])
    bullish = (e9 > e21) & (e21 > e50) & (hist > 0)
    bearish = (e9 < e21) & (e21 < e50) & (hist < 0)
    return np.select([bullish, bearish], [1, -1], default=0)


def signal_momentum(df: pd.DataFrame) -> np.ndarray:
    rsi = rsi_wilder(df["close"])
    bullish = (rsi > 55) & (rsi < 80)
    bearish = (rsi < 45) & (rsi > 20)
    return np.select([bullish, bearish], [1, -1], default=0)


def signal_volatility(df: pd.DataFrame) -> np.ndarray:
    """Expansão de volatilidade após squeeze costuma confirmar direção do move."""
    bw = bollinger_bandwidth(df["close"])
    bw_expanding = bw > bw.rolling(10).mean()
    price_up = df["close"] > df["close"].shift(3)
    bullish = bw_expanding & price_up
    bearish = bw_expanding & ~price_up
    return np.select([bullish, bearish], [1, -1], default=0)


def signal_volume(df: pd.DataFrame) -> np.ndarray:
    obv_series = obv(df)
    vwap_series = vwap_rolling(df)
    obv_rising = obv_series > obv_series.shift(5)
    above_vwap = df["close"] > vwap_series
    bullish = obv_rising & above_vwap
    bearish = ~obv_rising & ~above_vwap
    return np.select([bullish, bearish], [1, -1], default=0)


def signal_sector(df: pd.DataFrame, sector_returns: pd.Series, lookback: int = 20) -> np.ndarray:
    """Confluência setorial: correlaciona retornos do ativo com a média de
    retornos do setor (ex.: MU vs. média de AVGO/MRVL). sector_returns precisa
    ter o MESMO índice de df (reindexar/alinhar antes de chamar)."""
    asset_ret = df["close"].pct_change()
    rolling_corr = asset_ret.rolling(lookback).corr(sector_returns)
    sector_trend = sector_returns.rolling(5).mean()
    bullish = (rolling_corr > 0.5) & (sector_trend > 0)
    bearish = (rolling_corr > 0.5) & (sector_trend < 0)
    return np.select([bullish, bearish], [1, -1], default=0)


def catalyst_veto(df: pd.DataFrame, event_dates: Optional[list] = None, window: int = 2) -> pd.Series:
    """Veto de calendário — NÃO é um voto de compra/venda. Retorna True nos
    dias dentro de `window` dias de um evento conhecido (earnings de pares,
    lockup de IPO etc.); o motor força action='flat' nesses dias, porque
    perto de um catalisador conhecido a confluência técnica normal não é
    confiável (gap risk, ruído de fluxo), não porque o evento "vote" numa
    direção. Ver .agents/memory/skhy-ipo-monitoring.md para um caso real
    (gate de 28/jul antes dos resultados + listagem KOSPI da SKHY)."""
    result = pd.Series(False, index=df.index)
    if not event_dates:
        return result
    for d in event_dates:
        mask = (df.index >= pd.Timestamp(d) - pd.Timedelta(days=window)) & \
               (df.index <= pd.Timestamp(d) + pd.Timedelta(days=window))
        result[mask] = True
    return result


# ---------------------------------------------------------------------------
# Motor de confluência
# ---------------------------------------------------------------------------

SIGNAL_NAMES = ("trend", "momentum", "volatility", "volume", "sector")


@dataclass
class ConfluenceEngine:
    min_votes: int = 4
    total_signals: int = len(SIGNAL_NAMES)  # 5 — catalyst é veto, não voto
    kelly_fraction: float = 0.3  # fração do Kelly cheio (25-50% recomendado)

    def evaluate_row(self, votes: dict, vetoed: bool = False) -> dict:
        if vetoed:
            return {"action": "flat", "confidence": 0.0, "votes": votes, "catalystVeto": True}
        buy_votes = sum(1 for v in votes.values() if v == 1)
        sell_votes = sum(1 for v in votes.values() if v == -1)
        if buy_votes >= self.min_votes:
            return {"action": "buy", "confidence": buy_votes / self.total_signals, "votes": votes, "catalystVeto": False}
        if sell_votes >= self.min_votes:
            return {"action": "sell", "confidence": sell_votes / self.total_signals, "votes": votes, "catalystVeto": False}
        return {"action": "flat", "confidence": 0.0, "votes": votes, "catalystVeto": False}

    def evaluate_dataframe(
        self,
        df: pd.DataFrame,
        sector_returns: Optional[pd.Series] = None,
        event_dates: Optional[list] = None,
    ) -> pd.DataFrame:
        """df precisa ter colunas: open, high, low, close, volume (index = datetime)."""
        out = pd.DataFrame(index=df.index)
        out["trend"] = signal_trend(df)
        out["momentum"] = signal_momentum(df)
        out["volatility"] = signal_volatility(df)
        out["volume"] = signal_volume(df)
        out["sector"] = signal_sector(df, sector_returns) if sector_returns is not None else 0
        out["vetoed"] = catalyst_veto(df, event_dates)

        actions, confidences, vetoes = [], [], []
        for _, row in out.iterrows():
            votes = {k: int(row[k]) for k in SIGNAL_NAMES}
            res = self.evaluate_row(votes, vetoed=bool(row["vetoed"]))
            actions.append(res["action"])
            confidences.append(res["confidence"])
            vetoes.append(res["catalystVeto"])

        out["action"] = actions
        out["confidence"] = confidences
        out["catalystVeto"] = vetoes
        return out

    def kelly_position_size(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Kelly fracionado. avg_win/avg_loss são MAGNITUDES (sempre positivas)
        de retorno médio, ex.: avg_win=0.05 (+5%), avg_loss=0.03 (perda de 3%
        em módulo) — nunca passe o valor com sinal negativo aqui; internamente
        já aplicamos abs() como rede de segurança, mas o chamador deve
        calcular abs(media dos pnl negativos), não a média literal (que sai
        negativa e inverteria a fórmula)."""
        avg_loss = abs(avg_loss)
        avg_win = abs(avg_win)
        if avg_loss == 0:
            return 0.0
        b = avg_win / avg_loss
        p = win_rate
        q = 1 - p
        full_kelly = (b * p - q) / b
        return max(0.0, min(1.0, full_kelly * self.kelly_fraction))


def run_backtest(
    df: pd.DataFrame,
    engine: ConfluenceEngine,
    sector_returns: Optional[pd.Series] = None,
    event_dates: Optional[list] = None,
    initial_capital: float = 10_000.0,
    kelly_win_rate: float = 0.5,
    kelly_avg_win: float = 0.05,
    kelly_avg_loss: float = 0.03,
) -> dict:
    """kelly_win_rate/avg_win/avg_loss são priors PLACEHOLDER por padrão (primeira
    passada). Depois de rodar uma vez e extrair win_rate/avg_win/avg_loss REAIS
    dos trades fechados (ver scripts/backtest_confluence.py), rode de novo
    passando esses valores reais para recalibrar o position sizing."""
    signals = engine.evaluate_dataframe(df, sector_returns, event_dates)
    size_frac = engine.kelly_position_size(kelly_win_rate, kelly_avg_win, kelly_avg_loss)

    capital = initial_capital
    equity_curve = [capital]
    position = 0
    entry_price = None
    trades = []

    closes = df["close"].values
    actions = signals["action"].values

    for i in range(1, len(df)):
        price = closes[i]
        action = actions[i]

        if position == 0 and action in ("buy", "sell"):
            position = 1 if action == "buy" else -1
            entry_price = price
            trades.append({
                "entry_date": str(df.index[i])[:10], "entry_price": float(price),
                "direction": position, "size_frac": size_frac,
            })

        elif position != 0 and (action == "flat" or action == ("sell" if position == 1 else "buy")):
            pnl_pct = (price - entry_price) / entry_price * position
            capital *= (1 + pnl_pct * trades[-1]["size_frac"])
            trades[-1].update({"exit_date": str(df.index[i])[:10], "exit_price": float(price), "pnl_pct": float(pnl_pct)})
            position = 0
            entry_price = None

        equity_curve.append(capital)

    equity = pd.Series(equity_curve, index=df.index[: len(equity_curve)])
    returns = equity.pct_change().dropna()

    days = (equity.index[-1] - equity.index[0]).days or 1
    years = days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown = drawdown.min()

    closed_trades = [t for t in trades if "pnl_pct" in t]
    wins = [t["pnl_pct"] for t in closed_trades if t["pnl_pct"] > 0]
    losses = [t["pnl_pct"] for t in closed_trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(closed_trades) if closed_trades else 0

    return {
        "final_capital": capital,
        "total_return_pct": (capital / initial_capital - 1) * 100,
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "max_drawdown_pct": float(max_drawdown) * 100,
        "num_trades": len(closed_trades),
        "win_rate": win_rate,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss_abs": float(abs(np.mean(losses))) if losses else 0.0,
        "kelly_size_frac_used": size_frac,
        "trades": trades,
    }


def _fetch_ohlcv(
    ticker: str,
    period: str = "18mo",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """start/end (formato "YYYY-MM-DD") têm prioridade sobre period quando
    informados -- útil pra testar um regime histórico específico (ex.:
    correção/lateralização) em vez de só uma janela relativa a hoje."""
    import yfinance as yf
    if start or end:
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
    else:
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    if df.empty:
        return None, "Sem dados para o período"
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if len(df) < 60:
        return None, "Dados insuficientes (mínimo 60 dias, para EMA50/BB20 aquecerem)"
    return df, None


# ---------------------------------------------------------------------------
# Veto de risco macro (juros + petróleo) — só no modo endpoint (__main__)
# abaixo, NUNCA em evaluate_dataframe/run_backtest: aplicar o preço de
# petróleo/juros de HOJE a uma linha histórica de anos atrás seria
# metodologicamente errado (look-ahead bias). Limiares espelham
# YIELD_LEVEL/OIL_SHOCK_* de market_alerts.py — duplicados aqui de
# propósito, mesmo motivo do RSI de Wilder reimplementado acima (script
# standalone, sem contexto de pacote pra import relativo funcionar).
# ---------------------------------------------------------------------------

YIELD_TICKER            = "^TNX"
YIELD_LEVEL              = 4.5
OIL_TICKER               = "CL=F"
OIL_SHOCK_LOOKBACK_DAYS  = 10
OIL_SHOCK_PCT            = 15.0

# Tickers com exposição DIRETA de cadeia de suprimento a fabs da TSMC/
# estreito de Taiwan — só nesse subconjunto o veto de risco macro se aplica.
# Memória/storage (MU, SNDK, WDC) e infra (ANET, VRT, SMCI) ficam de fora por
# serem menos dependentes de uma fab específica de Taiwan.
HARDWARE_EXPOSED_TICKERS = {"NVDA", "AVGO", "AMD", "QCOM", "TSM", "AAPL", "ARM", "ALAB", "CRDO"}


def macro_risk_signals() -> dict:
    """Juro de 10y elevado + choque de alta no petróleo (mesmos limiares de
    market_alerts.py). NÃO inclui o componente geopolítico de manchetes —
    este script não recebe headlines como input (ver check_macro_regime_risk
    em market_alerts.py pro sinal completo de 3 componentes usado pelo loop
    do agente). Fail-open: qualquer falha de rede conta como sinal inativo,
    nunca derruba a avaliação principal do símbolo."""
    import yfinance as yf
    active: list[str] = []
    try:
        df_y = yf.Ticker(YIELD_TICKER).history(period="5d")
        if df_y is not None and not df_y.empty:
            y = float(df_y["Close"].iloc[-1])
            if y > 20:
                y = y / 10
            if y >= YIELD_LEVEL:
                active.append(f"yield 10y ~{y:.2f}%")
    except Exception:
        pass
    try:
        df_o = yf.Ticker(OIL_TICKER).history(period="1mo")
        if df_o is not None and len(df_o) >= OIL_SHOCK_LOOKBACK_DAYS + 1:
            then = float(df_o["Close"].iloc[-1 - OIL_SHOCK_LOOKBACK_DAYS])
            now = float(df_o["Close"].iloc[-1])
            if then > 0:
                chg = (now / then - 1) * 100
                if chg >= OIL_SHOCK_PCT:
                    active.append(f"WTI +{chg:.1f}% em {OIL_SHOCK_LOOKBACK_DAYS}p")
    except Exception:
        pass
    return {"activeSignals": active, "count": len(active)}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    try:
        symbol = sanitize_ticker(args["symbol"])
        period = args.get("period", "18mo")
        df, error = _fetch_ohlcv(symbol, period)
        if error:
            result = {"symbol": symbol, "error": error}
        else:
            engine = ConfluenceEngine(
                min_votes=int(args.get("minVotes", 4)),
                kelly_fraction=float(args.get("kellyFraction", 0.3)),
            )
            signals = engine.evaluate_dataframe(df)
            last = signals.iloc[-1]
            action = last["action"]
            confidence = float(last["confidence"])

            # Veto de risco macro: só avalia (custa 2 chamadas extras de rede)
            # quando pode de fato mudar o resultado -- ação já seria "buy" E o
            # símbolo tem exposição direta a Taiwan/TSMC.
            macro_veto = False
            macro_signals: list[str] = []
            if action == "buy" and symbol in HARDWARE_EXPOSED_TICKERS:
                risk = macro_risk_signals()
                if risk["count"] >= 2:
                    action = "flat"
                    confidence = 0.0
                    macro_veto = True
                    macro_signals = risk["activeSignals"]

            result = {
                "symbol": symbol,
                "asOf": str(df.index[-1])[:10],
                "action": action,
                "confidence": confidence,
                "votes": {k: int(last[k]) for k in SIGNAL_NAMES},
                "catalystVeto": bool(last["catalystVeto"]),
                "macroRiskVeto": macro_veto,
                "macroRiskSignals": macro_signals,
            }
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}

    out = json.dumps(result, ensure_ascii=False) + "\n"
    os.write(_real_stdout_fd, out.encode("utf-8"))
    os.close(_real_stdout_fd)
