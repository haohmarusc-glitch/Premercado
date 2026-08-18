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
    import earnings_dates as _earnings_dates
    import market_data_provider
except ImportError:
    from agent.bounded_parallel import deadline_exceeded
    from agent import earnings_dates as _earnings_dates
    from agent import market_data_provider

import sys

import pandas as pd
import yfinance as yf
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


DEFAULT_TICKERS = ["NVDA", "SMCI", "AVGO", "SKHY", "ARM"]


# ~1 mês em pregões, mesma convenção de janela em pregões (não dias corridos)
# de SIX_MONTHS_TRADING_DAYS em entry_exit_study.py.
RUNUP_PREGOES = 21
# Corte de "chegou esticado": run-up de dois dígitos no mês pré-earnings.
# Fixo e documentado de propósito -- um corte por volatilidade do ticker
# seria mais fino, mas deixaria de ser comparável entre papéis e viraria
# caixa-preta; começa simples, calibra depois com os próprios dados.
RUNUP_ESTICADO_PCT = 10.0


def _runup_pct(hist: pd.DataFrame, pos: int) -> float | None:
    """Variação % do fechamento RUNUP_PREGOES pregões antes do balanço até o
    fechamento da véspera (pos-1). None quando o histórico não alcança."""
    ini = pos - 1 - RUNUP_PREGOES
    if ini < 0:
        return None
    base = float(hist["Close"].iloc[ini])
    if not base or pd.isna(base):
        return None
    prev_close = float(hist["Close"].iloc[pos - 1])
    return round((prev_close / base - 1) * 100, 2)


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

    # get_earnings_dates é a chamada mais instável do yfinance e não passa pela
    # cadeia de fallback (que cuida de série de PREÇO). Sem retry nem cache, uma
    # resposta vazia passageira derrubava o painel inteiro -- visto em produção
    # (NBIS, 17/08/2026 11:36 BRT) minutos depois do MESMO script funcionar no
    # terminal. Ver agent/earnings_dates.py.
    _limite = lookback_events + 6
    earnings, _fonte_datas, _erro_datas = _earnings_dates.buscar(
        ticker, lambda: t.get_earnings_dates(limit=_limite), limit=_limite
    )
    if earnings is None:
        return {"ticker": ticker, "error": f"falha ao buscar earnings dates: {_erro_datas}"}
    if earnings.empty:
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
    # A barra do DIA CORRENTE vem sem Close antes do fechamento, e é a ÚLTIMA
    # linha -- `hist["Close"].iloc[-1]` pegava NaN, que json_seguro converte em
    # null, deixando current_price e os quatro níveis (que derivam dele) vazios
    # na tela. Visto em produção 18/08/2026, SNDK: "base: —" com R1/R2/S1/S2
    # todos em branco enquanto gap, fechamento e volume vinham certos.
    #
    # Este script chama t.history() DIRETO, sem passar pelo market_data_
    # provider -- então a fachada que limpa isso lá não o alcança. Mesmo
    # helper, aplicado aqui na mão.
    hist = market_data_provider.sem_barra_incompleta(hist)
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
            # Quanto o papel subiu/caiu no ~mês (RUNUP_PREGOES pregões) ANTES
            # do balanço -- proxy de expectativa embutida no preço. Motivado
            # pelo padrão "bom não é bom o suficiente" visto em produção
            # (ago/2026): SKHY com lucro recorde caiu ~9% e META caiu 8% no
            # earnings, ambas chegando esticadas; DELL/HPE chegando sem
            # euforia saltaram +32%/+19% -- a direção da reação dependeu mais
            # do run-up prévio que do resultado em si.
            "runup_pct": _runup_pct(hist, pos),
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
        escolhido = dict(max(candidates, key=lambda m: abs(m["close_pct"])))
        escolhido["runup_pct"] = e["runup_pct"]
        reaction_moves.append(escolhido)

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

    summary["runup"] = _runup_summary(df, hist, _ultimo_earnings_pos(hist, past_earnings.index))

    saida = {"ticker": ticker, "summary": summary, "events": events}
    # Mesmo vocabulário de degradação de get_trend.py: dado servido de cópia
    # vencida vem MARCADO. O silêncio é que seria a piora -- um painel completo
    # e sem aviso, calculado sobre uma agenda velha, não se distingue do bom.
    if _fonte_datas == "cache_vencido":
        saida["stale"] = True
        saida["fonteDatasEarnings"] = _fonte_datas
    return saida


