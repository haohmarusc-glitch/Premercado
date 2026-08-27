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
# Pregões acompanhados DEPOIS do balanço: um mês de mercado. Começou em 10
# (duas semanas) e foi estendido quando os dados mostraram casos que ainda
# não tinham se resolvido lá: a AOSL seguia -7,6% no D+10 sem sinal de
# recuperação, e a STX ainda subia.
#
# Esticar só faz sentido junto do EXCESSO sobre o benchmark: quanto mais
# longe do evento, mais o retorno cru mede a tendência do papel e menos a
# reação ao resultado. Sem o ajuste, D+21 seria quase só maré.
DIAS_TRAJETORIA = 21
# Referência quando o chamador não manda uma. SPY (mercado amplo) em vez de
# um ETF setorial porque este script atende ticker qualquer — o mapa por
# setor vive na tela (lib/benchmark-setor.ts) e chega por parâmetro.
BENCHMARK_PADRAO = "SPY"


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


def _janela_da_reacao(earnings_ts) -> tuple:
    """(janela, inferido) -- em qual sessão a notícia é precificada.

    "anuncio" para quem divulga ANTES da abertura (BMO): o próprio pregão do
    dia já reage. "seguinte" para quem divulga DEPOIS do fechamento (AMC): a
    reação é o pregão seguinte, e o fechamento do dia do anúncio é ANTERIOR à
    notícia.

    Por que isto existe: a versão anterior escolhia, entre as duas sessões, a
    que se moveu MAIS em módulo. Isso é seleção pelo resultado -- no momento em
    que o número seria útil (antes do balanço) ninguém sabe qual sessão vai
    andar mais, e a estatística passa a descrever algo que não se podia usar.
    Pior: em 25/08/2026 isso pegou, no NVDA de 2024-11-20, o dia do ANÚNCIO de
    uma empresa que reporta after-close -- ou seja, mediu como "reação" a
    sessão anterior à notícia.

    O horário de divulgação é fato publicado e vem no índice da fonte. Quando
    ele não vem (timestamp à meia-noite, ou horário no meio do pregão), o
    padrão declarado é AMC -- convenção dominante entre as empresas que
    acompanhamos -- e o evento é CONTADO como inferido, para o relatório poder
    dizer quantos dependeram da suposição."""
    hora = getattr(earnings_ts, "hour", None)
    minuto = getattr(earnings_ts, "minute", 0) or 0
    if hora is None:
        return "seguinte", True
    # Meia-noite em ponto é AUSÊNCIA de horário, não divulgação de madrugada.
    # Sem esta linha, `hora < 9` transformava "não sei" em "reporta antes da
    # abertura" -- afirmação confiante sobre um dado que não existe, e com o
    # agravante de trocar a sessão medida. Pego pela própria suíte.
    if hora == 0 and minuto == 0:
        return "seguinte", True
    if hora >= 16:                               # depois do fechamento
        return "seguinte", False
    if hora < 9 or (hora == 9 and minuto < 30):  # antes da abertura
        return "anuncio", False
    return "seguinte", True                      # meio do pregão: não dá pra afirmar


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


def _serie_benchmark(simbolo: str, start: str) -> pd.Series | None:
    """Fechamentos do benchmark, indexados por data (sem fuso).

    Falha vira None e a trajetória sai sem excesso — o retorno cru já é
    útil, e derrubar a análise inteira porque o ETF de referência não veio
    seria trocar o principal pelo acessório.
    """
    try:
        b = yf.Ticker(simbolo).history(start=start, auto_adjust=False)
        b = market_data_provider.sem_barra_incompleta(b)
        if b.empty:
            return None
        if b.index.tz is not None:
            b.index = b.index.tz_localize(None)
        return b["Close"]
    except Exception as e:  # noqa: BLE001
        print(f"[earnings_reaction] benchmark {simbolo} indisponível: {e}", file=sys.stderr)
        return None


