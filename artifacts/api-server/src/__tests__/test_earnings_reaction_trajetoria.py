"""
Testes da trajetória pós-earnings em earnings_reaction_analysis.py.

A tabela de reação mostrava só o dia do anúncio e o seguinte — não dizia se
a reação GRUDOU ou foi devolvida. Uma queda de -8% no dia que vira -1% em
duas semanas é uma história; -8% que vira -12% é outra, e a decisão de quem
segura a posição depende de qual das duas.

Funções puras com DataFrames sintéticos, mesma linha de
test_earnings_reaction_runup.py (validar a matemática, não a rede).
"""
import os
import sys

import pandas as pd
import pytest

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent.earnings_reaction_analysis import (  # noqa: E402
    DIAS_TRAJETORIA,
    _trajetoria,
    _trajetoria_resumo,
)


def _hist(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start=pd.Timestamp("2026-01-05"), periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


# ── _trajetoria ─────────────────────────────────────────────────────────────

def test_acumulado_e_sempre_contra_a_vespera_do_balanco():
    """A pergunta de quem segurou a posição é 'onde estou vs ANTES do
    resultado', não vs ontem — por isso acum_pct tem base fixa."""
    # véspera=100, balanço=90 (-10%), depois 91, 92, 93...
    closes = [100.0, 90.0, 91.0, 92.0, 93.0]
    hist = _hist(closes)
    t = _trajetoria(hist, pos=1, prev_close=100.0)
    assert [p["dia"] for p in t] == [1, 2, 3]
    assert t[0]["acum_pct"] == pytest.approx(-9.0)   # 91 vs 100
    assert t[1]["acum_pct"] == pytest.approx(-8.0)   # 92 vs 100
    assert t[2]["acum_pct"] == pytest.approx(-7.0)   # 93 vs 100


def test_variacao_do_dia_e_contra_o_pregao_anterior():
    """acum_pct sozinho esconde o caminho: +2% no D+5 pode ser subida
    contínua ou tombo seguido de recuperação."""
    closes = [100.0, 90.0, 99.0]  # balanço -10%, depois +10% no dia
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0)
    assert t[0]["dia_pct"] == pytest.approx(10.0)
    assert t[0]["acum_pct"] == pytest.approx(-1.0)


def test_para_no_teto_de_dias():
    closes = [100.0] + [100.0 + i for i in range(1, 20)]
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0)
    assert len(t) == DIAS_TRAJETORIA


def test_earnings_recente_devolve_trajetoria_parcial():
    """Balanço de ontem não tem 10 pregões depois — devolve o que existe em
    vez de inventar ou falhar."""
    closes = [100.0, 95.0, 96.0]
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0)
    assert len(t) == 1


def test_sem_pregao_seguinte_devolve_vazio():
    t = _trajetoria(_hist([100.0, 95.0]), pos=1, prev_close=100.0)
    assert t == []


def test_datas_acompanham_os_pregoes():
    t = _trajetoria(_hist([100.0, 95.0, 96.0]), pos=1, prev_close=100.0)
    assert t[0]["date"] == "2026-01-07"  # terceiro dia útil a partir de 05/01


# ── _trajetoria_resumo ──────────────────────────────────────────────────────

def _evento(acumulados: list[float]) -> dict:
    return {"trajetoria": [
        {"dia": i + 1, "date": f"2026-01-{i + 10:02d}", "acum_pct": v, "dia_pct": 0.0}
        for i, v in enumerate(acumulados)
    ]}


def test_media_por_horizonte_mostra_reacao_que_reverte():
    """Dois eventos que caem no D+1 e recuperam até o D+3: o resumo tem que
    deixar a reversão visível, que é o achado acionável."""
    r = _trajetoria_resumo([_evento([-8.0, -5.0, -1.0]), _evento([-6.0, -3.0, 1.0])])
    dias = {d["dia"]: d for d in r["dias"]}
    assert dias[1]["acum_medio_pct"] == pytest.approx(-7.0)
    assert dias[3]["acum_medio_pct"] == pytest.approx(0.0)


def test_n_por_horizonte_e_obrigatorio():
    """Eventos recentes não têm os 10 dias. Sem o `n`, uma média de 1 evento
    pareceria tão sólida quanto uma de 8 — e o D+10 é justamente o horizonte
    com menos amostra."""
    r = _trajetoria_resumo([_evento([-5.0, -4.0, -3.0]), _evento([-2.0])])
    dias = {d["dia"]: d for d in r["dias"]}
    assert dias[1]["n"] == 2
    assert dias[3]["n"] == 1


def test_conta_quantos_ficaram_positivos():
    r = _trajetoria_resumo([_evento([2.0]), _evento([-1.0]), _evento([3.0])])
    assert r["dias"][0]["positivos"] == 2


def test_sem_trajetoria_nenhuma_devolve_none():
    assert _trajetoria_resumo([{"trajetoria": []}]) is None
    assert _trajetoria_resumo([]) is None
