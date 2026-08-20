"""
"sell" fecha posição comprada; abrir short é decisão de pesquisa, não default.

O diagnóstico de 20/08/2026 (scripts/diagnostico_confluence.py) mediu os
shorts do ConfluenceEngine perdendo em SEIS de seis células ticker x regime --
inclusive no downcycle de 2022-23, onde a MU caiu 32% e os shorts ainda assim
perderam 14,9%. Remover o short melhorou todas as células, e `run_backtest`
virou long_only por default.

Estes testes fixam a semântica dos dois modos com um engine stub (ações
pré-determinadas, preços sintéticos, sem rede) e amarram os dois scripts de
pesquisa ao modo certo: o grid mede a semântica de produção (herda o default)
e o diagnóstico mede o sinal cru dos dois lados (long_only=False explícito) --
herdar o default ali apagaria a régua que o sustenta.

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


def test_sell_com_posicao_zerada_nao_abre_short():
    """O default. O voto vendedor continua na leitura (evaluate_dataframe não
    muda); ele só não vira posição."""
    df = _df([100, 100, 90, 80])
    res = run_backtest(df, _EngineFixo(["flat", "sell", "sell", "flat"]))
    assert res["num_trades"] == 0
    assert res["final_capital"] == 10_000.0


def test_sell_fecha_o_long_sem_inverter():
    df = _df([100, 100, 110, 105])
    # buy abre em 100; sell fecha em 110; o segundo sell NÃO abre short.
    res = run_backtest(df, _EngineFixo(["flat", "buy", "sell", "sell"]))
    assert res["num_trades"] == 1
    assert res["trades"][0]["direction"] == 1
    assert res["trades"][0]["pnl_pct"] == pytest.approx(0.10)
    assert res["final_capital"] == pytest.approx(11_000.0)


def test_long_only_false_preserva_o_comportamento_antigo():
    """O modo de pesquisa: o diagnóstico precisa continuar medindo o lado
    short para a evidência do default não virar dogma imexível."""
    df = _df([100, 100, 90, 80])
    res = run_backtest(df, _EngineFixo(["flat", "sell", "flat", "flat"]),
                       long_only=False)
    assert res["num_trades"] == 1
    assert res["trades"][0]["direction"] == -1
    assert res["trades"][0]["pnl_pct"] == pytest.approx(0.10)  # 100 -> 90, short


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