def _acum_benchmark(bench: pd.Series | None, data_base, data: pd.Timestamp) -> float | None:
    """Variação % do benchmark entre a véspera do balanço e `data`.

    Usa asof (último pregão <= data) porque o calendário do papel e o do ETF
    podem divergir — halt no papel, feriado parcial. Sem asof, um dia
    faltante viraria KeyError e mataria a trajetória do evento inteiro.
    """
    if bench is None or bench.empty:
        return None
    try:
        base = bench.asof(data_base)
        agora = bench.asof(data)
    except Exception:  # noqa: BLE001
        return None
    if pd.isna(base) or pd.isna(agora) or not base:
        return None
    return (float(agora) / float(base) - 1) * 100


def _trajetoria(hist: pd.DataFrame, pos: int, prev_close: float,
                dias: int = DIAS_TRAJETORIA,
                bench: pd.Series | None = None) -> list[dict]:
    """Os `dias` pregões seguintes ao balanço, cada um com dois números.

    `acum_pct` é sempre contra o fechamento da VÉSPERA do balanço — é o que
    responde "onde o papel está agora em relação a antes do resultado", que
    é a pergunta de quem segurou a posição. `dia_pct` é a variação do próprio
    pregão, que mostra se o movimento continuou ou parou.

    Sem os dois, um acumulado de +2% no D+5 esconde se veio de subida
    contínua ou de tombo seguido de recuperação — histórias opostas para
    quem opera.
    """
    saida: list[dict] = []
    for i in range(1, dias + 1):
        p = pos + i
        if p >= len(hist.index):
            break  # earnings recente: a trajetória ainda não completou
        close = float(hist["Close"].iloc[p])
        close_ant = float(hist["Close"].iloc[p - 1])
        if pd.isna(close) or pd.isna(close_ant) or not close_ant:
            break
        acum = (close / prev_close - 1) * 100
        ponto = {
            "dia": i,
            "date": str(hist.index[p].date()),
            "acum_pct": round(acum, 2),
            "dia_pct": round((close / close_ant - 1) * 100, 2),
        }
        # Excesso sobre o benchmark: separa "subiu porque o resultado foi bom"
        # de "subiu porque tudo subiu". Sem ele, um papel em ciclo de alta
        # mostra deriva pós-earnings positiva mesmo quando o balanço foi
        # irrelevante — e um papel em queda estrutural parece punido pelo
        # resultado quando só está acompanhando o próprio tombo.
        b = _acum_benchmark(bench, hist.index[pos - 1], hist.index[p])
        if b is not None:
            ponto["bench_pct"] = round(b, 2)
            ponto["excesso_pct"] = round(acum - b, 2)
        saida.append(ponto)
    return saida


def _trajetoria_resumo(events: list[dict]) -> dict | None:
    """Média do acumulado em cada horizonte, sobre os eventos que têm o dia.

    É o que separa "reação que gruda" de "reação que reverte": se a média do
    D+1 é -8% e a do D+10 é -1%, o mercado devolveu o tombo; se o D+10 é
    -12%, a reação foi só o começo. `n` por horizonte é obrigatório — os
    eventos mais recentes ainda não têm 10 pregões, e uma média de 1 evento
    não pode parecer igual a uma de 8.
    """
    por_dia: dict[int, list[float]] = {}
    excesso_por_dia: dict[int, list[float]] = {}
    for e in events:
        for ponto in e.get("trajetoria") or []:
            por_dia.setdefault(ponto["dia"], []).append(ponto["acum_pct"])
            if ponto.get("excesso_pct") is not None:
                excesso_por_dia.setdefault(ponto["dia"], []).append(ponto["excesso_pct"])
    if not por_dia:
        return None

    dias = []
    for dia, valores in sorted(por_dia.items()):
        linha = {
            "dia": dia,
            "n": len(valores),
            "acum_medio_pct": round(sum(valores) / len(valores), 2),
            "positivos": sum(1 for v in valores if v > 0),
        }
        exc = excesso_por_dia.get(dia)
        if exc:
            linha["excesso_medio_pct"] = round(sum(exc) / len(exc), 2)
            # Quantas vezes o papel BATEU o setor — mais informativo que
            # "quantas vezes subiu" num mercado que subiu junto.
            linha["bateu_bench"] = sum(1 for v in exc if v > 0)
        dias.append(linha)
    return {"dias": dias}


