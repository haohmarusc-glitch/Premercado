"""Backtest de pesquisa do ConfluenceEngine para MU, usando AVGO/MRVL como
sector_returns — script manual (NÃO é invocado pelo Node via subprocess/JSON
stdin, ao contrário dos outros arquivos em agent/). Rode direto:

    cd artifacts/api-server/src/agent/scripts
    python3 backtest_confluence.py

Ou de qualquer diretório (o sys.path.insert abaixo resolve o import).

Passos:
1. Busca ~2 anos de OHLCV diário de MU, AVGO, MRVL via yfinance.
2. Monta sector_returns = média dos retornos % de AVGO/MRVL, alinhado ao
   índice de MU.
3. Grid search de min_votes em (4, 5, 6) sobre MU — imprime e salva uma
   tabela markdown comparativa.
4. Recalibra kelly_position_size com win_rate/avg_win/avg_loss REAIS dos
   trades fechados do melhor min_votes (maior Sharpe sem destruir o
   win_rate nem explodir o max_drawdown), roda de novo e imprime a
   comparação antes/depois.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from confluence_engine import ConfluenceEngine, run_backtest, _fetch_ohlcv  # noqa: E402

TICKER = "MU"
SECTOR_TICKERS = ["AVGO", "MRVL"]
PERIOD = "2y"
MIN_VOTES_GRID = (4, 5, 6)
RESULTS_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confluence_grid_search_results.md")


def _sector_returns_for(mu_df: pd.DataFrame) -> pd.Series:
    """Retornos médios de AVGO/MRVL, alinhados (reindex) ao índice de MU --
    tickers diferentes podem ter datas de pregão levemente diferentes no
    yfinance (feriados regionais, atraso de dado etc.), então alinhar
    explicitamente evita NaN silencioso na correlação do signal_sector."""
    rets = []
    for t in SECTOR_TICKERS:
        df, error = _fetch_ohlcv(t, PERIOD)
        if error:
            print(f"AVISO: não consegui buscar {t}: {error}", file=sys.stderr)
            continue
        rets.append(df["close"].reindex(mu_df.index).pct_change())
    if not rets:
        raise RuntimeError("Nenhum ticker do setor pôde ser buscado — sector_returns indisponível")
    return pd.concat(rets, axis=1).mean(axis=1)


def _print_and_save_grid(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    md = ["| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(
            f"| {r['min_votes']} | {r['total_signals']} | {r['total_return_pct']:.2f} | "
            f"{r['cagr']*100:.2f}% | {r['sharpe']:.3f} | {r['max_drawdown_pct']:.2f} | "
            f"{r['num_trades']} | {r['win_rate']*100:.1f}% |"
        )
    with open(RESULTS_MD_PATH, "w") as f:
        f.write(f"# Grid search — ConfluenceEngine em {TICKER} (setor: {', '.join(SECTOR_TICKERS)})\n\n")
        f.write("\n".join(md) + "\n")
    print(f"\nTabela salva em {RESULTS_MD_PATH}")


def main() -> None:
    mu_df, error = _fetch_ohlcv(TICKER, PERIOD)
    if error:
        print(f"ERRO ao buscar {TICKER}: {error}", file=sys.stderr)
        sys.exit(1)

    sector_returns = _sector_returns_for(mu_df)

    print(f"=== Grid search min_votes em {MIN_VOTES_GRID} — {TICKER} com sector_returns de {SECTOR_TICKERS} ===")
    rows = []
    results_by_min_votes = {}
    for mv in MIN_VOTES_GRID:
        engine = ConfluenceEngine(min_votes=mv, kelly_fraction=0.3)
        res = run_backtest(mu_df, engine, sector_returns=sector_returns)
        results_by_min_votes[mv] = res
        rows.append({
            "min_votes": mv,
            "total_signals": engine.total_signals,
            "total_return_pct": res["total_return_pct"],
            "cagr": res["cagr"],
            "sharpe": res["sharpe"],
            "max_drawdown_pct": res["max_drawdown_pct"],
            "num_trades": res["num_trades"],
            "win_rate": res["win_rate"],
        })
    _print_and_save_grid(rows)

    # Melhor Sharpe entre os que têm pelo menos alguns trades (min_votes que
    # não geram nenhum trade não servem de comparação -- ver nota no README
    # do PR sobre min_votes=6 ser estruturalmente impossível com 5 sinais).
    viable = [r for r in rows if r["num_trades"] >= 3]
    if not viable:
        print("\nNenhum min_votes gerou pelo menos 3 trades no período -- não dá pra escolher um vencedor com confiança.")
        return
    best = max(viable, key=lambda r: r["sharpe"])
    print(f"\nMelhor min_votes por Sharpe (com >=3 trades): {best['min_votes']} "
          f"(sharpe={best['sharpe']:.3f}, win_rate={best['win_rate']*100:.1f}%, "
          f"max_drawdown={best['max_drawdown_pct']:.2f}%)")

    best_res = results_by_min_votes[best["min_votes"]]
    print(f"\n=== Recalibrando kelly_position_size com stats reais do min_votes={best['min_votes']} ===")
    print(f"win_rate real: {best_res['win_rate']:.4f}  avg_win real: {best_res['avg_win']:.4f}  "
          f"avg_loss_abs real: {best_res['avg_loss_abs']:.4f}")
    print("(priors placeholder eram: win_rate=0.5, avg_win=0.05, avg_loss=0.03)")

    engine_best = ConfluenceEngine(min_votes=best["min_votes"], kelly_fraction=0.3)
    recalibrated = run_backtest(
        mu_df, engine_best, sector_returns=sector_returns,
        kelly_win_rate=best_res["win_rate"],
        kelly_avg_win=best_res["avg_win"],
        kelly_avg_loss=best_res["avg_loss_abs"],
    )
    print(f"\ntotal_return_pct  antes (priors placeholder): {best_res['total_return_pct']:.2f}")
    print(f"total_return_pct  depois (kelly recalibrado):  {recalibrated['total_return_pct']:.2f}")
    print(f"kelly_size_frac_used antes:  {best_res['kelly_size_frac_used']:.4f}")
    print(f"kelly_size_frac_used depois: {recalibrated['kelly_size_frac_used']:.4f}")


if __name__ == "__main__":
    main()
