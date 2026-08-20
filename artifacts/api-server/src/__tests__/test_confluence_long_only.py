"""
As duas regras de posição do run_backtest: long-only por default, e execução
na barra SEGUINTE ao sinal.

Long-only (20/08/2026): o diagnóstico (scripts/diagnostico_confluence.py)
mediu os shorts do ConfluenceEngine perdendo em SEIS de seis células
ticker x regime -- inclusive no downcycle de 2022-23, onde a MU caiu 32% e os
shorts ainda assim perderam 14,9%. "sell" fecha posição comprada; abrir short
é decisão de pesquisa (long_only=False), não default.

Execução D+1 (também 20/08/2026): o candle i só é conhecido no próprio
fechamento, então executar "no close de i" usava um preço que não existia na
hora da decisão -- look-ahead A FAVOR da estratégia, apontado por auditoria
externa e confirmado no código. O fill é no open de i+1 (ou no close de i+1
em série só-close), e o sinal do último candle nunca executa.

Estes testes fixam a semântica com um engine stub (ações pré-determinadas,
preços sintéticos, sem rede) e amarram os dois scripts de pesquisa ao modo
certo: o grid mede a semântica de produção (herda o default) e o diagnóstico
mede o sinal cru dos dois lados (long_only=False explícito) -- herdar o
default ali apagaria a régua que o sustenta.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_confluence_long_only.py -v
"""
import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

_AGENT = pathlib.Path(__file__).resolve().parent.parent / "agent"

# O módulo usa imports planos, então é carregado por caminho -- mesmo padrão
# (e mesmo motivo) de test_confluence_engine_fallback.py.
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))
_spec = importlib.util.spec_from_file_location(
    "confluence_engine", str(_AGENT / "confluence_engine.py"))
_ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ce)
run_backtest = _ce.run_backtest


class _EngineFixo:
    """Ações pré-determinadas: o teste é sobre a SEMÂNTICA de posição do
    run_backtest, não sobre os sinais -- que têm testes próprios."""

    def __init__(self, acoes: list):
        self._acoes = acoes

    def evaluate_dataframe(self, df, sector_returns=None, event_dates=None):
        return pd.DataFrame({"action": self._acoes}, index=df.index)

    def kelly_position_size(self, *a, **k) -> float:
        return 1.0  # exposição cheia: o efeito de cada trade fica legível


def _df(closes: list) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-05", periods=len(closes))
    return pd.DataFrame({"close": pd.Series(closes, index=idx, dtype=float)})


# ── long-only ────────────────────────────────────────────────────────────────

def test_sell_com_posicao_zerada_nao_abre_short():
    """O default. O voto vendedor continua na leitura (evaluate_dataframe não
    muda); ele só não vira posição."""
    df = _df([100, 100, 90, 80])
    res = run_backtest(df, _EngineFixo(["flat", "sell", "sell", "flat"]))
    assert res["num_trades"] == 0
    assert res["final_capital"] == 10_000.0

def test_sell_fecha_o_long_sem_inverter():
    # "buy" do candle 0 entra no candle 1 (100); "sell" do candle 1 sai no
    # candle 2 (110); o "sell" do candle 2, já zerado, NÃO abre short.
    df = _df([100, 100, 110, 105])
    res = run_backtest(df, _EngineFixo(["buy", "sell", "sell", "flat"]),
                       slippage_pct=0.0)
    assert res["num_trades"] == 1
    assert res["trades"][0]["direction"] == 1
    assert res["trades"][0]["pnl_pct"] == pytest.approx(0.10)
    assert res["final_capital"] == pytest.approx(11_000.0)

def test_long_only_false_preserva_o_lado_short():
    """O modo de pesquisa: o diagnóstico precisa continuar medindo o lado
    short para a evidência do default não virar dogma imexível."""
    df = _df([100, 100, 90, 80])
    # "sell" do candle 0 abre short no candle 1 (100); "flat" fecha no 2 (90).
    res = run_backtest(df, _EngineFixo(["sell", "flat", "flat", "flat"]),
                       long_only=False, slippage_pct=0.0)
    assert res["num_trades"] == 1
    assert res["trades"][0]["direction"] == -1
    assert res["trades"][0]["pnl_pct"] == pytest.approx(0.10)  # 100 -> 90, short


# ── execução D+1 ─────────────────────────────────────────────────────────────

def test_sinal_do_ultimo_candle_nao_executa():
    """Não existe barra seguinte: em operação real seria a ordem de amanhã.
    Executá-la no próprio candle seria exatamente o look-ahead removido."""
    df = _df([100, 100, 100, 130])
    res = run_backtest(df, _EngineFixo(["flat", "flat", "flat", "buy"]))
    assert res["num_trades"] == 0
    assert res["final_capital"] == 10_000.0

def test_fill_e_no_open_do_dia_seguinte_quando_a_serie_tem_open():
    """Com OHLC de verdade, o primeiro preço disponível depois do sinal é o
    open de D+1 -- nem o close de D (look-ahead), nem o close de D+1 (tarde
    demais de propósito)."""
    idx = pd.bdate_range("2026-01-05", periods=4)
    df = pd.DataFrame({
        "open":  pd.Series([99.0, 104.0, 107.0, 109.0], index=idx),
        "close": pd.Series([100.0, 100.0, 110.0, 110.0], index=idx),
    })
    res = run_backtest(df, _EngineFixo(["buy", "sell", "flat", "flat"]),
                       slippage_pct=0.0)
    t = res["trades"][0]
    assert t["entry_price"] == pytest.approx(104.0)   # open do candle 1
    assert t["exit_price"] == pytest.approx(107.0)    # open do candle 2
    assert t["pnl_pct"] == pytest.approx((107.0 - 104.0) / 104.0)

def test_slippage_encarece_a_entrada_e_a_saida():
    # Mesmo trade do teste acima, com 1% de fricção por lado: compra a
    # 104*1.01 e vende a 107*0.99 -- o pnl encolhe nos dois lados.
    idx = pd.bdate_range("2026-01-05", periods=4)
    df = pd.DataFrame({
        "open":  pd.Series([99.0, 104.0, 107.0, 109.0], index=idx),
        "close": pd.Series([100.0, 100.0, 110.0, 110.0], index=idx),
    })
    res = run_backtest(df, _EngineFixo(["buy", "sell", "flat", "flat"]),
                       slippage_pct=0.01)
    t = res["trades"][0]
    assert t["entry_price"] == pytest.approx(104.0 * 1.01)
    assert t["exit_price"] == pytest.approx(107.0 * 0.99)
    assert t["pnl_pct"] == pytest.approx((107.0 * 0.99 - 104.0 * 1.01) / (104.0 * 1.01))


# ── amarras dos scripts de pesquisa ──────────────────────────────────────────

def test_diagnostico_mede_os_dois_lados():
    """Amarra por leitura de fonte, como test_confluence_parametros já faz com
    o grid: o diagnóstico TEM que passar long_only=False explícito."""
    fonte = (_AGENT / "scripts" / "diagnostico_confluence.py").read_text(encoding="utf-8")
    assert "long_only=False" in fonte

def test_grid_mede_a_semantica_de_producao():
    """O grid NÃO passa long_only: herda o default recomendado, e é assim que
    os números dele passam a descrever o que produção faria."""
    fonte = (_AGENT / "scripts" / "backtest_confluence.py").read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert not any("long_only" in l for l in codigo if "run_backtest(" in l)