def analyze_ticker(ticker: str, lookback_events: int = 8,
                   benchmark: str | None = None) -> dict:
    t = yf.Ticker(ticker)
    benchmark = (benchmark or BENCHMARK_PADRAO).strip().upper() or BENCHMARK_PADRAO

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
    passados = earnings[earnings.index < now]
    # Deduplicar por DATA DE CALENDÁRIO antes de cortar o lookback. A fonte
    # devolve o mesmo balanço duas vezes de vez em quando (visto em 25/08/2026:
    # SMCI com 2025-02-25 repetido), e sem isto o evento entra DUAS VEZES em
    # todas as estatísticas -- média, desvio, threshold e as bandas. No caso
    # real a duplicata era uma reação de +12,23% e puxava a média de +6,05%
    # para +6,82%, sustentando a frase "SMCI tem viés positivo".
    #
    # `keep="first"`: a primeira ocorrência é a que carrega o horário de
    # divulgação usado por `_janela_da_reacao`.
    dups = int(passados.index.normalize().duplicated().sum())
    passados = passados[~passados.index.normalize().duplicated(keep="first")]
    past_earnings = passados.head(lookback_events)
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
    # Uma chamada só para todos os eventos deste ticker — a janela do
    # benchmark é a mesma do histórico já buscado.
    bench = None if benchmark == ticker.strip().upper() else _serie_benchmark(benchmark, start)

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
        janela, janela_inferida = _janela_da_reacao(earnings_ts)

        events.append({
            "earnings_date": str(earnings_date.date()),
            # Qual sessão precifica a notícia -- decidido pelo HORÁRIO de
            # divulgação, nunca pela magnitude do movimento (ver
            # `_janela_da_reacao`).
            "janela_reacao": janela,
            "janela_inferida": janela_inferida,
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
            # Os 10 pregões seguintes: responde se a reação do dia grudou ou
            # foi devolvida. Vazio nos earnings recentes demais (o mercado
            # ainda não teve os pregões), e o resumo conta o `n` por dia.
            "trajetoria": _trajetoria(hist, pos, prev_close, bench=bench),
        })

    if not events:
        return {"ticker": ticker, "error": "não foi possível casar earnings dates com pregões do histórico"}

    # A sessão da reação vem do HORÁRIO de divulgação, não do tamanho do
    # movimento -- ver `_janela_da_reacao` para o vício que isto corrige.
    reaction_moves = []
    inferidos = 0
    for e in events:
        pedida = e["announcement_day"] if e["janela_reacao"] == "anuncio" else e["next_day"]
        # Sem a sessão pedida (earnings recente demais, sem pregão seguinte
        # ainda), o evento fica FORA da estatística. Cair na outra sessão
        # seria trocar o dia em silêncio, que é o defeito de origem.
        if pedida is None:
            continue
        escolhido = dict(pedida)
        escolhido["runup_pct"] = e["runup_pct"]
        reaction_moves.append(escolhido)
        inferidos += 1 if e["janela_inferida"] else 0

    if not reaction_moves:
        return {"ticker": ticker, "error": "sem janelas válidas de reação nos eventos encontrados"}

    df = pd.DataFrame(reaction_moves)
    volume_ratio = df["volume"] / avg_volume if avg_volume else None
    summary = {
        "n_events": len(df),
        # Quantos eventos a fonte devolveu repetidos (e foram descartados) e
        # quantos dependeram da suposição AMC por não trazerem horário: os dois
        # números existem para o leitor saber onde a série é mais frouxa.
        "eventos_duplicados_descartados": dups,
        "eventos_com_janela_inferida": inferidos,
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
    trajetoria = _trajetoria_resumo(events)
    if trajetoria:
        summary["trajetoria"] = trajetoria

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
    None quando nenhum earnings passado casa com um pregão do histórico.

    ARMADILHA (visto em produção, auditoria de 27/08/2026 -- NVDA, SMCI, ARM
    simultaneamente): esta função só fazia `searchsorted` na DATA do anúncio,
    sem olhar `_janela_da_reacao` -- ou seja, devolvia a posição do pregão do
    ANÚNCIO mesmo para reportadores AMC, cuja reação de verdade é o pregão
    SEGUINTE (mesmo bug de seleção de sessão que motivou `_janela_da_reacao`
    em primeiro lugar, só que reintroduzido aqui, num segundo lugar que
    também precisa da resposta). O docstring já prometia "pregão de reação"
    -- a implementação nunca cumpriu.

    Efeito em cascata: `_runup_summary` usa esta posição pra compor o
    "run-up ex-evento" removendo APENAS o retorno desse pregão da janela de
    21 pregões. Com a posição errada, ela removia o pregão do ANÚNCIO (a
    sessão ANTERIOR à notícia, que pode ter ido em qualquer direção) e
    deixava a reação de verdade -- tipicamente o movimento maior -- inteira
    dentro do run-up "limpo". NVDA: run-up bruto +19,72%, "ex-evento"
    +21,66% (removeu o -1,59% do dia do anúncio; a reação real do dia
    seguinte foi +8,50%). O sinal era visível a olho: remover UM dia
    positivo (a reação) deveria DERRUBAR o run-up ex-evento, não elevá-lo.
    """
    posicoes = []
    for ts in earnings_index:
        pos = hist.index.searchsorted(ts.tz_localize(None).normalize())
        if not (0 < pos < len(hist.index)):
            continue
        janela, _inferido = _janela_da_reacao(ts)
        if janela == "seguinte":
            pos += 1
            if pos >= len(hist.index):
                continue  # reação AMC ainda não aconteceu -- sem pregão pra apontar
        posicoes.append(int(pos))
    return max(posicoes) if posicoes else None


# Amostra mínima para a correlação valer alguma coisa. Com 4 pontos, r salta
# de -0,9 a +0,9 trocando um evento -- publicar isso como número convida a
# leitura que ele não sustenta.
CORR_MIN_EVENTOS = 5
CORR_BOOTSTRAP_AMOSTRAS = 2000
CORR_SEMENTE = 20260825


def _correlacao_com_incerteza(x, y) -> dict:
    """r de Pearson com IC 95% por bootstrap e p-valor por PERMUTAÇÃO.

    Por que os três juntos: em 25/08/2026 o relatório publicava só o r, e a
    leitura com IA promoveu o AVGO (r=-0,60, n=7) a "padrão estatisticamente
    relevante" e o transformou na recomendação principal -- quando p=0,15, ou
    seja, nem sem correção de múltiplos ele passa. O r sozinho não distingue
    padrão de ruído; o par (IC, p) distingue.

    Permutação em vez de teste-t pelo mesmo motivo de padroes_estatisticos.py:
    n de um dígito e cauda gorda quebram a aproximação normal justamente nos
    casos extremos, que são os que chamam atenção."""
    import numpy as np
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = len(x)
    if n < CORR_MIN_EVENTOS or x.std() == 0 or y.std() == 0:
        return {"corr_runup_reacao": None, "corr_n": int(n),
                "corr_ic95": None, "corr_p_valor": None,
                "corr_nota": f"amostra de {n} evento(s) -- mínimo {CORR_MIN_EVENTOS}"}

    r = float(np.corrcoef(x, y)[0, 1])
    rng = np.random.default_rng(CORR_SEMENTE)

    # IC por bootstrap sobre os PARES (reamostra eventos inteiros, preservando
    # o casamento run-up/reação).
    idx = rng.integers(0, n, size=(CORR_BOOTSTRAP_AMOSTRAS, n))
    rs = []
    for linha in idx:
        xb, yb = x[linha], y[linha]
        if xb.std() == 0 or yb.std() == 0:
            continue
        rs.append(np.corrcoef(xb, yb)[0, 1])
    ic = ([round(float(v), 2) for v in np.percentile(rs, [2.5, 97.5])]
          if len(rs) >= CORR_BOOTSTRAP_AMOSTRAS // 2 else None)

    # p por permutação: embaralha y contra x e conta quantas vezes o acaso
    # produz |r| igual ou maior. O +1 no numerador e no denominador impede
    # p=0, que seria afirmar impossibilidade a partir de 2000 sorteios.
    extremos = 0
    for _ in range(CORR_BOOTSTRAP_AMOSTRAS):
        yp = rng.permutation(y)
        if abs(np.corrcoef(x, yp)[0, 1]) >= abs(r):
            extremos += 1
    p = (extremos + 1) / (CORR_BOOTSTRAP_AMOSTRAS + 1)

    return {"corr_runup_reacao": round(r, 2), "corr_n": int(n),
            "corr_ic95": ic, "corr_p_valor": round(float(p), 4),
            "corr_nota": None}


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
        # Corte SIMÉTRICO. Era `<= 0`, o que rotulava de "descontado" qualquer
        # papel que não tivesse subido -- inclusive um que andou -0,7%, que é
        # de fato plano. Isso não é rótulo mal escolhido, é o que sustentava a
        # conclusão: em 25/08/2026 o AVGO aparecia "descontado em -6,91%" e com
        # "2 de 2 subiram" no histórico; pela regra simétrica ele está NEUTRO
        # hoje, e o histórico de descontados cai para um único evento.
        esticados = com_runup[com_runup["runup_pct"] >= RUNUP_ESTICADO_PCT]
        descontados = com_runup[com_runup["runup_pct"] <= -RUNUP_ESTICADO_PCT]
        out.update({
            # O piso de amostra mora dentro do helper (CORR_MIN_EVENTOS), que
            # devolve r=None com a nota do porquê -- em vez do `>= 4` solto de
            # antes, que sumia com o campo e não dizia nada ao leitor.
            **_correlacao_com_incerteza(com_runup["runup_pct"].to_numpy(float),
                                        com_runup["close_pct"].to_numpy(float)),
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
                else "descontado" if base_estado <= -RUNUP_ESTICADO_PCT
                else "neutro"
            )

    return out


def aplicar_holm(results: list[dict], alfa: float = 0.05) -> list[dict]:
    """Marca `corr_sobrevive` em cada ticker pela correção de Holm-Bonferroni.

    A correção só existe ENTRE tickers: uma cesta de cinco papéis são cinco
    testes da mesma hipótese ("run-up prevê reação"), e a leitura sempre
    destaca o mais extremo. Sem corrigir, varrer cinco a 5% produz um "achado"
    por acaso a cada quatro rodadas -- e o achado vem com história pronta.

    Foi exatamente o que aconteceu em 25/08/2026: entre cinco correlações, a
    leitura elegeu o AVGO (r=-0,60) como "padrão estatisticamente relevante" e
    o virou recomendação principal. Sozinho ele já não passava (p=0,15); com
    Holm fica em 0,62. Mesma mecânica de padroes_estatisticos.holm, reescrita
    aqui para não arrastar aquele módulo (e o numpy dele) para este script."""
    testados = [r for r in results
                if (r.get("summary", {}).get("runup") or {}).get("corr_p_valor") is not None]
    for r in results:
        ru = (r.get("summary", {}) or {}).get("runup")
        if ru is not None:
            ru["corr_sobrevive"] = False
            ru["corr_p_corrigido"] = None
    m = len(testados)
    if m == 0:
        return results
    ordenados = sorted(testados, key=lambda r: r["summary"]["runup"]["corr_p_valor"])
    maior_ate_agora = 0.0
    for i, r in enumerate(ordenados):
        ru = r["summary"]["runup"]
        # Holm: o i-ésimo menor p é comparado com alfa/(m-i). O p corrigido
        # equivalente é p*(m-i), e o acumulado monotônico impede o absurdo de
        # um p corrigido menor que o do teste mais forte que ele.
        corrigido = min(1.0, max(maior_ate_agora, ru["corr_p_valor"] * (m - i)))
        maior_ate_agora = corrigido
        ru["corr_p_corrigido"] = round(corrigido, 4)
        ru["corr_sobrevive"] = corrigido <= alfa
    return results


def _print_report(results: list[dict]) -> None:
    aplicar_holm(results)
    for r in results:
        print(f"\n=== {r['ticker']} ===")
        if "error" in r:
            print(f"  ⚠ {r['error']}")
            continue
        s = r["summary"]
        std = f"{s['close_pct_std']:.2f}" if s["close_pct_std"] is not None else "N/A"
        vol = f"{s['volume_ratio_mean']:.2f}x" if s["volume_ratio_mean"] is not None else "N/A"
        print(f"  Eventos analisados: {s['n_events']}")
        if s.get("eventos_duplicados_descartados"):
            print(f"    ({s['eventos_duplicados_descartados']} evento(s) repetido(s) na fonte, descartado(s))")
        if s.get("eventos_com_janela_inferida"):
            print(f"    ⚠ {s['eventos_com_janela_inferida']} evento(s) sem horário de divulgação — "
                  f"assumido after-close")
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
                ic = ru.get("corr_ic95")
                faixa = f"IC95 [{ic[0]:+.2f}, {ic[1]:+.2f}]" if ic else "IC95 indisponível"
                pc = ru.get("corr_p_corrigido")
                veredito = ("SOBREVIVE a Holm" if ru.get("corr_sobrevive")
                            else "NÃO sobrevive a Holm")
                print(f"    correlação run-up × reação: {ru['corr_runup_reacao']:+.2f} "
                      f"(n={ru.get('corr_n')}, {faixa})")
                print(f"      p={ru['corr_p_valor']:.3f}"
                      + (f" · corrigido p/ múltiplos tickers={pc:.3f}" if pc is not None else "")
                      + f" — {veredito}")
                # O IC que cruza zero é a leitura mais importante da linha: ele
                # diz que os dados são compatíveis com correlação NENHUMA.
                ic_exclui_zero = bool(ic) and not (ic[0] < 0 < ic[1])
                if ic and not ic_exclui_zero:
                    print("      ⚠ o IC cruza zero — compatível com ausência de relação")
                # IC e p discordando é o aviso mais útil da linha, e o mais
                # fácil de abusar: quem quer a conclusão cita o que a sustenta
                # e ignora o outro. Com amostra de um dígito os dois métodos
                # divergem com frequência -- o bootstrap de correlação fica
                # otimista demais, e a permutação é a mais confiável dos dois.
                # Nos dados reais de 25/08 os DOIS tickers com r alto caíram
                # aqui, em direções opostas.
                if ic is not None and ic_exclui_zero != (ru["corr_p_valor"] <= 0.05):
                    print("      ⚠ IC e p-valor DISCORDAM — com amostra deste tamanho "
                          "isso é comum; nenhum dos dois decide sozinho")
            elif ru.get("corr_nota"):
                print(f"    correlação run-up × reação: não publicada ({ru['corr_nota']})")
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
    parser.add_argument("--benchmark", default=BENCHMARK_PADRAO,
                        help="Referência do excesso na trajetória (SMH, KWEB, ITB...)")
    args = parser.parse_args()

    tickers = [tk.strip().upper() for tk in args.tickers.split(",") if tk.strip()]
    results = [analyze_ticker(tk, args.lookback, args.benchmark) for tk in tickers]

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
        # Um benchmark para o lote inteiro: a tela manda o do setor do papel
        # investigado (lib/benchmark-setor.ts). Ausente = SPY.
        _benchmark = str(_payload.get("benchmark") or "").strip().upper() or None
        _saida = []
        for tk in _tickers:
            # Resultado parcial vale mais que timeout: sem isto o laço roda até
            # o Node matar o processo e tudo que já foi analisado se perde.
            if deadline_exceeded():
                _saida.append({"ticker": tk, "error": "orçamento de tempo esgotado"})
                continue
            _saida.append(analyze_ticker(tk, _lookback, _benchmark))
        print(json_seguro.dumps(_saida, ensure_ascii=False))
    else:
        main()
