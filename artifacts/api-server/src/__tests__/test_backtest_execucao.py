"""
A semântica de execução de _simulate (backtest.py) depois da auditoria de
20/08/2026 -- os dois vieses A FAVOR da estratégia que ela apontou e o código
confirmou:

  1. o sinal do candle D executava no próprio close de D, um preço que não
     existia na hora da decisão (look-ahead). Agora: ordem na ABERTURA de D+1.
  2. stop/take-profit checavam só o Close -- stop tocado intradia e devolvido
     não existia. Agora: gap de abertura sai no open; toque intradia sai no
     nível, via High/Low; stop e target no MESMO candle assumem o stop
     (política conservadora -- o OHLC não diz qual veio primeiro, e a escolha
     otimista inflaria o resultado justamente nos dias mais voláteis).

Os testes chamam _simulate direto com OHLC sintético e séries de sinal
explícitas -- sem rede, sem mock de yfinance, sem indicador no meio: cada
assert é sobre a regra de execução, nada mais. Fricções zeradas onde o número
redondo importa.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_execucao.py -v
"""
import pandas as pd
import pytest

from agent import backtest as bt

# > 20 pregões (piso do _simulate); os cenários usam os primeiros candles e
# deixam o resto flat em 100.
_N = 25


def _ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """rows = [(open, high, low, close), ...], completado até _N com dias
    flat a 100 -- inertes por construção nos cenários abaixo."""
    completo = list(rows) + [(100.0, 100.0, 100.0, 100.0)] * (_N - len(rows))
    idx = pd.bdate_range("2026-01-05", periods=_N)
    return pd.DataFrame(
        {c: pd.Series([r[i] for r in completo], index=idx, dtype=float)
         for i, c in enumerate(("open", "high", "low", "close"))})


def _sinais(df: pd.DataFrame, buys: list[int] = (), sells: list[int] = ()):
    buy = pd.Series(False, index=df.index)
    sell = pd.Series(False, index=df.index)
    for i in buys:
        buy.iloc[i] = True
    for i in sells:
        sell.iloc[i] = True
    return buy, sell


def _simular(df, buys=(), sells=(), **kw):
    buy, sell = _sinais(df, buys, sells)
    params = dict(position_fraction=1.0, commission_pct=0.0, slippage_pct=0.0,
                  stop_loss_pct=None, take_profit_pct=None)
    params.update(kw)
    return bt._simulate("TST", "manual", "2026-01-05", "2026-02-06",
                        df, buy, sell, **params)


# ── execução D+1 ─────────────────────────────────────────────────────────────

def test_sinal_de_hoje_executa_no_open_de_amanha():
    df = _ohlc([(100, 101, 99, 100),     # candle 0: sinal de compra
                (104, 106, 103, 105),    # candle 1: entrada no OPEN = 104
                (107, 108, 106, 107)])   # candle 2: sinal de venda (sai no open do 3)
    res = _simular(df, buys=[0], sells=[2])
    t = res["trades"][0]
    assert t["entryDate"] == str(df.index[1])[:10]
    assert t["entryPrice"] == pytest.approx(104.0)
    # sell no candle 2 -> sai no open do candle 3 (100, o flat de preenchimento)
    assert t["exitDate"] == str(df.index[3])[:10]
    assert t["exitPrice"] == pytest.approx(100.0)

def test_sinal_no_ultimo_candle_nao_executa():
    """Não há barra seguinte: seria a ordem de amanhã, não um trade de hoje."""
    df = _ohlc([])
    res = _simular(df, buys=[_N - 1])
    assert res["totalTrades"] == 0
    assert all(e["equity"] == pytest.approx(10_000.0) for e in res["equityCurve"])

def test_close_do_dia_do_sinal_nao_e_mais_o_fill():
    """A regressão exata do look-ahead: candle do sinal fecha barato, o dia
    seguinte abre com gap. O backtest antigo comprava no close barato que só
    era conhecido depois de fechar; o honesto paga o gap."""
    df = _ohlc([(100, 101, 99, 100),
                (113, 115, 112, 114)])   # gap de +13% na abertura
    res = _simular(df, buys=[0])
    assert res["trades"][0]["entryPrice"] == pytest.approx(113.0)


# ── stop/target contra o pregão inteiro ──────────────────────────────────────

