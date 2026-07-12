"""Backtest de pesquisa do ConfluenceEngine em MU/AVGO/MRVL -- script manual
(NÃO é invocado pelo Node via subprocess/JSON stdin, ao contrário dos outros
arquivos em agent/). Rode direto:

    cd artifacts/api-server/src/agent/scripts
    python3 backtest_confluence.py

Ou de qualquer diretório (o sys.path.insert abaixo resolve o import).

Roda a mesma bateria de testes em DOIS REGIMES de mercado (ver REGIMES
abaixo): o período recente de rali forte (2y, supercycle de HBM/IA) e um
período histórico de correção/lateralização (memory chip downcycle de
2022-2023, queda ampla de tech por alta de juros) -- uma estratégia de
confluência devia teoricamente se sair melhor evitando falsos sinais num
mercado sem direção clara, não necessariamente capturando uma tendência
forte e sustentada (onde qualquer filtro que espera confirmação tende a
entrar tarde/sair cedo).

Pra cada ticker do universo (MU, AVGO, MRVL), usando os outros dois como
sector_returns, em CADA regime:
1. Grid search de min_votes (4, 5, 6) COM sector_returns.
2. O mesmo grid search SEM sector_returns (ablação -- isola se a
   confirmação setorial está ajudando ou só reduzindo trades à toa).
3. Buy & hold do próprio ticker no mesmo período, pra ter uma régua de
   comparação -- Sharpe/win_rate positivos não significam nada se a
   estratégia fica de fora da maior parte de um rali forte.
4. Recalibra kelly_position_size com win_rate/avg_win/avg_loss REAIS do
   melhor min_votes (versão com setor) e roda de novo.

Salva um relatório markdown comparativo único, com uma seção por regime x
ticker.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from confluence_engine import ConfluenceEngine, run_backtest, _fetch_ohlcv  # noqa: E402

TICKERS = ["MU", "AVGO", "MRVL"]
MIN_VOTES_GRID = (4, 5, 6)
RESULTS_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confluence_grid_search_results.md")

# Cada regime é um dict de kwargs pra _fetch_ohlcv (period OU start/end).
# "correcao_2022_2023": memory chip downcycle + selloff amplo de tech por
# alta de juros -- período genuinamente lateral/de queda pra MU/AVGO/MRVL,
# em contraste com o supercycle de alta sustentada de "recente_2y".
REGIMES = {
    "recente_2y (supercycle HBM/IA)": {"period": "2y"},
    "correcao_2022_2023 (memory downcycle + selloff de juros)": {"start": "2022-01-01", "end": "2023-06-30"},
}


def _equity_metrics(equity: pd.Series) -> dict:
    """Mesma matemática de CAGR/Sharpe/drawdown usada em run_backtest --
    reaproveitada aqui pro cálculo de buy & hold, que não passa por
    ConfluenceEngine.evaluate_dataframe."""
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


def _buy_and_hold(df: pd.DataFrame, initial_capital: float = 10_000.0) -> dict:
    equity = initial_capital * (df["close"] / df["close"].iloc[0])
    return _equity_metrics(equity)


def _fetch_all(tickers: list, fetch_kwargs: dict) -> dict:
    dfs = {}
    for t in tickers:
        df, error = _fetch_ohlcv(t, **fetch_kwargs)
        if error:
            print(f"AVISO: não consegui buscar {t}: {error}", file=sys.stderr)
            continue
        dfs[t] = df
    return dfs


def _sector_returns_excluding(dfs: dict, focus_ticker: str) -> "pd.Series | None":
    """Retornos médios dos OUTROS tickers do universo, alinhados (reindex)
    ao índice do ticker em foco -- datas de pregão podem diferir levemente
    entre tickers no yfinance (feriados regionais, atraso de dado etc.)."""
    others = [t for t in dfs if t != focus_ticker]
    if not others:
        return None
    rets = [dfs[t]["close"].reindex(dfs[focus_ticker].index).pct_change() for t in others]
    return pd.concat(rets, axis=1).mean(axis=1)


def _grid_search(df: pd.DataFrame, sector_returns) -> list:
    rows = []
    for mv in MIN_VOTES_GRID:
        engine = ConfluenceEngine(min_votes=mv, kelly_fraction=0.3)
        res = run_backtest(df, engine, sector_returns=sector_returns)
        rows.append({
            "min_votes": mv,
            "total_signals": engine.total_signals,
            "total_return_pct": res["total_return_pct"],
            "cagr": res["cagr"],
            "sharpe": res["sharpe"],
            "max_drawdown_pct": res["max_drawdown_pct"],
            "num_trades": res["num_trades"],
            "win_rate": res["win_rate"],
            "avg_win": res["avg_win"],
            "avg_loss_abs": res["avg_loss_abs"],
        })
    return rows


def _print_table(rows: list, cols: tuple = ("min_votes", "total_signals", "total_return_pct", "cagr", "sharpe", "max_drawdown_pct", "num_trades", "win_rate")) -> None:
    print(pd.DataFrame(rows)[list(cols)].to_string(index=False))


def _md_table(rows: list) -> str:
    md = ["| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(
            f"| {r['min_votes']} | {r['total_signals']} | {r['total_return_pct']:.2f} | "
            f"{r['cagr']*100:.2f}% | {r['sharpe']:.3f} | {r['max_drawdown_pct']:.2f} | "
            f"{r['num_trades']} | {r['win_rate']*100:.1f}% |"
        )
    return "\n".join(md)


def _best_viable(rows: list, min_trades: int = 3):
    viable = [r for r in rows if r["num_trades"] >= min_trades]
    return max(viable, key=lambda r: r["sharpe"]) if viable else None


def _run_for_ticker(ticker: str, dfs: dict, regime_label: str) -> str:
    df = dfs[ticker]
    sector_returns = _sector_returns_excluding(dfs, ticker)
    other_tickers = [t for t in dfs if t != ticker]

    print(f"\n{'='*78}\n[{regime_label}] {ticker}  (setor de comparação: {', '.join(other_tickers) or 'nenhum'})\n{'='*78}")

    print(f"\n-- Grid search COM sector_returns ({', '.join(other_tickers)}) --")
    rows_with = _grid_search(df, sector_returns)
    _print_table(rows_with)

    print(f"\n-- Grid search SEM sector_returns (ablação) --")
    rows_without = _grid_search(df, None)
    _print_table(rows_without)

    bh = _buy_and_hold(df)
    print(f"\n-- Buy & hold {ticker} ({df.index[0].date()} a {df.index[-1].date()}) --")
    print(f"total_return_pct={bh['total_return_pct']:.2f}  cagr={bh['cagr']*100:.2f}%  "
          f"sharpe={bh['sharpe']:.3f}  max_drawdown={bh['max_drawdown_pct']:.2f}%")

    md_sections = [
        f"## [{regime_label}] {ticker}\n",
        f"Período: {df.index[0].date()} a {df.index[-1].date()}\n",
        f"### Com sector_returns ({', '.join(other_tickers)})\n",
        _md_table(rows_with),
        f"\n### Sem sector_returns (ablação)\n",
        _md_table(rows_without),
        f"\n### Buy & hold {ticker}\n",
        f"total_return_pct={bh['total_return_pct']:.2f}%, cagr={bh['cagr']*100:.2f}%, "
        f"sharpe={bh['sharpe']:.3f}, max_drawdown={bh['max_drawdown_pct']:.2f}%\n",
    ]

    best = _best_viable(rows_with)
    if best is None:
        print("\nNenhum min_votes (com setor) gerou pelo menos 3 trades -- sem recalibração de Kelly pra esse ticker/regime.")
        md_sections.append("\nNenhum min_votes (com setor) gerou pelo menos 3 trades -- sem recalibração de Kelly.\n")
        return "\n".join(md_sections)

    print(f"\nMelhor min_votes (com setor) por Sharpe, >=3 trades: {best['min_votes']} "
          f"(sharpe={best['sharpe']:.3f}, win_rate={best['win_rate']*100:.1f}%, "
          f"max_drawdown={best['max_drawdown_pct']:.2f}%)")
    print(f"win_rate real: {best['win_rate']:.4f}  avg_win real: {best['avg_win']:.4f}  "
          f"avg_loss_abs real: {best['avg_loss_abs']:.4f}  (priors placeholder eram 0.5/0.05/0.03)")

    engine_best = ConfluenceEngine(min_votes=best["min_votes"], kelly_fraction=0.3)
    recalibrated = run_backtest(
        df, engine_best, sector_returns=sector_returns,
        kelly_win_rate=best["win_rate"], kelly_avg_win=best["avg_win"], kelly_avg_loss=best["avg_loss_abs"],
    )
    print(f"total_return_pct antes (priors placeholder): {best['total_return_pct']:.2f}")
    print(f"total_return_pct depois (kelly recalibrado):  {recalibrated['total_return_pct']:.2f}")

    md_sections.append(
        f"\n### Recalibração de Kelly (min_votes={best['min_votes']}, com setor)\n\n"
        f"win_rate real: {best['win_rate']:.4f}, avg_win real: {best['avg_win']:.4f}, "
        f"avg_loss_abs real: {best['avg_loss_abs']:.4f} (priors placeholder: 0.5/0.05/0.03)\n\n"
        f"total_return_pct antes: {best['total_return_pct']:.2f}% -> depois: {recalibrated['total_return_pct']:.2f}%\n"
    )
    return "\n".join(md_sections)


def main() -> None:
    all_sections = []
    for regime_label, fetch_kwargs in REGIMES.items():
        print(f"\n\n{'#'*78}\nREGIME: {regime_label}  ({fetch_kwargs})\n{'#'*78}")
        dfs = _fetch_all(TICKERS, fetch_kwargs)
        if not dfs:
            print(f"ERRO: não consegui buscar nenhum ticker do universo pro regime '{regime_label}'", file=sys.stderr)
            continue
        for t in dfs:
            all_sections.append(_run_for_ticker(t, dfs, regime_label))

    if not all_sections:
        print("ERRO: nenhum regime produziu resultado", file=sys.stderr)
        sys.exit(1)

    with open(RESULTS_MD_PATH, "w") as f:
        f.write(f"# Grid search — ConfluenceEngine em {', '.join(TICKERS)}, múltiplos regimes\n\n")
        f.write("\n\n".join(all_sections) + "\n")
    print(f"\nRelatório completo salvo em {RESULTS_MD_PATH}")


if __name__ == "__main__":
    main()
