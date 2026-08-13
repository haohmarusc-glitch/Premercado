"""
Testes do run-up pré-earnings em earnings_reaction_analysis.py -- o padrão
"bom não é bom o suficiente" (visto em produção ago/2026: SKHY com lucro
recorde caiu ~9% chegando esticada; DELL sem euforia prévia saltou +32%).

Testa as funções puras (_runup_pct e _runup_summary) com DataFrames
sintéticos -- analyze_ticker inteiro depende de yfinance e fica de fora,
mesma linha dos outros testes do repo (validar a matemática, não a rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_earnings_reaction_runup.py -v
"""
import os
import sys

import pandas as pd
import pytest

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent.earnings_reaction_analysis import (  # noqa: E402
    RUNUP_PREGOES,
    _runup_pct,
    _runup_summary,
)


def _hist_com_precos(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp("2026-08-12"), periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


# ── _runup_pct ──────────────────────────────────────────────────────────────

def test_runup_pct_mede_do_fechamento_21_pregoes_antes_ate_a_vespera():
    # 100 constante até subir pra 120 na véspera do balanço (posição final).
    closes = [100.0] * (RUNUP_PREGOES + 1) + [120.0]
    hist = _hist_com_precos(closes)
    pos = len(closes)  # balanço seria o pregão seguinte ao último
    assert _runup_pct(hist, pos) == pytest.approx(20.0)


def test_runup_pct_none_quando_historico_nao_alcanca():
    hist = _hist_com_precos([100.0] * 10)  # menos que RUNUP_PREGOES+1
    assert _runup_pct(hist, len(hist)) is None


# ── _runup_summary ──────────────────────────────────────────────────────────

def _df_reacoes(pares: list[tuple[float, float]]) -> pd.DataFrame:
    """pares = [(runup_pct, close_pct da reação), ...]"""
    return pd.DataFrame([{"runup_pct": r, "close_pct": c} for r, c in pares])


HIST_NEUTRO = _hist_com_precos([100.0] * (RUNUP_PREGOES + 5))


def test_runup_summary_conta_esticados_que_cairam_e_descontados_que_subiram():
    df = _df_reacoes([
        (15.0, -8.0),   # esticado -> caiu
        (22.0, -3.0),   # esticado -> caiu
        (12.0, 4.0),    # esticado -> subiu (contra o padrão)
        (-5.0, 9.0),    # descontado -> subiu
        (-2.0, -1.0),   # descontado -> caiu (contra o padrão)
        (5.0, 2.0),     # neutro (não entra em nenhum bucket)
    ])
    out = _runup_summary(df, HIST_NEUTRO)
    assert out["esticado_n"] == 3
    assert out["esticado_caiu_n"] == 2
    assert out["descontado_n"] == 2
    assert out["descontado_subiu_n"] == 1
    assert out["esticado_reacao_media"] == pytest.approx((-8.0 - 3.0 + 4.0) / 3, abs=0.01)
    # 6 pares válidos >= 4 -> correlação sai (e o padrão acima é negativo)
    assert out["corr_runup_reacao"] is not None
    assert out["corr_runup_reacao"] < 0


def test_runup_summary_amostra_pequena_nao_inventa_correlacao():
    out = _runup_summary(_df_reacoes([(15.0, -8.0), (-5.0, 9.0), (3.0, 1.0)]), HIST_NEUTRO)
    # 3 pares: buckets saem (contagem é auditável), correlação não (ruído puro)
    assert out["esticado_n"] == 1
    assert out["corr_runup_reacao"] is None


def test_runup_summary_estado_atual_esticado():
    # Sobe de 100 pra 115 nos últimos RUNUP_PREGOES pregões -> +15% >= corte.
    closes = [100.0] * 10 + [100.0] + [115.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["estado_atual"] == "esticado"
    assert out["runup_atual_pct"] == pytest.approx(15.0)


def test_runup_summary_estado_atual_descontado():
    closes = [100.0] * 10 + [100.0] + [92.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["estado_atual"] == "descontado"


def test_runup_summary_sem_eventos_com_runup_so_devolve_estado_atual():
    df = pd.DataFrame([{"close_pct": -3.0}])  # evento antigo sem coluna runup_pct
    out = _runup_summary(df, HIST_NEUTRO)
    assert out["n_com_runup"] == 0
    assert "esticado_n" not in out
