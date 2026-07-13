"""Estudo de correlação: mercados globais overnight x gap de abertura da
Nasdaq -- NÃO é invocado pelo Node via subprocess, ao contrário dos outros
arquivos em agent/. Rode direto:

    cd artifacts/api-server/src/agent/scripts
    python3 backtest_global_market_pulse.py

Ou de qualquer diretório (o sys.path.insert abaixo resolve o import).

Contexto: get_global_market_snapshot() (market_alerts.py) devolve só dado
bruto -- variação % do último pregão de Nikkei/KOSPI/Hang Seng/DAX/FTSE/CAC/
EUR-USD/futuros Nasdaq -- deliberadamente SEM pontuação/threshold embutido.
Este script responde a pergunta anterior a qualquer estratégia: será que o
movimento desses mercados de fato ANTECIPA o gap de abertura da Nasdaq, ou é
ruído? NÃO backtesta uma estratégia de compra/venda (não existe nenhuma aqui)
-- só mede correlação e hit-rate de direção, em DOIS REGIMES (rali recente x
correção 2022-2023), mesma metodologia de backtest_confluence.py.

Métricas por mercado global, alinhadas pela mesma data de calendário da
Nasdaq (a sessão de Tóquio/Coreia/Hong Kong de um dia D fecha ANTES da
Nasdaq abrir no mesmo dia D; Europa está em overlap direto com a manhã da
Nasdaq no dia D -- em ambos os casos "mesma data" já captura a relação de
antecedência que interessa):
  - corr_gap:      correlação entre a variação do fechamento do mercado
                    global no dia D e o GAP de abertura da Nasdaq no dia D
                    (Open de hoje vs. Close de ontem).
  - hit_rate_gap:  % de dias em que o SINAL da variação do mercado global
                    bate com o sinal do gap da Nasdaq (mais fácil de
                    interpretar que o coeficiente de correlação).
  - corr_day:      correlação com a variação do pregão INTEIRO da Nasdaq
                    (fechamento a fechamento) -- serve pra ver se um efeito
                    encontrado no gap sobrevive ao dia ou se dilui/reverte.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf

TARGET_TICKER = "^IXIC"
TARGET_LABEL = "Nasdaq Composite"

GLOBAL_TICKERS: dict[str, str] = {
    "^N225":    "Nikkei 225 (Japao)",
    "^KS11":    "KOSPI Composite (Coreia)",
    "^HSI":     "Hang Seng (Hong Kong)",
    "^GDAXI":   "DAX (Alemanha)",
    "^FTSE":    "FTSE 100 (Reino Unido)",
    "^FCHI":    "CAC 40 (Franca)",
    "EURUSD=X": "EUR/USD",
    "NQ=F":     "Nasdaq 100 futuros",
}

RESULTS_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "global_market_correlation_results.md"
)

# Mesmos dois regimes de backtest_confluence.py: rali recente (supercycle
# HBM/IA) vs. correção 2022-2023 (memory downcycle + selloff de juros) --
# um efeito overnight genuíno devia aparecer nos dois, não só num deles.
REGIMES: dict[str, dict] = {
    "recente_2y (supercycle HBM/IA)": {"period": "2y"},
    "correcao_2022_2023 (memory downcycle + selloff de juros)": {"start": "2022-01-01", "end": "2023-06-30"},
}


def _fetch_history(ticker: str, period: str | None = None, start: str | None = None,
                    end: str | None = None) -> "pd.DataFrame | None":
    try:
        if start is not None:
            df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
        else:
            df = yf.Ticker(ticker).history(period=period or "2y", interval="1d", auto_adjust=False)
        if df is None or df.empty or len(df) < 3:
            return None
        # Cada bolsa devolve o index com o fuso horário local dela (Tóquio,
        # Frankfurt, Nova York...) -- removemos o tz e mantemos só a data,
        # que é exatamente o que precisamos pra alinhar "mesmo dia de
        # calendário" entre mercados diferentes.
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()
        return df
    except Exception as exc:
        print(f"AVISO: falha ao buscar {ticker}: {exc}", file=sys.stderr)
        return None


def _day_change_series(df: pd.DataFrame) -> pd.Series:
    return df["Close"].pct_change() * 100.0


def _gap_series(df: pd.DataFrame) -> pd.Series:
    """Gap de abertura: Open de hoje vs. Close de ontem."""
    return (df["Open"] / df["Close"].shift(1) - 1) * 100.0


def _correlation(a: pd.Series, b: pd.Series) -> "tuple[float, int]":
    joined = pd.concat([a, b], axis=1).dropna()
    if len(joined) < 5:
        return float("nan"), len(joined)
    corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
    return float(corr), len(joined)


def _hit_rate(a: pd.Series, b: pd.Series) -> "tuple[float, int]":
    """% de dias em que o sinal de `a` (mercado global) bate com o sinal de
    `b` (gap/variação da Nasdaq), entre os dias em que ambos têm dado."""
    joined = pd.concat([a, b], axis=1).dropna()
    joined = joined[(joined.iloc[:, 0] != 0) & (joined.iloc[:, 1] != 0)]
    if joined.empty:
        return float("nan"), 0
    same_sign = np.sign(joined.iloc[:, 0]) == np.sign(joined.iloc[:, 1])
    return float(same_sign.mean() * 100.0), len(joined)


def _run_regime(regime_label: str, fetch_kwargs: dict) -> str:
    print(f"\n\n{'#' * 78}\nREGIME: {regime_label}  ({fetch_kwargs})\n{'#' * 78}")

    target_df = _fetch_history(TARGET_TICKER, **fetch_kwargs)
    if target_df is None:
        msg = f"ERRO: não consegui buscar {TARGET_TICKER} pro regime '{regime_label}'"
        print(msg, file=sys.stderr)
        return f"## {regime_label}\n\n{msg}\n"

    target_gap = _gap_series(target_df)
    target_day_change = _day_change_series(target_df)

    rows = []
    for ticker, label in GLOBAL_TICKERS.items():
        df = _fetch_history(ticker, **fetch_kwargs)
        if df is None:
            print(f"AVISO: sem dados de {ticker} ({label}) pro regime '{regime_label}'", file=sys.stderr)
            rows.append({
                "ticker": ticker, "label": label, "n": 0,
                "corr_gap": float("nan"), "hit_rate_gap": float("nan"), "corr_day": float("nan"),
            })
            continue

        global_change = _day_change_series(df).reindex(target_gap.index)

        corr_gap, n_gap = _correlation(global_change, target_gap)
        hit_gap, _ = _hit_rate(global_change, target_gap)
        corr_day, _ = _correlation(global_change, target_day_change)

        rows.append({
            "ticker": ticker, "label": label, "n": n_gap,
            "corr_gap": corr_gap, "hit_rate_gap": hit_gap, "corr_day": corr_day,
        })

    df_rows = pd.DataFrame(rows)
    print(f"\nVariação do fechamento do mercado global  x  gap de abertura da {TARGET_LABEL} "
          f"(mesma data de calendário)\n")
    print(df_rows[["ticker", "label", "n", "corr_gap", "hit_rate_gap", "corr_day"]].to_string(index=False))

    md = [
        f"## {regime_label}\n",
        f"Alvo: {TARGET_LABEL} ({TARGET_TICKER}). "
        f"Período: {target_df.index[0].date()} a {target_df.index[-1].date()}.\n",
        "| Mercado | Ticker | N dias | Corr. x gap abertura | Hit-rate direção (%) | Corr. x variação dia todo |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r['label']} | {r['ticker']} | {r['n']} | {r['corr_gap']:.3f} | "
            f"{r['hit_rate_gap']:.1f}% | {r['corr_day']:.3f} |"
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
        f.write("# Correlação: mercados globais overnight x gap de abertura da Nasdaq\n\n")
        f.write(
            "Mede se a variação do fechamento de Nikkei/KOSPI/Hang Seng/DAX/FTSE/CAC/EUR-USD/"
            "futuros Nasdaq antecipa o gap de abertura da Nasdaq (^IXIC) no mesmo dia de "
            "calendário. `corr_gap`/`hit_rate_gap` usam o gap real (Open de hoje vs. Close de "
            "ontem da Nasdaq); `corr_day` usa a variação do pregão inteiro, pra ver se o efeito "
            "(quando existe) sobrevive além da abertura ou se dilui ao longo do dia. NÃO testa "
            "nenhuma estratégia de compra/venda -- só mede se o dado tem poder preditivo antes "
            "de decidir se vale a pena construir um sinal em cima dele.\n\n"
        )
        f.write("\n\n".join(sections))
    print(f"\nRelatório completo salvo em {RESULTS_MD_PATH}")


if __name__ == "__main__":
    main()
