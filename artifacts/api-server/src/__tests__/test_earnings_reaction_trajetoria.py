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
    _acum_benchmark,
    _trajetoria,
    _trajetoria_resumo,
)


def _bench(closes: list[float], start="2026-01-05") -> pd.Series:
    idx = pd.date_range(start=pd.Timestamp(start), periods=len(closes), freq="B")
    return pd.Series(closes, index=idx)


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
    # Série com folga sobre o teto, para provar que ele é o que limita.
    closes = [100.0] + [100.0 + i for i in range(1, DIAS_TRAJETORIA + 6)]
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


# ── excesso sobre o benchmark ───────────────────────────────────────────────

def test_excesso_desconta_a_mare_do_setor():
    """O ponto da coluna: papel +5% num setor que subiu 5% não reagiu ao
    resultado — reagiu junto. Sem o desconto, ciclo de alta vira 'deriva
    pós-earnings positiva' em qualquer papel do grupo."""
    closes = [100.0, 100.0, 105.0]          # papel: +5% acumulado no D+1
    bench = _bench([50.0, 50.0, 52.5])      # benchmark: +5% no mesmo intervalo
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0, bench=bench)
    assert t[0]["acum_pct"] == pytest.approx(5.0)
    assert t[0]["bench_pct"] == pytest.approx(5.0)
    assert t[0]["excesso_pct"] == pytest.approx(0.0)


def test_excesso_positivo_quando_o_papel_bate_o_setor():
    closes = [100.0, 100.0, 110.0]
    bench = _bench([50.0, 50.0, 51.0])      # +2%
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0, bench=bench)
    assert t[0]["excesso_pct"] == pytest.approx(8.0)


def test_queda_menor_que_a_do_setor_e_excesso_positivo():
    """Cair 3% num setor que caiu 8% é força relativa — o retorno cru
    sozinho leria como punição."""
    closes = [100.0, 100.0, 97.0]
    bench = _bench([50.0, 50.0, 46.0])      # -8%
    t = _trajetoria(_hist(closes), pos=1, prev_close=100.0, bench=bench)
    assert t[0]["excesso_pct"] == pytest.approx(5.0)


def test_sem_benchmark_a_trajetoria_sai_sem_excesso():
    """Benchmark fora do ar não pode derrubar a análise: o retorno cru já
    é útil sozinho."""
    t = _trajetoria(_hist([100.0, 95.0, 96.0]), pos=1, prev_close=100.0, bench=None)
    assert "excesso_pct" not in t[0]
    assert t[0]["acum_pct"] == pytest.approx(-4.0)


def test_benchmark_com_calendario_diferente_usa_o_pregao_anterior():
    """Feriado parcial/halt faz o ETF não ter a data exata — asof pega o
    último pregão disponível em vez de estourar e perder o evento."""
    bench = pd.Series([50.0, 52.0], index=pd.to_datetime(["2026-01-05", "2026-01-06"]))
    v = _acum_benchmark(bench, pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-09"))
    assert v == pytest.approx(4.0)  # último disponível (06/01) vs base


def test_acum_benchmark_sem_serie_e_none():
    assert _acum_benchmark(None, pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")) is None


def test_resumo_media_o_excesso_e_conta_quem_bateu_o_setor():
    eventos = [
        {"trajetoria": [{"dia": 1, "acum_pct": 5.0, "excesso_pct": 3.0}]},
        {"trajetoria": [{"dia": 1, "acum_pct": 1.0, "excesso_pct": -1.0}]},
    ]
    r = _trajetoria_resumo(eventos)
    d1 = r["dias"][0]
    assert d1["excesso_medio_pct"] == pytest.approx(1.0)
    assert d1["bateu_bench"] == 1
    assert d1["acum_medio_pct"] == pytest.approx(3.0)


def test_resumo_sem_excesso_omite_os_campos():
    r = _trajetoria_resumo([{"trajetoria": [{"dia": 1, "acum_pct": 2.0}]}])
    assert "excesso_medio_pct" not in r["dias"][0]


def test_janela_e_de_um_mes_de_mercado():
    """Estendida de 10 para 21 quando os dados mostraram casos ainda não
    resolvidos no D+10 (AOSL seguia -7,6%; STX ainda subia)."""
    assert DIAS_TRAJETORIA == 21
