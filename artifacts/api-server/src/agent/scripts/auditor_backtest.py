"""
Auditor independente do backtest -- o motor não pode ser o único juiz de si
mesmo (roteiro do motor de pesquisa auditável, Diário 20/08/2026, etapa 2).

O look-ahead removido no PR #362 viveu anos no motor sem nenhum teste piscar,
porque todos os testes conferiam o que o motor DIZIA fazer, usando o próprio
motor como referência. Este módulo quebra essa circularidade com três peças:

1. `simular_referencia` -- uma SEGUNDA implementação do contrato de execução
   (sinal de D executa no open de D+1; stop/target contra o pregão inteiro
   com gap saindo no open e ambos-no-mesmo-candle assumindo o stop; fricções
   de comissão/slippage; fechamento forçado no fim do período). Escrita
   burra de propósito: loop dia a dia, floats crus, sem pandas no miolo,
   nomes e estrutura diferentes -- para um erro de RACIOCÍNIO do motor não
   se repetir aqui por cópia. Se as duas implementações divergem, uma das
   duas está errada, e a discussão vira sobre o contrato, não sobre o código.

2. `comparar` -- diff linha a linha entre o resultado do motor e o da
   referência: cada trade (datas, preços, motivo, pnl), cada ponto da equity
   curve, e os agregados. Tolerância só para o arredondamento que o motor
   aplica (2 casas); qualquer coisa acima é divergência nomeada.

3. `verificar_coerencia_interna` -- rederiva as métricas do resultado a
   partir das PARTES dele mesmo (equity curve, trades): finalValue tem que
   ser o último ponto da equity, o drawdown tem que sair da equity publicada,
   winRate dos trades publicados. Pega a classe de bug em que a simulação
   está certa e a agregação mente.

ESCOPO: execução e métricas. Os INDICADORES (RSI, médias, score) não são
auditados aqui -- as cópias deles entre módulos já têm testes de sincronia
próprios (test_backtest_confluencia), e uma segunda implementação de
indicador viraria manutenção dupla sem pergunta nova.

A bateria sintética (`rodar_bateria`) roda SEM REDE e está pendurada no CI
via test_auditor_backtest.py: divergência entre motor e auditor quebra o
build. Modo real (na VPS, dentro do container):

    docker compose exec -T -w /app/artifacts/api-server/src/agent/scripts app \
      /app/.venv/bin/python3 auditor_backtest.py --ticker NVDA \
      --start 2024-08-01 --end 2026-08-01 --strategy rsi < /dev/null
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import backtest as motor  # noqa: E402


# ── 1. a implementação de referência ─────────────────────────────────────────

def simular_referencia(ohlc, buy_signal, sell_signal, position_fraction=1.0,
                       commission_pct=0.001, slippage_pct=0.0005,
                       stop_loss_pct=None, take_profit_pct=None,
                       capital_inicial=10_000.0):
    """O mesmo CONTRATO de _simulate, em prosa de estado explícito.

    Devolve floats CRUS (sem arredondar) -- o arredondamento é apresentação
    do motor, e a comparação aplica tolerância para ele."""
    caixa = capital_inicial
    acoes_em_maos = 0.0
    preco_pago = None
    dia_da_compra = None
    negocios = []
    curva = []  # [(data, patrimonio)]

    def _executa_venda(preco_bruto, dia, motivo):
        nonlocal caixa, acoes_em_maos, preco_pago, dia_da_compra
        preco_liquido = preco_bruto - preco_bruto * slippage_pct
        recebido = acoes_em_maos * preco_liquido
        recebido -= recebido * commission_pct
        negocios.append({
            "entryDate": dia_da_compra, "exitDate": dia,
            "entryPrice": preco_pago, "exitPrice": preco_liquido,
            "pnl": (preco_liquido - preco_pago) / preco_pago * 100,
            "exitReason": motivo, "closedOpen": motivo == "period_end",
        })
        caixa += recebido
        acoes_em_maos = 0.0
        preco_pago = None
        dia_da_compra = None

    ordem_para_amanha = None  # "comprar" | "vender"
    datas = [str(d)[:10] for d in ohlc.index]
    for i in range(len(ohlc)):
        abre = float(ohlc["open"].iloc[i])
        maxima = float(ohlc["high"].iloc[i])
        minima = float(ohlc["low"].iloc[i])
        fecha = float(ohlc["close"].iloc[i])

        # A ordem decidida ontem executa no primeiro preço de hoje.
        if ordem_para_amanha == "vender" and acoes_em_maos > 0:
            _executa_venda(abre, datas[i], "signal")
        elif ordem_para_amanha == "comprar" and acoes_em_maos == 0 and caixa > 0:
            preco_de_compra = abre + abre * slippage_pct
            aporte = caixa * position_fraction
            acoes_em_maos = (aporte - aporte * commission_pct) / preco_de_compra
            preco_pago = preco_de_compra
            dia_da_compra = datas[i]
            caixa -= aporte
        ordem_para_amanha = None

        # O pregão inteiro pode tirar a posição: gap de abertura sai no open
        # (não existe fill no nível); toque intradia sai no nível; os dois
        # níveis no mesmo candle assumem o stop.
        if acoes_em_maos > 0:
            piso = preco_pago * (1 - stop_loss_pct) if stop_loss_pct is not None else None
            teto = preco_pago * (1 + take_profit_pct) if take_profit_pct is not None else None
            if piso is not None and abre <= piso:
                _executa_venda(abre, datas[i], "stop_loss")
            elif teto is not None and abre >= teto:
                _executa_venda(abre, datas[i], "take_profit")
            elif piso is not None and minima <= piso:
                _executa_venda(piso, datas[i], "stop_loss")
            elif teto is not None and maxima >= teto:
                _executa_venda(teto, datas[i], "take_profit")

        # O sinal de hoje (conhecido só no fechamento) vira a ordem de amanhã.
        if bool(buy_signal.iloc[i]) and acoes_em_maos == 0:
            ordem_para_amanha = "comprar"
        elif bool(sell_signal.iloc[i]) and acoes_em_maos > 0:
            ordem_para_amanha = "vender"

        curva.append((datas[i], caixa + acoes_em_maos * fecha))

    if acoes_em_maos > 0:
        _executa_venda(float(ohlc["close"].iloc[-1]), datas[-1], "period_end")
        curva[-1] = (datas[-1], caixa)

    return {"trades": negocios, "curva": curva, "caixa_final": caixa}


# ── 2. o diff motor x referência ─────────────────────────────────────────────

# O motor arredonda preço/pnl/equity a 2 casas; a referência não arredonda.
_TOL_PRECO = 0.011
_TOL_EQUITY = 0.03


def _difere(a, b, tol):
    if a is None or b is None:
        return a is not b
    return abs(float(a) - float(b)) > tol


def comparar(resultado_motor: dict, referencia: dict) -> list:
    """Divergências nomeadas entre o resultado do motor e a referência.
    Lista vazia = os dois contam a mesma história, número a número."""
    problemas = []

    def _anota(onde, campo, valor_motor, valor_ref):
        problemas.append({"onde": onde, "campo": campo,
                          "motor": valor_motor, "auditor": valor_ref})

    ref_trades = referencia["trades"]
    if resultado_motor["totalTrades"] != len(ref_trades):
        _anota("agregado", "totalTrades", resultado_motor["totalTrades"], len(ref_trades))

    # O payload do motor guarda só os últimos 30 trades -- alinhar pela cauda.
    trades_motor = resultado_motor["trades"]
    cauda_ref = ref_trades[-len(trades_motor):] if trades_motor else []
    for k, (tm, tr) in enumerate(zip(trades_motor, cauda_ref)):
        rotulo = f"trade[{k}] ({tm.get('entryDate')})"
        for campo in ("entryDate", "exitDate", "exitReason"):
            if tm.get(campo) != tr.get(campo):
                _anota(rotulo, campo, tm.get(campo), tr.get(campo))
        for campo in ("entryPrice", "exitPrice", "pnl"):
            if _difere(tm.get(campo), tr.get(campo), _TOL_PRECO):
                _anota(rotulo, campo, tm.get(campo), tr.get(campo))

    curva_motor = resultado_motor["equityCurve"]
    curva_ref = referencia["curva"]
    if len(curva_motor) != len(curva_ref):
        _anota("equityCurve", "len", len(curva_motor), len(curva_ref))
    for k, (pm, (data_ref, eq_ref)) in enumerate(zip(curva_motor, curva_ref)):
        if pm["date"] != data_ref:
            _anota(f"equityCurve[{k}]", "date", pm["date"], data_ref)
        if _difere(pm["equity"], eq_ref, _TOL_EQUITY):
            _anota(f"equityCurve[{k}] ({data_ref})", "equity", pm["equity"], round(eq_ref, 2))

    if _difere(resultado_motor["finalValue"], referencia["caixa_final"], _TOL_EQUITY):
        _anota("agregado", "finalValue",
               resultado_motor["finalValue"], round(referencia["caixa_final"], 2))
    return problemas


# ── 3. coerência interna do próprio resultado ────────────────────────────────

def verificar_coerencia_interna(res: dict) -> list:
    """As métricas têm que sair das partes publicadas do MESMO resultado.
    Pega a classe de bug em que a simulação acerta e a agregação mente --
    invisível para o diff acima, porque a referência compararia só a
    simulação."""
    problemas = []

    def _anota(campo, publicado, rederivado):
        problemas.append({"onde": "coerencia", "campo": campo,
                          "motor": publicado, "auditor": rederivado})

    equity = [p["equity"] for p in res["equityCurve"]]
    if not equity:
        return [{"onde": "coerencia", "campo": "equityCurve", "motor": "vazia", "auditor": "-"}]

    if _difere(res["finalValue"], equity[-1], _TOL_EQUITY):
        _anota("finalValue vs equity final", res["finalValue"], equity[-1])

    retorno = (res["finalValue"] - res["initialCapital"]) / res["initialCapital"] * 100
    if _difere(res["totalReturn"], retorno, _TOL_PRECO):
        _anota("totalReturn", res["totalReturn"], round(retorno, 2))

    pico, pior = equity[0], 0.0
    for e in equity:
        pico = max(pico, e)
        pior = min(pior, (e - pico) / pico)
    if _difere(res["maxDrawdown"], pior * 100, _TOL_PRECO):
        _anota("maxDrawdown", res["maxDrawdown"], round(pior * 100, 2))

    # Sharpe/Sortino como o motor declara calcular: retornos diários da
    # equity, desvio amostral (ddof=1, o default do pandas), anualizado.
    retornos = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]

    def _desvio(valores):
        if len(valores) < 2:
            return 0.0
        media = sum(valores) / len(valores)
        return math.sqrt(sum((v - media) ** 2 for v in valores) / (len(valores) - 1))

    if retornos and _desvio(retornos) > 0:
        sharpe = (sum(retornos) / len(retornos)) / _desvio(retornos) * math.sqrt(252)
        if _difere(res["sharpe"], sharpe, _TOL_PRECO):
            _anota("sharpe", res["sharpe"], round(sharpe, 2))

    negativos = [r for r in retornos if r < 0]
    if res.get("sortino") is not None and negativos and _desvio(negativos) > 0:
        sortino = (sum(retornos) / len(retornos)) / _desvio(negativos) * math.sqrt(252)
        if _difere(res["sortino"], sortino, _TOL_PRECO):
            _anota("sortino", res["sortino"], round(sortino, 2))

    # Métricas de trade só quando o payload tem TODOS os trades (corte em 30).
    if res["totalTrades"] <= 30 and res["trades"]:
        pnls = [t["pnl"] for t in res["trades"]]
        vitorias = [p for p in pnls if p > 0]
        win_rate = len(vitorias) / len(pnls) * 100
        if _difere(res["winRate"], win_rate, 0.06):  # motor arredonda a 1 casa
            _anota("winRate", res["winRate"], round(win_rate, 1))
        if res.get("expectancy") is not None:
            if _difere(res["expectancy"], sum(pnls) / len(pnls), _TOL_PRECO):
                _anota("expectancy", res["expectancy"], round(sum(pnls) / len(pnls), 2))
        perdas = [p for p in pnls if p <= 0]
        if res.get("profitFactor") is not None and perdas and sum(perdas) != 0:
            pf = sum(vitorias) / abs(sum(perdas))
            if _difere(res["profitFactor"], pf, _TOL_PRECO):
                _anota("profitFactor", res["profitFactor"], round(pf, 2))

    return problemas


# ── bateria sintética (sem rede; pendurada no CI) ────────────────────────────

def _ohlc_de(linhas):
    """[(open, high, low, close), ...] -> DataFrame no contrato do motor."""
    idx = pd.bdate_range("2026-01-05", periods=len(linhas))
    return pd.DataFrame(
        {c: pd.Series([l[j] for l in linhas], index=idx, dtype=float)
         for j, c in enumerate(("open", "high", "low", "close"))})


def _passeio(n, semente, deriva=0.0004, vol=0.02):
    rng = np.random.default_rng(semente)
    fecha = pd.Series(100 * np.cumprod(1 + deriva + rng.normal(0, vol, n)),
                      index=pd.bdate_range("2024-01-02", periods=n))
    abre = fecha.shift(1).fillna(fecha.iloc[0]) * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({
        "open": abre,
        "high": pd.concat([abre, fecha], axis=1).max(axis=1) * (1 + np.abs(rng.normal(0, 0.004, n))),
        "low": pd.concat([abre, fecha], axis=1).min(axis=1) * (1 - np.abs(rng.normal(0, 0.004, n))),
        "close": fecha,
    })


def _sinais_fixos(ohlc, compras=(), vendas=()):
    buy = pd.Series(False, index=ohlc.index)
    sell = pd.Series(False, index=ohlc.index)
    for i in compras:
        buy.iloc[i] = True
    for i in vendas:
        sell.iloc[i] = True
    return buy, sell


def _cenarios():
    """(nome, ohlc, buy, sell, params). Cobre cada regra de execução que já
    escondeu um viés, mais passeios aleatórios com o gerador de sinais REAL
    do motor -- os casos desenhados provam as bordas; os passeios provam que
    nada diverge no uso comum."""
    lisos = [(100.0, 100.0, 100.0, 100.0)] * 24
    todos = []

    df = _ohlc_de([(100, 101, 99, 100), (100, 101, 99, 100), (90, 92, 89, 91)] + lisos)
    todos.append(("gap_pelo_stop", df, *_sinais_fixos(df, compras=[0]),
                  dict(stop_loss_pct=0.05)))

    df = _ohlc_de([(100, 101, 99, 100), (100, 101, 99, 100), (100, 115, 92, 105)] + lisos)
    todos.append(("stop_e_target_no_mesmo_candle", df, *_sinais_fixos(df, compras=[0]),
                  dict(stop_loss_pct=0.05, take_profit_pct=0.10)))

    df = _ohlc_de([(100, 101, 99, 100), (100, 101, 90, 92)] + lisos)
    todos.append(("stop_no_dia_da_entrada", df, *_sinais_fixos(df, compras=[0]),
                  dict(stop_loss_pct=0.05)))

    df = _ohlc_de([(100, 101, 99, 100), (100, 101, 99, 100),
                   (100, 101, 99, 100), (98, 99, 90, 91)] + lisos)
    todos.append(("saida_por_sinal_antes_do_intradia", df,
                  *_sinais_fixos(df, compras=[0], vendas=[2]), dict(stop_loss_pct=0.05)))

    df = _ohlc_de([(100, 101, 99, 100), (104, 106, 103, 105)] + lisos)
    todos.append(("trade_aberto_no_fim", df, *_sinais_fixos(df, compras=[0]), {}))

    df = _ohlc_de([(100, 101, 99, 100), (104, 106, 103, 105), (107, 112, 106, 111),
                   (111, 113, 108, 109)] + lisos)
    todos.append(("com_friccoes", df, *_sinais_fixos(df, compras=[0], vendas=[2]),
                  dict(position_fraction=0.5, commission_pct=0.002, slippage_pct=0.001)))

    df = _ohlc_de(lisos)
    todos.append(("sem_nenhum_trade", df, *_sinais_fixos(df), {}))

    # > 30 trades: exercita o corte trades[-30:] do payload e o alinhamento
    # pela cauda no comparador.
    df = _passeio(160, semente=3)
    buy = pd.Series([i % 4 == 0 for i in range(len(df))], index=df.index)
    sell = pd.Series([i % 4 == 2 for i in range(len(df))], index=df.index)
    todos.append(("muitos_trades", df, buy, sell, {}))

    for semente, estrategia in ((7, "rsi"), (11, "ma_cross")):
        df = _passeio(420, semente=semente)
        buy, sell = motor._build_signals(df["close"], estrategia)
        todos.append((f"passeio_{estrategia}_semente{semente}", df, buy, sell,
                      dict(stop_loss_pct=0.08, take_profit_pct=0.15)))

    return todos


def rodar_bateria() -> list:
    """[{cenario, divergencias, incoerencias, trades}] por cenário."""
    saida = []
    for nome, ohlc, buy, sell, extras in _cenarios():
        params = dict(position_fraction=1.0, commission_pct=0.0, slippage_pct=0.0,
                      stop_loss_pct=None, take_profit_pct=None)
        params.update(extras)
        res = motor._simulate("AUD", "auditoria", str(ohlc.index[0])[:10],
                              str(ohlc.index[-1])[:10], ohlc, buy, sell, **params)
        ref = simular_referencia(ohlc, buy, sell, **params)
        saida.append({"cenario": nome,
                      "divergencias": comparar(res, ref),
                      "incoerencias": verificar_coerencia_interna(res),
                      "trades": res["totalTrades"]})
    return saida


# ── modo real (VPS) ──────────────────────────────────────────────────────────

def _auditar_ticker(ticker, start, end, strategy, stop, target):
    ohlc_full, erro = motor._fetch_warmed_ohlc(ticker, start, end)
    if erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2
    buy_full, sell_full = motor._build_signals(ohlc_full["close"], strategy)
    ohlc, buy, sell = motor._trim_to_window(ohlc_full, buy_full, sell_full, start)
    params = dict(position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                  stop_loss_pct=stop, take_profit_pct=target)
    res = motor._simulate(ticker, strategy, start, end, ohlc, buy, sell, **params)
    if "error" in res:
        print(f"ERRO do motor: {res['error']}", file=sys.stderr)
        return 2
    ref = simular_referencia(ohlc, buy, sell, **params)
    divergencias = comparar(res, ref) + verificar_coerencia_interna(res)
    print(f"{ticker} {strategy} {start}..{end}: {res['totalTrades']} trades, "
          f"retorno {res['totalReturn']}%, {len(divergencias)} divergência(s)")
    for d in divergencias:
        print(f"  DIVERGE {d['onde']}.{d['campo']}: motor={d['motor']} auditor={d['auditor']}")
    return 1 if divergencias else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Auditor independente do backtest")
    ap.add_argument("--ticker")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--strategy", default="rsi")
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--target", type=float, default=None)
    args = ap.parse_args()

    if args.ticker:
        if not (args.start and args.end):
            ap.error("--ticker exige --start e --end")
        return _auditar_ticker(args.ticker, args.start, args.end,
                               args.strategy, args.stop, args.target)

    # Sem ticker: a bateria sintética, a mesma do CI.
    falhas = 0
    for r in rodar_bateria():
        problemas = r["divergencias"] + r["incoerencias"]
        status = "ok" if not problemas else f"{len(problemas)} PROBLEMA(S)"
        print(f"{r['cenario']:38s} {r['trades']:3d} trades  {status}")
        for d in problemas:
            print(f"  DIVERGE {d['onde']}.{d['campo']}: motor={d['motor']} auditor={d['auditor']}")
        falhas += len(problemas)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
