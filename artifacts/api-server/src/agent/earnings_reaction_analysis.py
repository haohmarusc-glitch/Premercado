"""
Analisa o comportamento histórico de preço/volume nos dias de earnings de um
conjunto de tickers, pra parametrizar a volatilidade esperada em vez de
confiar no "calor do momento" (Fear & Greed Index) na hora de decidir uma
operação de day trade ou swing trade em torno de um resultado.

Pra cada earnings passado, calcula:
  - gap de abertura (open vs fechamento do pregão anterior)
  - variação de fechamento (close vs fechamento do pregão anterior)
  - range intradiário (high-low vs fechamento do pregão anterior)
  - volume vs média do período

O yfinance não informa de forma confiável se o resultado foi divulgado antes
da abertura (BMO -- reage no PRÓPRIO pregão) ou depois do fechamento (AMC --
reage no pregão SEGUINTE). Em vez de adivinhar, o script reporta as DUAS
janelas por evento ("dia do anúncio" e "dia seguinte") -- na prática, a que
tiver o gap/volume nitidamente maior costuma ser a reação real.

Uso:
    python3 -m agent.earnings_reaction_analysis
    python3 -m agent.earnings_reaction_analysis --tickers NVDA,SMCI,AVGO
    python3 -m agent.earnings_reaction_analysis --lookback 6 --json
"""
import argparse
import json

# bounded_parallel é importado dos DOIS jeitos porque este script roda dos dois
# jeitos: como arquivo solto (scenarios.ts spawna por caminho, sem PYTHONPATH --
# aí sys.path[0] é o próprio diretório agent/) e, em outros pontos, como módulo
# do pacote. Só stdlib dentro dele, então o import flat é seguro.
try:
    from bounded_parallel import deadline_exceeded
except ImportError:
    from agent.bounded_parallel import deadline_exceeded

import sys

import pandas as pd
import yfinance as yf

DEFAULT_TICKERS = ["NVDA", "SMCI", "AVGO", "SKHY", "ARM"]


def _session_move(hist: pd.DataFrame, pos: int, prev_close: float) -> dict | None:
    """Métricas de um único pregão (índice `pos` em `hist`) relativas ao
    fechamento anterior `prev_close`. None se `pos` estiver fora do range."""
    if pos < 0 or pos >= len(hist.index):
        return None
    day = hist.iloc[pos]
    if prev_close in (None, 0) or pd.isna(prev_close):
        return None
    return {
        "date": str(hist.index[pos].date()),
        "gap_pct": round(float((day["Open"] - prev_close) / prev_close * 100), 2),
        "close_pct": round(float((day["Close"] - prev_close) / prev_close * 100), 2),
        "intraday_range_pct": round(float((day["High"] - day["Low"]) / prev_close * 100), 2),
        "volume": float(day["Volume"]),
    }