def _ultimo_earnings_pos(hist: pd.DataFrame, earnings_index) -> int | None:
    """Posição em `hist` do pregão de reação do earnings mais RECENTE.
    None quando nenhum earnings passado casa com um pregão do histórico."""
    posicoes = []
    for ts in earnings_index:
        pos = hist.index.searchsorted(ts.tz_localize(None).normalize())
        if 0 < pos < len(hist.index):
            posicoes.append(int(pos))
    return max(posicoes) if posicoes else None


def _runup_summary(df: pd.DataFrame, hist: pd.DataFrame, ultimo_earnings_pos: int | None = None) -> dict:
    """Estatística do padrão "bom não é bom o suficiente": o run-up do mês
    pré-earnings previu a direção da reação nos balanços passados deste
    ticker? E em qual estado (esticado/descontado/neutro) o papel está AGORA?

    Só descreve o histórico -- não prevê nada sozinho. Com ~8 eventos por
    ticker a amostra é pequena; por isso os CONTADORES por bucket (fáceis de
    auditar: "5 de 6 esticados caíram") acompanham a correlação de Pearson,
    que nesse tamanho de amostra é só um indício, nunca prova.
    """
    com_runup = df.dropna(subset=["runup_pct"]) if "runup_pct" in df.columns else pd.DataFrame()

    out: dict = {
        "runup_pregoes": RUNUP_PREGOES,
        "esticado_corte_pct": RUNUP_ESTICADO_PCT,
        "n_com_runup": int(len(com_runup)),
    }

    if len(com_runup) >= 3:
        esticados = com_runup[com_runup["runup_pct"] >= RUNUP_ESTICADO_PCT]
        descontados = com_runup[com_runup["runup_pct"] <= 0]
        out.update({
            "corr_runup_reacao": round(float(com_runup["runup_pct"].corr(com_runup["close_pct"])), 2)
            if len(com_runup) >= 4 else None,
            "esticado_n": int(len(esticados)),
            "esticado_caiu_n": int((esticados["close_pct"] < 0).sum()),
            "esticado_reacao_media": round(float(esticados["close_pct"].mean()), 2) if len(esticados) else None,
            "descontado_n": int(len(descontados)),
            "descontado_subiu_n": int((descontados["close_pct"] > 0).sum()),
            "descontado_reacao_media": round(float(descontados["close_pct"].mean()), 2) if len(descontados) else None,
        })

    # Estado ATUAL: mesmo run-up de RUNUP_PREGOES pregões, terminando no
    # último fechamento -- é o que permite dizer "o papel está chegando
    # esticado no próximo balanço".
    #
    # ARMADILHA (visto em produção, NBIS 17/08/2026): essa janela olha pra
    # trás a partir de HOJE, então logo depois de um balanço ela ENGOLE o
    # próprio pregão de reação. NBIS reportou em 12/08 e saltou +34,14%; DOIS
    # pregões depois o "run-up atual" saía +61,66% e o papel era classificado
    # "esticado" -- mas isso é a REAÇÃO já ocorrida, não a antecipação que o
    # indicador se propõe a medir (ex-evento o run-up era ~+20,5%). Qualquer
    # snapshot tirado logo após um earnings classificava o ticker como
    # esticado por construção, e a análise com IA lia isso como "o papel
    # chega esticado ao balanço" -- de um evento que já tinha acontecido.
    if len(hist) > RUNUP_PREGOES:
        ini = len(hist) - 1 - RUNUP_PREGOES
        base = float(hist["Close"].iloc[ini])
        atual = float(hist["Close"].iloc[-1])
        if base and not pd.isna(base):
            runup_atual = round((atual / base - 1) * 100, 2)
            out["runup_atual_pct"] = runup_atual

            # A janela cobre o pregão de reação do último balanço?
            contaminada = ultimo_earnings_pos is not None and ultimo_earnings_pos >= ini
            out["janela_contem_earnings"] = bool(contaminada)

            base_estado = runup_atual
            if contaminada:
                out["pregoes_desde_earnings"] = len(hist) - 1 - ultimo_earnings_pos
                # Run-up "limpo": remove SÓ o retorno do pregão de reação da
                # variação acumulada da janela (composição, não subtração).
                ret_evento = float(hist["Close"].iloc[ultimo_earnings_pos]) / float(
                    hist["Close"].iloc[ultimo_earnings_pos - 1]
                )
                if ret_evento:
                    ex_evento = round(((atual / base) / ret_evento - 1) * 100, 2)
                    out["runup_atual_ex_evento_pct"] = ex_evento
                    base_estado = ex_evento

            # estado_atual sempre sai do número LIMPO -- é ele que responde
            # "o papel está esticado?" de forma comparável com os eventos
            # históricos, que por construção medem só o pré-balanço.
            out["estado_atual"] = (
                "esticado" if base_estado >= RUNUP_ESTICADO_PCT
                else "descontado" if base_estado <= 0
                else "neutro"
            )

    return out


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
        ru = s.get("runup") or {}
        if ru.get("esticado_n") is not None:
            print(
                f"  Run-up pré-earnings ({ru['runup_pregoes']} pregões, corte esticado ≥{ru['esticado_corte_pct']:.0f}%):"
            )
            print(
                f"    esticados: {ru['esticado_caiu_n']}/{ru['esticado_n']} caíram na reação"
                + (f" (média {ru['esticado_reacao_media']:+.2f}%)" if ru.get("esticado_reacao_media") is not None else "")
            )
            print(
                f"    descontados: {ru['descontado_subiu_n']}/{ru['descontado_n']} subiram na reação"
                + (f" (média {ru['descontado_reacao_media']:+.2f}%)" if ru.get("descontado_reacao_media") is not None else "")
            )
            if ru.get("corr_runup_reacao") is not None:
                print(f"    correlação run-up × reação: {ru['corr_runup_reacao']:+.2f} (amostra pequena, indício)")
        if ru.get("runup_atual_pct") is not None:
            print(f"  Estado atual: {ru['estado_atual']} (run-up de {ru['runup_atual_pct']:+.2f}% no último mês)")
            if ru.get("janela_contem_earnings"):
                print(
                    f"    ⚠ a janela inclui o balanço de {ru['pregoes_desde_earnings']} pregão(ões) atrás"
                    + (
                        f" -- ex-evento: {ru['runup_atual_ex_evento_pct']:+.2f}% (é este que define o estado)"
                        if ru.get("runup_atual_ex_evento_pct") is not None else ""
                    )
                )
        print("  Últimos eventos (run-up prévio → dia do anúncio | dia seguinte):")
        for e in r["events"][:6]:
            a = e["announcement_day"]
            n = e["next_day"]
            a_txt = f"gap {a['gap_pct']:+.2f}% / fech {a['close_pct']:+.2f}%" if a else "sem pregão"
            n_txt = f"gap {n['gap_pct']:+.2f}% / fech {n['close_pct']:+.2f}%" if n else "sem pregão"
            ru_txt = f"{e['runup_pct']:+.2f}%" if e.get("runup_pct") is not None else "n/d"
            print(f"    {e['earnings_date']} (run-up {ru_txt}): [{a_txt}]  |  [{n_txt}]")


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
        print(json_seguro.dumps(_saida, ensure_ascii=False))
    else:
        main()
