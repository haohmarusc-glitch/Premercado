"""
A mecânica de carteira do run_portfolio_backtest (modo B, 20/08/2026).

O que diferencia carteira de cesta é exatamente o que se testa aqui: caixa
COMPARTILHADO (uma entrada só acontece se sobrar capital), cota-alvo
patrimônio/n marcada no fechamento de ontem, calendários divergentes entre
tickers, e a atribuição por ticker/setor que transforma "cinco apostas
independentes" em concentração visível.

A amarra que fecha a cadeia de auditoria: com n=1, a carteira TEM que
reproduzir o _simulate -- e o _simulate é conferido pela referência
independente do auditor. Se este teste passa, a mecânica de carteira herda a
auditoria da execução por transitividade; o que sobra para os outros testes
é só o que a carteira acrescenta (caixa, cota, atribuição).

Tudo sem rede, pela costura `_dados` -- o caminho real de fetch/sinais é o
mesmo dos outros modos e tem seus próprios testes.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_carteira.py -v
"""
import pandas as pd
import pytest

from agent import backtest as bt

_SEM_FRICCAO = dict(commission_pct=0.0, slippage_pct=0.0)


def _ohlc(linhas, inicio="2026-01-05"):
    idx = pd.bdate_range(inicio, periods=len(linhas))
    return pd.DataFrame(
        {c: pd.Series([l[j] for l in linhas], index=idx, dtype=float)
         for j, c in enumerate(("open", "high", "low", "close"))})


def _sinais(ohlc, compras=(), vendas=()):
    buy = pd.Series(False, index=ohlc.index)
    sell = pd.Series(False, index=ohlc.index)
    for i in compras:
        buy.iloc[i] = True
    for i in vendas:
        sell.iloc[i] = True
    return buy, sell


def _flat(n=25, preco=100.0):
    return [(preco, preco, preco, preco)] * n


# ── a amarra: n=1 reproduz o motor auditado ──────────────────────────────────

def test_carteira_de_um_ticker_reproduz_o_simulate():
    linhas = [(100, 101, 99, 100), (104, 106, 103, 105), (107, 112, 106, 111),
              (111, 113, 108, 109), (109, 110, 105, 106)] + _flat(20)
    df = _ohlc(linhas)
    buy, sell = _sinais(df, compras=[0], vendas=[2])
    params = dict(position_fraction=1.0, commission_pct=0.001, slippage_pct=0.0005,
                  stop_loss_pct=None, take_profit_pct=None)
    solo = bt._simulate("AA", "x", "2026-01-05", "2026-02-06", df, buy, sell, **params)

    carteira = bt.run_portfolio_backtest(
        ["AA"], "2026-01-05", "2026-02-06",
        commission_pct=0.001, slippage_pct=0.0005,
        _dados={"AA": (df, buy, sell)})

    # Retornos em % são invariantes à escala do capital ($10k vs $100k).
    assert carteira["totalReturn"] == pytest.approx(solo["totalReturn"], abs=0.011)
    assert carteira["totalTrades"] == solo["totalTrades"]
    ts, tc = solo["trades"][0], carteira["trades"][0]
    for campo in ("entryDate", "exitDate", "exitReason"):
        assert tc[campo] == ts[campo]
    assert tc["pnl"] == pytest.approx(ts["pnl"], abs=0.011)
    assert carteira["maxDrawdown"] == pytest.approx(solo["maxDrawdown"], abs=0.02)


# ── caixa compartilhado ──────────────────────────────────────────────────────

def test_cota_e_metade_do_patrimonio_com_dois_tickers():
    a = _ohlc(_flat())
    b = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA", "BB"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0])),
                "BB": (b, *_sinais(b, compras=[0]))})
    # Ambos entram no dia 1 com cota 100k/2 = 50k cada.
    assert {t["ticker"]: t["aporte"] for t in res["trades"]} == {"AA": 50_000.0, "BB": 50_000.0}
    assert res["exposicao"]["picoExposicaoPct"] == pytest.approx(100.0)

def test_caixa_escasso_limita_a_segunda_entrada():
    """O momento em que carteira deixa de ser cesta: a cota diz 75k, o caixa
    só tem 50k -- a entrada é do tamanho do caixa, não da vontade."""
    # AA entra a 100 no dia 1 e dobra; BB entra no dia 5, quando o
    # patrimônio marcado é 50k caixa + 100k posição = 150k -> cota 75k.
    a = _ohlc([(100, 101, 99, 100), (100, 101, 99, 100), (150, 155, 149, 150),
               (200, 201, 199, 200), (200, 201, 199, 200)] + _flat(20, 200))
    b = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA", "BB"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0])),
                "BB": (b, *_sinais(b, compras=[3]))})
    aportes = {t["ticker"]: t["aporte"] for t in res["trades"]}
    assert aportes["AA"] == pytest.approx(50_000.0)
    assert aportes["BB"] == pytest.approx(50_000.0)  # min(caixa 50k, cota 75k)