def analyze_ticker(ticker: str, lookback_events: int = 8) -> dict:
    t = yf.Ticker(ticker)

    try:
        earnings = t.get_earnings_dates(limit=lookback_events + 6)
    except Exception as e:
        return {"ticker": ticker, "error": f"falha ao buscar earnings dates: {type(e).__name__}: {e}"}

    if earnings is None or earnings.empty:
        return {"ticker": ticker, "error": "sem histórico de earnings dates disponível (ticker muito novo?)"}

    now = pd.Timestamp.now(tz=earnings.index.tz)
    past_earnings = earnings[earnings.index < now].head(lookback_events)
    if past_earnings.empty:
        return {"ticker": ticker, "error": "sem earnings passados na janela pedida"}

    earliest = past_earnings.index.min()
    start = (earliest - pd.Timedelta(days=10)).tz_localize(None).strftime("%Y-%m-%d")
    try:
        hist = t.history(start=start, auto_adjust=False)
    except Exception as e:
        return {"ticker": ticker, "error": f"falha ao buscar histórico de preço: {type(e).__name__}: {e}"}
    if hist.empty:
        return {"ticker": ticker, "error": "sem histórico de preço no período"}
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    avg_volume = float(hist["Volume"].mean())

    events = []
    for earnings_ts in past_earnings.index:
        earnings_date = earnings_ts.tz_localize(None).normalize()
        # Pregão do próprio dia (ou o próximo disponível, se cair em fim de
        # semana/feriado) -- searchsorted("left") já resolve os dois casos.
        pos = hist.index.searchsorted(earnings_date)
        if pos == 0 or pos >= len(hist.index):
            continue  # sem pregão anterior pra calcular a variação, ou fora do histórico
        prev_close = float(hist["Close"].iloc[pos - 1])

        announcement_day = _session_move(hist, pos, prev_close)
        next_day = _session_move(hist, pos + 1, hist["Close"].iloc[pos] if pos < len(hist.index) else None)

        events.append({
            "earnings_date": str(earnings_date.date()),
            "announcement_day": announcement_day,
            "next_day": next_day,
        })

    if not events:
        return {"ticker": ticker, "error": "não foi possível casar earnings dates com pregões do histórico"}

    # Pra estatística, usa o maior |close_pct| entre as duas janelas de cada
    # evento -- é o proxy mais simples pra "qual das duas foi a reação real"
    # sem assumir BMO/AMC.
    reaction_moves = []
    for e in events:
        candidates = [m for m in (e["announcement_day"], e["next_day"]) if m is not None]
        if not candidates:
            continue
        reaction_moves.append(max(candidates, key=lambda m: abs(m["close_pct"])))

    if not reaction_moves:
        return {"ticker": ticker, "error": "sem janelas válidas de reação nos eventos encontrados"}

    df = pd.DataFrame(reaction_moves)
    volume_ratio = df["volume"] / avg_volume if avg_volume else None
    summary = {
        "n_events": len(df),
        "gap_pct_mean": round(float(df["gap_pct"].mean()), 2),
        "gap_pct_abs_mean": round(float(df["gap_pct"].abs().mean()), 2),
        "close_pct_mean": round(float(df["close_pct"].mean()), 2),
        "close_pct_abs_mean": round(float(df["close_pct"].abs().mean()), 2),
        "close_pct_std": round(float(df["close_pct"].std()), 2) if len(df) > 1 else None,
        "intraday_range_pct_mean": round(float(df["intraday_range_pct"].mean()), 2),
        "volume_ratio_mean": round(float(volume_ratio.mean()), 2) if volume_ratio is not None else None,
        # Nível sugerido pra calibrar um alerta pós-earnings: |média| + 1
        # desvio-padrão do movimento de fechamento -- mesma lógica de
        # threshold_pct ≈ atr_pct * 1.5 já usada em create_alert (ver
        # agent.py), só que calibrada pela reação histórica a earnings
        # específica desse ticker em vez do ATR do dia a dia.
        "suggested_threshold_pct": round(
            float(df["close_pct"].abs().mean() + (df["close_pct"].std() if len(df) > 1 else 0)), 2
        ),
    }

    # Níveis de preço em $ pra visualização direta -- projeção estatística das
    # bandas acima sobre o preço atual, NÃO suporte/resistência técnico real
    # (não vem de estrutura de preço, só da magnitude histórica de reação a
    # earnings). R1/S1 = movimento médio; R2/S2 = o mesmo threshold_pct
    # sugerido pra alerta, como alvo/risco extremo.
    current_price = float(hist["Close"].iloc[-1])
    avg_frac = summary["close_pct_abs_mean"] / 100
    extreme_frac = summary["suggested_threshold_pct"] / 100
    summary["current_price"] = round(current_price, 2)
    summary["r1_price"] = round(current_price * (1 + avg_frac), 2)
    summary["r2_price"] = round(current_price * (1 + extreme_frac), 2)
    summary["s1_price"] = round(current_price * (1 - avg_frac), 2)
    summary["s2_price"] = round(current_price * (1 - extreme_frac), 2)

    return {"ticker": ticker, "summary": summary, "events": events}


