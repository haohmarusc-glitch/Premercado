"""Backtest de uma regra de entrada/saída de verdade usando o sinal global
(Europa/Ásia) que se confirmou real no estudo de correlação anterior
(backtest_global_market_pulse.py) -- NÃO é invocado pelo Node via
subprocess, mesma convenção de backtest_confluence.py. Rode direto:

    cd artifacts/api-server/src/agent/scripts
    python3 backtest_global_signal_strategy.py

Ou de qualquer diretório (o sys.path.insert abaixo resolve o import).

Contexto: o estudo de correlação anterior achou hit-rate consistentemente
acima de 50% pra Europa (DAX/CAC/FTSE) e Ásia (Nikkei/KOSPI/HSI) contra o
gap de abertura da Nasdaq, nos dois regimes -- e confirmou que EUR/USD não
tem sinal nenhum (por isso fica de fora aqui). Correlação não é estratégia;
este script testa se dá pra converter esse sinal em retorno de verdade.

Regra testada (3 variantes de sinal x 2 variantes de posição):
  Sinal:    europa (média DAX+CAC+FTSE), asia (média Nikkei+KOSPI+HSI),
            combinado (média dos dois grupos).
  Posição:  momentum_long_flat  -> comprado se sinal > 0, senão fora (caixa)
            momentum_long_short -> comprado se sinal > 0, vendido se < 0

Retorno simulado: abertura -> fechamento da Nasdaq NO MESMO DIA (não
fechamento -> fechamento) -- é o retorno que dava pra capturar de verdade
agindo no sinal antes/na abertura, evitando o viés de usar o próprio gap
(que já está contaminado pela correlação testada antes) como retorno.

Compara contra buy & hold do próprio índice (fechamento -> fechamento,
período inteiro), nos mesmos dois regimes do ConfluenceEngine e do estudo
de correlação anterior -- mesma régua de sempre: Sharpe/retorno positivos
não significam nada se a estratégia fica de fora da maior parte de um rali.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf

TARGET_TICKER = "^IXIC"
TARGET_LABEL = "Nasdaq Composite"

# EUR/USD fica de fora -- backtest_global_market_pulse.py confirmou hit-rate
# de ~49% (pior que cara-ou-coroa) nos dois regimes.
EUROPE_TICKERS = {"^GDAXI": "DAX", "^FCHI": "CAC 40", "^FTSE": "FTSE 100"}
ASIA_TICKERS = {"^N225": "Nikkei 225", "^KS11": "KOSPI", "^HSI": "Hang Seng"}

RESULTS_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "global_signal_strategy_results.md"
)

REGIMES: dict[str, dict] = {
    "recente_2y (supercycle HBM/IA)": {"period": "2y"},
    "correcao_2022_2023 (memory downcycle + selloff de juros)": {"start": "2022-01-01", "end": "2023-06-30"},
}

INITIAL_CAPITAL = 10_000.0


def _fetch_history(ticker: str, period: str | None = None, start: str | None = None,
                    end: str | None = None) -> "pd.DataFrame | None":
    try:
        if start is not None:
            df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
        else:
            df = yf.Ticker(ticker).history(period=period or "2y", interval="1d", auto_adjust=False)
        if df is None or df.empty or len(df) < 3:
            return None
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()
        return df
    except Exception as exc:
        print(f"AVISO: falha ao buscar {ticker}: {exc}", file=sys.stderr)
        return None


def _day_change_series(df: pd.DataFrame) -> pd.Series:
    return df["Close"].pct_change() * 100.0


def _intraday_return_series(df: pd.DataFrame) -> pd.Series:
    """Retorno abertura -> fechamento do MESMO dia (o que dava pra capturar
    agindo no sinal antes/na abertura)."""
    return df["Close"] / df["Open"] - 1.0


def _equity_metrics(equity: pd.Series) -> dict:
    """Mesma matemática de CAGR/Sharpe/drawdown de backtest_confluence.py."""
    returns = equity.pct_change().dropna()
    days = (equity.index[-1] - equity.index[0]).days or 1
    years = days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown = drawdown.min()
    return {
        "total_return_pct": float(equity.iloc[-1] / equity.iloc[0] - 1) * 100,
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "max_drawdown_pct": float(max_drawdown) * 100,
    }


def _buy_and_hold(df: pd.DataFrame) -> dict:
    equity = INITIAL_CAPITAL * (df["Close"] / df["Close"].iloc[0])
    return _equity_metrics(equity)


def _group_signal(tickers: dict, fetch_kwargs: dict, target_index: pd.DatetimeIndex) -> "pd.Series | None":
    """Média da variação % diária dos tickers do grupo, alinhada às datas
    do alvo (Nasdaq). Ignora tickers que falharem ao buscar."""
    series_list = []
    for ticker in tickers:
        df = _fetch_history(ticker, **fetch_kwargs)
        if df is None:
            print(f"AVISO: sem dados de {ticker} pro grupo -- seguindo sem ele", file=sys.stderr)
            continue
        series_list.append(_day_change_series(df).reindex(target_index))
    if not series_list:
        return None
    return pd.concat(series_list, axis=1).mean(axis=1)


def _simulate(signal: pd.Series, intraday_return: pd.Series, mode: str) -> dict:
    """mode='long_flat' -> posição 0/1; mode='long_short' -> posição -1/0/1."""
    joined = pd.concat([signal, intraday_return], axis=1).dropna()
    joined.columns = ["signal", "ret"]
    if len(joined) < 5:
        return {"error": "dados insuficientes", "num_trades": 0}

    if mode == "long_flat":
        position = np.where(joined["signal"] > 0, 1.0, 0.0)
    else:
        position = np.sign(joined["signal"])

    strat_returns = position * joined["ret"]
    equity = INITIAL_CAPITAL * (1 + strat_returns).cumprod()
    metrics = _equity_metrics(equity)

    traded = strat_returns[position != 0]
    num_trades = int((position != 0).sum())
    win_rate = float((traded > 0).mean()) if num_trades > 0 else float("nan")

    metrics.update({"num_trades": num_trades, "win_rate": win_rate})
    return metrics


def _fmt(m: dict) -> str:
    if "error" in m:
        return m["error"]
    return (f"retorno={m['total_return_pct']:.2f}%  cagr={m['cagr']*100:.2f}%  "
            f"sharpe={m['sharpe']:.3f}  max_dd={m['max_drawdown_pct']:.2f}%  "
            f"trades={m['num_trades']}  win_rate={m.get('win_rate', float('nan'))*100:.1f}%")


def _run_regime(regime_label: str, fetch_kwargs: dict) -> str:
    print(f"\n\n{'#' * 78}\nREGIME: {regime_label}  ({fetch_kwargs})\n{'#' * 78}")

    target_df = _fetch_history(TARGET_TICKER, **fetch_kwargs)
    if target_df is None:
        msg = f"ERRO: não consegui buscar {TARGET_TICKER} pro regime '{regime_label}'"
        print(msg, file=sys.stderr)
        return f"## {regime_label}\n\n{msg}\n"

    intraday_return = _intraday_return_series(target_df)

    europe_signal = _group_signal(EUROPE_TICKERS, fetch_kwargs, target_df.index)
    asia_signal = _group_signal(ASIA_TICKERS, fetch_kwargs, target_df.index)
    combined_signal = None
    if europe_signal is not None and asia_signal is not None:
        combined_signal = pd.concat([europe_signal, asia_signal], axis=1).mean(axis=1)

    signals = {
        "europa (DAX+CAC+FTSE)": europe_signal,
        "asia (Nikkei+KOSPI+HSI)": asia_signal,
        "combinado (europa+asia)": combined_signal,
    }

    bh = _buy_and_hold(target_df)
    print(f"\nBuy & hold {TARGET_LABEL} ({target_df.index[0].date()} a {target_df.index[-1].date()}): "
          f"{_fmt({**bh, 'num_trades': len(target_df), 'win_rate': float('nan')})}")

    md = [
        f"## {regime_label}\n",
        f"Alvo: {TARGET_LABEL} ({TARGET_TICKER}). "
        f"Período: {target_df.index[0].date()} a {target_df.index[-1].date()}.\n",
        f"Buy & hold: retorno={bh['total_return_pct']:.2f}%, cagr={bh['cagr']*100:.2f}%, "
        f"sharpe={bh['sharpe']:.3f}, max_dd={bh['max_drawdown_pct']:.2f}%\n",
        "| Sinal | Posição | Retorno | CAGR | Sharpe | Max DD | Trades | Win rate |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for signal_label, signal in signals.items():
        if signal is None:
            print(f"\nAVISO: sinal '{signal_label}' indisponível neste regime")
            continue
        for mode, mode_label in (("long_flat", "long/flat"), ("long_short", "long/short")):
            m = _simulate(signal, intraday_return, mode)
            print(f"\n[{signal_label} | {mode_label}] {_fmt(m)}")
            if "error" in m:
                md.append(f"| {signal_label} | {mode_label} | {m['error']} | | | | | |")
            else:
                md.append(
                    f"| {signal_label} | {mode_label} | {m['total_return_pct']:.2f}% | "
                    f"{m['cagr']*100:.2f}% | {m['sharpe']:.3f} | {m['max_drawdown_pct']:.2f}% | "
                    f"{m['num_trades']} | {m['win_rate']*100:.1f}% |"
                )

    return "\n".join(md) + "\n"


def main() -> None:
    sections = []
    for regime_label, fetch_kwargs in REGIMES.items():
        sections.append(_run_regime(regime_label, fetch_kwargs))

    if not sections:
        print("ERRO: nenhum regime produziu resultado", file=sys.stderr)
        sys.exit(1)

    with open(RESULTS_MD_PATH, "w") as f:
        f.write("# Backtest: estratégia de sinal global (Europa/Ásia) vs. buy & hold da Nasdaq\n\n")
        f.write(
            "Testa se o sinal de correlação encontrado em "
            "global_market_correlation_results.md (Europa e Ásia batem o gap de abertura "
            "da Nasdaq com hit-rate > 50%, EUR/USD não) vira retorno de verdade. Retorno "
            "simulado é abertura -> fechamento da Nasdaq no mesmo dia (o que dava pra "
            "capturar agindo no sinal antes/na abertura), não fechamento -> fechamento "
            "(que já estaria contaminado pelo próprio gap testado). Compara com buy & hold "
            "do índice inteiro no mesmo período.\n\n"
        )
        f.write("\n\n".join(sections))
    print(f"\nRelatório completo salvo em {RESULTS_MD_PATH}")


if __name__ == "__main__":
    main()
