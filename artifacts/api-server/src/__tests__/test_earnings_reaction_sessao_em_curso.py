"""
A barra do dia CORRENTE tem `Close` mesmo antes do fechamento -- só que é o
preço em tempo real, ainda mudando, não o fechamento definitivo. Tratá-la
como reação já ocorrida contamina `close_pct_mean`, o threshold, os buckets
esticado/descontado e a correlação run-up × reação com um número que ainda
vai mudar o resto do pregão.

Auditoria de 27/08/2026 (segunda rodada, depois do fix do run-up ex-evento):
o NVDA reportou AMC em 26/08, e a "reação" de 27/08 (`fech +9,42%`) já
entrava nas estatísticas históricas mesmo com o pregão americano ainda
podendo estar aberto. `sem_barra_incompleta` (market_data_provider.py) não
protege disso -- ela só descarta barra com `Close` vazio, e a barra em curso
TEM Close.

Mesma classe de bug que `_sessao_ainda_aberta` já existe para evitar em
`macro_risk_snapshot.py` (Coreia, 19/08/2026) -- só que lá é offset fixo
(sem horário de verão) e aqui precisa de `zoneinfo` de verdade (Nova York
observa DST).

Rodar: pytest artifacts/api-server/src/__tests__/test_earnings_reaction_sessao_em_curso.py -v
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent.earnings_reaction_analysis import (  # noqa: E402
    RUNUP_PREGOES,
    _sessao_de_hoje_ainda_em_curso,
    _session_move,
    _trajetoria,
    _ultimo_earnings_pos,
)

_NY = ZoneInfo("America/New_York")
_HOJE = pd.Timestamp("2026-08-27")  # sexta-feira -- dia útil, sem ambiguidade de fim de semana


def _hist_com_precos(closes: list[float], fim: pd.Timestamp = _HOJE) -> pd.DataFrame:
    idx = pd.date_range(end=fim, periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes, "Open": closes, "High": closes,
                         "Low": closes, "Volume": [1_000_000.0] * len(closes)}, index=idx)


# ── _sessao_de_hoje_ainda_em_curso ───────────────────────────────────────────

def test_hoje_no_meio_do_pregao_esta_em_curso():
    agora = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)  # 13h ET, mercado aberto
    assert _sessao_de_hoje_ainda_em_curso(_HOJE.date(), agora) is True


def test_hoje_depois_do_fechamento_nao_esta_mais_em_curso():
    agora = datetime(2026, 8, 27, 17, 0, tzinfo=_NY)  # 17h ET, folga de 0.5h já passou
    assert _sessao_de_hoje_ainda_em_curso(_HOJE.date(), agora) is False


def test_dentro_da_margem_apos_o_fechamento_ainda_conta_como_em_curso():
    agora = datetime(2026, 8, 27, 16, 15, tzinfo=_NY)  # 16h15, dentro da folga de 0.5h
    assert _sessao_de_hoje_ainda_em_curso(_HOJE.date(), agora) is True


def test_barra_de_ontem_nunca_esta_em_curso_mesmo_no_horario_de_pregao():
    ontem = pd.Timestamp("2026-08-26").date()
    agora = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)
    assert _sessao_de_hoje_ainda_em_curso(ontem, agora) is False


# ── _session_move ─────────────────────────────────────────────────────────

def test_session_move_nao_devolve_barra_de_hoje_em_curso():
    hist = _hist_com_precos([100.0, 109.42])  # última barra = hoje
    agora_aberto = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)
    assert _session_move(hist, 1, 100.0, agora_ny=agora_aberto) is None


def test_session_move_devolve_a_barra_apos_o_fechamento():
    hist = _hist_com_precos([100.0, 109.42])
    agora_fechado = datetime(2026, 8, 27, 17, 0, tzinfo=_NY)
    mov = _session_move(hist, 1, 100.0, agora_ny=agora_fechado)
    assert mov is not None
    assert mov["close_pct"] == pytest.approx(9.42, abs=0.01)


def test_session_move_de_dia_passado_nao_e_afetado():
    hist = _hist_com_precos([100.0, 109.42, 108.0])
    agora_aberto = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)
    # posição 1 é de ontem (a última é hoje) -- não deve ser bloqueada.
    mov = _session_move(hist, 1, 100.0, agora_ny=agora_aberto)
    assert mov is not None


# ── _trajetoria ───────────────────────────────────────────────────────────

def test_trajetoria_para_antes_do_dia_em_curso():
    # earnings na posição 0; D+1 (posição 1) é HOJE, ainda em curso.
    hist = _hist_com_precos([100.0, 109.42])
    agora_aberto = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)
    traj = _trajetoria(hist, 0, 100.0, dias=5, agora_ny=agora_aberto)
    assert traj == []  # nenhum pregão FECHADO disponível ainda


def test_trajetoria_inclui_o_dia_depois_que_o_pregao_fecha():
    hist = _hist_com_precos([100.0, 109.42])
    agora_fechado = datetime(2026, 8, 27, 17, 0, tzinfo=_NY)
    traj = _trajetoria(hist, 0, 100.0, dias=5, agora_ny=agora_fechado)
    assert len(traj) == 1
    assert traj[0]["acum_pct"] == pytest.approx(9.42, abs=0.01)


# ── _ultimo_earnings_pos ─────────────────────────────────────────────────

def test_ultimo_earnings_pos_recua_quando_a_reacao_de_hoje_ainda_esta_em_curso():
    """Reprodução do NVDA: AMC ontem, reação hoje, pregão americano ainda
    aberto -- a posição não pode ser a de hoje (provisória)."""
    hist = _hist_com_precos([100.0] * (RUNUP_PREGOES + 5) + [98.41, 109.42])
    pos_anuncio = len(hist) - 2  # ontem
    ts_amc = hist.index[pos_anuncio].replace(hour=16, minute=20)
    agora_aberto = datetime(2026, 8, 27, 13, 0, tzinfo=_NY)

    pos = _ultimo_earnings_pos(hist, pd.DatetimeIndex([ts_amc]), agora_ny=agora_aberto)
    assert pos is None  # único earnings, reação ainda em curso -- nada de fechado pra apontar


def test_ultimo_earnings_pos_usa_a_reacao_assim_que_o_pregao_fecha():
    hist = _hist_com_precos([100.0] * (RUNUP_PREGOES + 5) + [98.41, 109.42])
    pos_anuncio = len(hist) - 2
    ts_amc = hist.index[pos_anuncio].replace(hour=16, minute=20)
    agora_fechado = datetime(2026, 8, 27, 17, 0, tzinfo=_NY)

    pos = _ultimo_earnings_pos(hist, pd.DatetimeIndex([ts_amc]), agora_ny=agora_fechado)
    assert pos == pos_anuncio + 1