def _print_report(results: list[dict]) -> None:
    for r in results:
        print(f"\n=== {r['ticker']} ===")
        if "error" in r:
            print(f"  ⚠ {r['error']}")
            continue
        s = r["summary"]
        std = f"{s['close_pct_std']:.2f}" if s["close_pct_std"] is not None else "N/A"
        vol = f"{s['volume_ratio_mean']:.2f}x" if s["volume_ratio_mean"] is not None else "N/A"
        print(f"  Eventos analisados: {s['n_events']}")
        print(f"  Gap de abertura médio: {s['gap_pct_mean']:+.2f}%  (|média| {s['gap_pct_abs_mean']:.2f}%)")
        print(f"  Variação de fechamento média: {s['close_pct_mean']:+.2f}%  (|média| {s['close_pct_abs_mean']:.2f}%, desvio {std})")
        print(f"  Range intradiário médio: {s['intraday_range_pct_mean']:.2f}%")
        print(f"  Volume vs média do período: {vol}")
        print(f"  Threshold sugerido pra alerta pós-earnings: ±{s['suggested_threshold_pct']:.2f}%")
        print(f"  Preço atual: ${s['current_price']:.2f}")
        print(f"  R2 (+{s['suggested_threshold_pct']:.2f}%): ${s['r2_price']:.2f}  |  R1 (+{s['close_pct_abs_mean']:.2f}%): ${s['r1_price']:.2f}")
        print(f"  S1 (-{s['close_pct_abs_mean']:.2f}%): ${s['s1_price']:.2f}  |  S2 (-{s['suggested_threshold_pct']:.2f}%): ${s['s2_price']:.2f}")
        print("  Últimos eventos (dia do anúncio | dia seguinte):")
        for e in r["events"][:6]:
            a = e["announcement_day"]
            n = e["next_day"]
            a_txt = f"gap {a['gap_pct']:+.2f}% / fech {a['close_pct']:+.2f}%" if a else "sem pregão"
            n_txt = f"gap {n['gap_pct']:+.2f}% / fech {n['close_pct']:+.2f}%" if n else "sem pregão"
            print(f"    {e['earnings_date']}: [{a_txt}]  |  [{n_txt}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--lookback", type=int, default=8, help="Quantos earnings passados considerar por ticker")
    parser.add_argument("--json", action="store_true", help="Saída em JSON em vez de texto formatado")
    args = parser.parse_args()

    tickers = [tk.strip().upper() for tk in args.tickers.split(",") if tk.strip()]
    results = [analyze_ticker(tk, args.lookback) for tk in tickers]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_report(results)


if __name__ == "__main__":
    # Chamado pelo Node com payload JSON no stdin (rota web,
    # routes/earnings-reaction.ts) -- stdin não é um terminal (isatty()=False)
    # nesse caso. Rodando manualmente sem nada de pipe, cai no CLI de
    # argparse normal (--tickers/--json/etc.), mesmo comportamento de sempre.
    _raw_stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    if _raw_stdin.strip():
        _payload = json.loads(_raw_stdin)
        _tickers = [str(tk).strip().upper() for tk in (_payload.get("tickers") or DEFAULT_TICKERS) if str(tk).strip()]
        _lookback = int(_payload.get("lookback") or 8)
        _saida = []
        for tk in _tickers:
            # Resultado parcial vale mais que timeout: sem isto o laço roda até
            # o Node matar o processo e tudo que já foi analisado se perde.
            if deadline_exceeded():
                _saida.append({"ticker": tk, "error": "orçamento de tempo esgotado"})
                continue
            _saida.append(analyze_ticker(tk, _lookback))
        print(json.dumps(_saida, ensure_ascii=False))
    else:
        main()