def test_stop_tocado_intradia_sai_no_nivel_do_stop():
    """O caso que o Close sozinho não via: Low fura o stop, Close volta acima."""
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 99, 100),     # entrada a 100 (open)
                (100, 101, 93, 99)])     # Low 93 fura o stop de 95; Close 99 "esconde"
    res = _simular(df, buys=[0], stop_loss_pct=0.05)
    t = res["trades"][0]
    assert t["exitReason"] == "stop_loss"
    assert t["exitDate"] == str(df.index[2])[:10]
    assert t["exitPrice"] == pytest.approx(95.0)   # fill no NÍVEL, não no Low

def test_gap_de_abertura_atraves_do_stop_sai_no_open():
    """Abriu abaixo do stop: não existe fill no nível -- o primeiro preço do
    dia já é pior. Fingir fill a 95 num dia que abriu a 90 inflaria o resultado."""
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 99, 100),     # entrada a 100
                (90, 92, 89, 91)])       # abre a 90, stop era 95
    res = _simular(df, buys=[0], stop_loss_pct=0.05)
    t = res["trades"][0]
    assert t["exitReason"] == "stop_loss"
    assert t["exitPrice"] == pytest.approx(90.0)

def test_target_tocado_intradia_sai_no_nivel():
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 99, 100),     # entrada a 100
                (100, 112, 99, 101)])    # High 112 passa o target de 110; Close volta
    res = _simular(df, buys=[0], take_profit_pct=0.10)
    t = res["trades"][0]
    assert t["exitReason"] == "take_profit"
    assert t["exitPrice"] == pytest.approx(110.0)

def test_stop_e_target_no_mesmo_candle_assume_o_stop():
    """A ambiguidade central do OHLC diário: os dois níveis dentro do mesmo
    candle, sem como saber a ordem. A política é a conservadora."""
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 99, 100),                # entrada a 100
                (100, 115, 92, 105)])               # High passa 110 E Low fura 95
    res = _simular(df, buys=[0], stop_loss_pct=0.05, take_profit_pct=0.10)
    t = res["trades"][0]
    assert t["exitReason"] == "stop_loss"
    assert t["exitPrice"] == pytest.approx(95.0)

def test_stop_pode_disparar_no_proprio_dia_da_entrada():
    """A entrada é no open; o resto do pregão existe. Um crash à tarde não
    espera o dia seguinte para valer."""
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 90, 92)])     # entra a 100 no open, Low 90 fura o stop
    res = _simular(df, buys=[0], stop_loss_pct=0.05)
    t = res["trades"][0]
    assert t["exitReason"] == "stop_loss"
    assert t["entryDate"] == t["exitDate"]

def test_saida_por_sinal_e_no_open_e_vem_antes_da_checagem_do_dia():
    """A saída decidida ontem executa no primeiro preço de hoje (open) -- o
    stop do MESMO dia, que só se resolve intradia, não passa na frente dela."""
    df = _ohlc([(100, 101, 99, 100),
                (100, 101, 99, 100),     # entrada a 100
                (100, 101, 99, 100),     # candle 2: sinal de venda
                (98, 99, 90, 91)])       # candle 3: sai no open 98 (não no stop 95)
    res = _simular(df, buys=[0], sells=[2], stop_loss_pct=0.05)
    t = res["trades"][0]
    assert t["exitReason"] == "signal"
    assert t["exitPrice"] == pytest.approx(98.0)


# ── dado degradado ───────────────────────────────────────────────────────────

def test_ohlc_degradado_pro_close_vira_fill_no_close_de_d1():
    """_fetch_warmed_ohlc preenche open/high/low ausentes com o close do dia.
    Nesse modo o fill degenera pro close de D+1 -- mais tardio que um open
    real, mas ainda sem look-ahead -- e a simulação segue coerente."""
    idx = pd.bdate_range("2026-01-05", periods=_N)
    closes = pd.Series([100.0] * _N, index=idx)
    closes.iloc[1] = 105.0
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes})
    res = _simular(df, buys=[0])
    t = res["trades"][0]
    assert t["entryPrice"] == pytest.approx(105.0)   # close de D+1 como fill
    assert t["exitReason"] == "period_end"           # nunca houve sinal de saída