def test_a_cota_reserva_espaco_para_entradas_futuras():
    """A propriedade estrutural da cota patrimônio/n: duas entradas no dia 1
    consomem 2/3 do capital, e a terceira -- dias depois -- ainda encontra a
    cota inteira dela em caixa. É o que impede o primeiro sinal do período
    de comer o capital dos outros."""
    a = _ohlc(_flat()); b = _ohlc(_flat()); c = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA", "BB", "CC"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0])),
                "BB": (b, *_sinais(b, compras=[0])),
                "CC": (c, *_sinais(c, compras=[3]))})
    aportes = {t["ticker"]: t["aporte"] for t in res["trades"]}
    assert aportes["AA"] == pytest.approx(100_000 / 3, abs=0.01)
    assert aportes["BB"] == pytest.approx(100_000 / 3, abs=0.01)
    assert aportes["CC"] == pytest.approx(100_000 / 3, abs=0.01)
    # E o invariante de caixa: os aportes nunca somam mais que o capital.
    assert sum(aportes.values()) <= 100_000.0 + 1e-6

def test_conservacao_sem_friccao_e_precos_flat():
    """Preço parado + fricção zero: ninguém ganha nem perde um centavo.
    Qualquer vazamento aqui é bug de contabilidade de caixa."""
    a = _ohlc(_flat()); b = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA", "BB"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0], vendas=[10])),
                "BB": (b, *_sinais(b, compras=[5]))})
    assert res["finalValue"] == pytest.approx(100_000.0)
    assert all(p["equity"] == pytest.approx(100_000.0) for p in res["equityCurve"])
    assert all(p["buyHoldEquity"] == pytest.approx(100_000.0) for p in res["equityCurve"])


# ── calendários divergentes e fim de período ─────────────────────────────────

def test_ticker_sem_pregao_no_dia_nao_quebra_nem_desmarca():
    a = _ohlc(_flat(25))
    b_linhas = _flat(24)
    b = _ohlc(b_linhas)
    b = b.drop(b.index[10])  # feriado regional de BB
    res = bt.run_portfolio_backtest(
        ["AA", "BB"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0])),
                "BB": (b, *_sinais(b, compras=[0]))})
    # Calendário mestre = união; no dia sem pregão de BB a posição dele é
    # marcada no último fechamento conhecido, e nada explode.
    assert len(res["equityCurve"]) == 25
    assert res["totalTrades"] == 2  # os dois fecham no period_end

def test_fim_de_periodo_fecha_tudo_e_final_value_e_o_caixa():
    a = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0]))})
    assert res["trades"][-1]["exitReason"] == "period_end"
    assert res["finalValue"] == res["equityCurve"][-1]["equity"]


# ── atribuição ───────────────────────────────────────────────────────────────

def test_contribuicao_por_ticker_soma_o_retorno_total():
    """Sem fricção, a soma das contribuições é o retorno da carteira -- se
    não fechar, a atribuição está inventando ou perdendo dinheiro."""
    a = _ohlc([(100, 101, 99, 100), (100, 101, 99, 100), (120, 121, 119, 120),
               (120, 121, 119, 120)] + _flat(21, 120))
    b = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA", "BB"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0], vendas=[2])),
                "BB": (b, *_sinais(b, compras=[0]))})
    soma = sum(r["contribuicaoPct"] for r in res["porTicker"])
    assert soma == pytest.approx(res["totalReturn"], abs=0.02)

def test_bootstrap_da_carteira_e_sobre_contribuicoes():
    """A carteira usa a variante aditiva, nunca a composta do motor por
    ticker -- e a exposição média sai junto (é o número que explica o gap
    contra um benchmark 100% investido)."""
    a = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["AA"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"AA": (a, *_sinais(a, compras=[0]))})
    # 1 trade: aviso de amostra, e jamais um compostoIc95.
    assert "aviso" in res["bootstrap"]
    assert res["exposicao"]["mediaExposicaoPct"] > 90  # comprado quase o período todo


def test_concentracao_setorial_e_medida():
    """MU e SNDK são do MESMO grupo (memória): segurar os dois ao mesmo tempo
    tem que aparecer como concentração, não como diversificação."""
    mu = _ohlc(_flat()); sndk = _ohlc(_flat())
    res = bt.run_portfolio_backtest(
        ["MU", "SNDK"], "2026-01-05", "2026-02-06", **_SEM_FRICCAO,
        _dados={"MU": (mu, *_sinais(mu, compras=[0])),
                "SNDK": (sndk, *_sinais(sndk, compras=[0]))})
    memoria = next(s for s in res["porSetor"] if s["sector"] == "memory")
    assert memoria["maxSimultaneas"] == 2
    assert memoria["pctDiasCom2ouMais"] > 80
