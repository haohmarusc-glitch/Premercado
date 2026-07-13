"""Testa um FILTRO DE REGIME automático pra ligar/desligar a estratégia de
sinal europeu (backtest_global_signal_strategy.py) -- NÃO é invocado pelo
Node via subprocess, mesma convenção dos outros scripts em agent/. Rode
direto:

    cd artifacts/api-server/src/agent/scripts
    python3 backtest_regime_switch_strategy.py

Ou de qualquer diretório (o sys.path.insert abaixo resolve o import).

Contexto: já sabemos (memory doc + PRs #54-#56) que o sinal europeu
(DAX+CAC+FTSE) só sobrevive ao custo de transação real em regime de
correção/lateral -- no rali forte ele perde pro buy&hold sozinho. Até aqui
isso foi validado rotulando os dois regimes NA MÃO ("recente_2y" vs
"correcao_2022_2023"). Este script testa se dá pra detectar isso
automaticamente, sem rotular nada, e comparar:

  (a) buy & hold sempre
  (b) sinal europeu sempre ligado (já testado antes)
  (c) HÍBRIDO: filtro de regime decide -- em alta, fica 100% comprado
      passivo (igual ao buy&hold); fora de alta, liga o sinal europeu
      (long/flat ou long/short).

Filtro de regime: SMA de janela configurável (SMA_WINDOWS abaixo, por
padrão 100 E 200 lado a lado) do próprio alvo (Nasdaq, ^IXIC). Close > SMA
-> "alta" (desliga o sinal, buy&hold passivo); Close <= SMA -> "correção/
lateral" (liga o sinal europeu). Simples de propósito -- é o filtro de
tendência mais padrão que existe; não é o objetivo aqui inventar um
classificador de regime sofisticado antes de saber se a ideia básica já
ajuda.

Motivo de testar os dois: o resultado real da SMA100 (PR #57/#58) mostrou
que ela discrimina os regimes de verdade (78,4% "alta" no rali vs. 36,9%
na correção), mas é rápida/ruidosa demais -- fica alternando "alta"/
"correção" DENTRO do próprio rali, e cada troca de regime é uma operação
com custo. Uma SMA mais lenta (200) deve ser mais "pegajosa" (menos trocas
falsas dentro de uma tendência sustentada), o que devia reduzir esse custo
extra especificamente no regime de rali -- é essa hipótese que este script
testa, comparando SMA100 e SMA200 lado a lado no mesmo run.

Busca com warmup extra (a maior SMA testada precisa do próprio tanto de
pregões de histórico ANTES do início do período de teste pra já estar
válida no primeiro dia) e recorta pro período de teste depois -- mesmo
padrão de _trim_to_window em backtest.py. Custo de transação: mesmos
defaults de backtest.py, cobrado por perna a cada troca de posição
(incluindo a troca causada pelo próprio filtro de regime, que também é
uma operação real).

Reporta, pra cada regime x janela de SMA testada: % de dias classificados
como "alta" pelo filtro (valida se o filtro faz sentido -- espera-se
maioria "alta" no regime de rali e maioria "correção" no regime de
correção), e as métricas líquidas de custo do híbrido vs. buy&hold vs.
sinal sempre ligado.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf

TARGET_TICKER = "^IXIC"
TARGET_LABEL = "Nasdaq Composite"

# Único sinal que sobreviveu ao custo real (PR #56) -- Ásia fica de fora.
EUROPE_TICKERS = {"^GDAXI": "DAX", "^FCHI": "CAC 40", "^FTSE": "FTSE 100"}

SMA_WINDOWS = [100, 200]
WARMUP_CALENDAR_DAYS = int(max(SMA_WINDOWS) * 1.6)  # folga pra feriados/fins de semana

RESULTS_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "regime_switch_strategy_results.md"
)

REGIMES: dict[str, dict] = {
    "recente_2y (supercycle HBM/IA)": {"period": "2y"},
    "correcao_2022_2023 (memory downcycle + selloff de juros)": {"start": "2022-01-01", "end": "2023-06-30"},
}

INITIAL_CAPITAL = 10_000.0
COMMISSION_PCT = 0.001
SLIPPAGE_PCT = 0.0005


def _fetch_history(ticker: str, start: str | None = None, end: str | None = None) -> "pd.DataFrame | None":
    try:
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
        if df is None or df.empty or len(df) < 3:
            return None
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()
        return df
    except Exception as exc:
        print(f"AVISO: falha ao buscar {ticker}: {exc}", file=sys.stderr)
        return None


def _requested_start(fetch_kwargs: dict) -> pd.Timestamp:
    """Primeiro dia do período de teste pedido (sem warmup) -- usado só pra
    saber onde recortar depois de calcular a SMA com o histórico extra."""
    if "start" in fetch_kwargs:
        return pd.Timestamp(fetch_kwargs["start"]).normalize()
    period = fetch_kwargs.get("period", "2y")
    years = float(period.rstrip("y"))
    return (pd.Timestamp.today().normalize() - pd.Timedelta(days=int(years * 365.25)))


def _fetch_with_warmup(ticker: str, fetch_kwargs: dict) -> "tuple[pd.DataFrame, pd.Timestamp] | tuple[None, None]":
    req_start = _requested_start(fetch_kwargs)
    warm_start = req_start - pd.Timedelta(days=WARMUP_CALENDAR_DAYS)
    df = _fetch_history(ticker, start=warm_start.strftime("%Y-%m-%d"), end=fetch_kwargs.get("end"))
    if df is None:
        return None, None
    return df, req_start


def _day_change_series(df: pd.DataFrame) -> pd.Series:
    return df["Close"].pct_change() * 100.0


def _intraday_return_series(df: pd.DataFrame) -> pd.Series:
    return df["Close"] / df["Open"] - 1.0


def _equity_metrics(equity: pd.Series) -> dict:
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


def _simulate_position(position: pd.Series, intraday_return: pd.Series,
                        commission_pct: float = 0.0, slippage_pct: float = 0.0) -> dict:
    joined = pd.concat([position, intraday_return], axis=1).dropna()
    joined.columns = ["position", "ret"]
    if len(joined) < 5:
        return {"error": "dados insuficientes", "num_trades": 0}

    position_prev = joined["position"].shift(1).fillna(0.0)
    turnover = (joined["position"] - position_prev).abs()
    cost_drag = turnover * (commission_pct + slippage_pct)

    strat_returns = joined["position"] * joined["ret"] - cost_drag
    equity = INITIAL_CAPITAL * (1 + strat_returns).cumprod()
    metrics = _equity_metrics(equity)

    traded = strat_returns[joined["position"] != 0]
    num_trades = int((joined["position"] != 0).sum())
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

    target_full, req_start = _fetch_with_warmup(TARGET_TICKER, fetch_kwargs)
    if target_full is None:
        msg = f"ERRO: não consegui buscar {TARGET_TICKER} pro regime '{regime_label}'"
        print(msg, file=sys.stderr)
        return f"## {regime_label}\n\n{msg}\n"

    uptrend_full_by_window = {
        w: target_full["Close"] > target_full["Close"].rolling(w).mean() for w in SMA_WINDOWS
    }

    europe_series = []
    for ticker in EUROPE_TICKERS:
        df, _ = _fetch_with_warmup(ticker, fetch_kwargs)
        if df is None:
            print(f"AVISO: sem dados de {ticker} -- seguindo sem ele", file=sys.stderr)
            continue
        europe_series.append(_day_change_series(df).reindex(target_full.index))
    if not europe_series:
        msg = f"ERRO: nenhum ticker europeu disponível pro regime '{regime_label}'"
        print(msg, file=sys.stderr)
        return f"## {regime_label}\n\n{msg}\n"
    europe_signal_full = pd.concat(europe_series, axis=1).mean(axis=1)

    # Recorta pro período de teste pedido -- as SMAs/sinal já usaram o
    # warmup, o teste olha só [req_start, fim].
    mask = target_full.index >= req_start
    target_df = target_full.loc[mask]
    uptrend_by_window = {w: s.loc[mask] for w, s in uptrend_full_by_window.items()}
    europe_signal = europe_signal_full.loc[mask]
    intraday_return = _intraday_return_series(target_df)

    for w, uptrend in uptrend_by_window.items():
        pct_uptrend = float(uptrend.mean() * 100)
        print(f"\nFiltro de regime (Close > SMA{w}): {pct_uptrend:.1f}% dos dias classificados 'alta' "
              f"({len(uptrend)} dias no período de teste, {target_df.index[0].date()} a {target_df.index[-1].date()}).")

    bh_equity = INITIAL_CAPITAL * (target_df["Close"] / target_df["Close"].iloc[0])
    bh = _equity_metrics(bh_equity)
    print(f"\nBuy & hold {TARGET_LABEL}: {_fmt({**bh, 'num_trades': len(target_df), 'win_rate': float('nan')})}")

    md = [
        f"## {regime_label}\n",
        f"Alvo: {TARGET_LABEL} ({TARGET_TICKER}). Período de teste: "
        f"{target_df.index[0].date()} a {target_df.index[-1].date()}.\n",
    ]
    for w, uptrend in uptrend_by_window.items():
        pct_uptrend = float(uptrend.mean() * 100)
        md.append(f"Filtro de regime (Close > SMA{w}): **{pct_uptrend:.1f}% dos dias classificados 'alta'**.\n")
    md += [
        f"Buy & hold: retorno={bh['total_return_pct']:.2f}%, cagr={bh['cagr']*100:.2f}%, "
        f"sharpe={bh['sharpe']:.3f}, max_dd={bh['max_drawdown_pct']:.2f}%\n",
        "| Estratégia | Retorno (bruto) | Retorno (líquido) | Sharpe (bruto) | Sharpe (líquido) | "
        "Max DD (líquido) | Trades | Win rate (líquido) |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for mode, mode_label in (("long_flat", "long/flat"), ("long_short", "long/short")):
        if mode == "long_flat":
            europe_position = pd.Series(np.where(europe_signal > 0, 1.0, 0.0), index=europe_signal.index)
        else:
            europe_position = pd.Series(np.sign(europe_signal), index=europe_signal.index)

        # Sinal europeu sempre ligado (baseline já visto em backtest_global_signal_strategy.py,
        # recalculado aqui pra comparar lado a lado com o híbrido no mesmo run). Independe da
        # janela de SMA, só precisa ser reportado uma vez por modo.
        gross_always = _simulate_position(europe_position, intraday_return)
        net_always = _simulate_position(europe_position, intraday_return, COMMISSION_PCT, SLIPPAGE_PCT)

        rows = [(f"sinal europeu sempre ligado ({mode_label})", gross_always, net_always)]

        for w, uptrend in uptrend_by_window.items():
            # Híbrido: em alta (uptrend=True) fica 100% comprado passivo;
            # fora de alta, usa a posição do sinal europeu.
            hybrid_position = pd.Series(
                np.where(uptrend.reindex(europe_position.index).fillna(False), 1.0, europe_position),
                index=europe_position.index,
            )
            gross_hybrid = _simulate_position(hybrid_position, intraday_return)
            net_hybrid = _simulate_position(hybrid_position, intraday_return, COMMISSION_PCT, SLIPPAGE_PCT)
            rows.append((f"híbrido: SMA{w} liga/desliga ({mode_label})", gross_hybrid, net_hybrid))

        for label, gross, net in rows:
            print(f"\n[{label}]")
            print(f"  bruto:    {_fmt(gross)}")
            print(f"  líquido:  {_fmt(net)}")
            if "error" in gross or "error" in net:
                md.append(f"| {label} | erro | erro | | | | | |")
            else:
                md.append(
                    f"| {label} | {gross['total_return_pct']:.2f}% | {net['total_return_pct']:.2f}% | "
                    f"{gross['sharpe']:.3f} | {net['sharpe']:.3f} | {net['max_drawdown_pct']:.2f}% | "
                    f"{net['num_trades']} | {net['win_rate']*100:.1f}% |"
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
        windows_str = " e ".join(f"SMA{w}" for w in SMA_WINDOWS)
        f.write(f"# Backtest: filtro de regime ({windows_str}) liga/desliga a estratégia de sinal europeu\n\n")
        f.write(
            f"Testa se um filtro de tendência simples (Close do Nasdaq vs. sua própria "
            f"{windows_str}) detecta automaticamente o mesmo regime que rotulamos na mão em "
            "backtest_global_signal_strategy.py -- e se o híbrido resultante (buy&hold passivo "
            "em alta, sinal europeu fora de alta) preserva o edge da correção sem perder tanto "
            "no rali. Compara SMA100 (já testada em PR #57, rápida/ruidosa) com SMA200 (mais "
            "lenta/pegajosa) lado a lado, pra ver se a janela mais lenta reduz as trocas falsas "
            "de regime dentro de um rali sustentado. Retorno/Sharpe reportados BRUTO (sem custo) "
            "e LÍQUIDO (custo igual a backtest.py, cobrado a cada troca de posição, inclusive a "
            "troca causada pelo próprio filtro de regime).\n\n"
        )
        f.write("\n\n".join(sections))
    print(f"\nRelatório completo salvo em {RESULTS_MD_PATH}")


if __name__ == "__main__":
    main()
