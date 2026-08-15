"""
Testes de radar_ia_2026.py -- pacote Radar IA 2026 (dados estáticos de
14/08/2026 + funções de correlação/contágio/dedup).

Sem rede e sem banco: o módulo é dados embutidos + funções puras, então os
testes validam contratos e a matemática, incluindo os 3 casos mínimos
pedidos pelo guia de integração (dedup de cluster, concentração MU+SNDK,
par de baixa correlação MU+CEG).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_radar_ia_2026.py -v
"""
import os
import sys
from datetime import date, timedelta

import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import radar_ia_2026 as radar  # noqa: E402


# ── contagens do pacote (validação do guia: 109/51/33) ─────────────────────

def test_contagens_do_snapshot_batem_com_o_guia():
    assert len(radar.CORRELACOES) == 109
    assert len(radar.EARNINGS) == 51
    assert len(radar.TEMA_IA) == 33
    assert sum(len(v) for v in radar.RISCOS.values()) == 33


# ── correlacao ─────────────────────────────────────────────────────────────

def test_correlacao_simetrica_e_case_insensitive():
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.82)
    assert radar.correlacao("sndk", "mu") == pytest.approx(0.82)


def test_correlacao_consigo_mesmo_e_um():
    assert radar.correlacao("MU", "MU") == 1.0


def test_correlacao_nao_medida_devolve_none():
    assert radar.correlacao("MU", "XXXX") is None


# ── sinais_duplicados / ajustar_scores_por_cluster (Passo 1 do guia) ───────

def test_sinais_duplicados_acha_par_do_mesmo_trade():
    dups = radar.sinais_duplicados(["MU", "SNDK", "NVDA"])
    pares = [d["par"] for d in dups]
    assert ("MU", "SNDK") in pares
    # NVDA nao forma par >= 0.70 com MU/SNDK nos dados medidos
    assert all("NVDA" not in p for p in pares)


def test_ajustar_scores_por_cluster_penaliza_o_menor_do_par():
    # Caso do guia: MU 8.0 / SNDK 7.0 / NVDA 6.0 -> SNDK cai ~7.0*0.59,
    # MU e NVDA intactos.
    scores = radar.ajustar_scores_por_cluster({"MU": 8.0, "SNDK": 7.0, "NVDA": 6.0})
    assert scores["MU"] == pytest.approx(8.0)
    assert scores["NVDA"] == pytest.approx(6.0)
    assert scores["SNDK"] == pytest.approx(7.0 * (1 - 0.82 / 2), abs=0.01)


def test_ajustar_scores_sem_pares_altos_nao_mexe_em_nada():
    scores = radar.ajustar_scores_por_cluster({"MU": 5.0, "CEG": 4.0})
    assert scores == {"MU": 5.0, "CEG": 4.0}


# ── earnings_proximos (default seguro: hoje BRT, nunca o snapshot) ─────────

def test_earnings_proximos_com_ref_do_snapshot_acha_a_semana_de_1808():
    evs = radar.earnings_proximos(dias=5, ref=radar.HOJE_SNAPSHOT)
    tickers = {e["ticker"] for e in evs}
    # semana de 18-19/08 nos dados: HD/BIDU/TOL (18) + ADI/WOLF/LOW/... (19)
    assert "HD" in tickers
    assert "LOW" in tickers
    assert all(e["data"] >= "2026-08-14" for e in evs)


def test_earnings_proximos_default_usa_hoje_nao_o_snapshot(monkeypatch):
    # Congela "hoje" num dia sem nenhum earnings no dataset (2027) -- se o
    # default ainda fosse HOJE_SNAPSHOT, a lista viria cheia.
    monkeypatch.setattr(radar, "today_brt", lambda: date(2027, 6, 1))
    assert radar.earnings_proximos(dias=14) == []


def test_earnings_proximos_ordenado_por_data():
    evs = radar.earnings_proximos(dias=30, ref=radar.HOJE_SNAPSHOT)
    datas = [e["data"] for e in evs]
    assert datas == sorted(datas)


# ── alerta_contagio (Passo 2 do guia) ──────────────────────────────────────

def test_alerta_contagio_nvda_expoe_o_portfolio_default():
    a = radar.alerta_contagio("NVDA", ["MU", "SMCI", "ARM", "MRVL", "AVGO"])
    por_ticker = {p["posicao"]: p for p in a["posicoes_expostas"]}
    # gatilho pré-cadastrado no guia: NVDA -> SMCI 0.51 / AVGO 0.48 / MU 0.44
    assert por_ticker["SMCI"]["correlacao"] == pytest.approx(0.51)
    assert por_ticker["AVGO"]["correlacao"] == pytest.approx(0.48)
    assert por_ticker["MU"]["correlacao"] == pytest.approx(0.44)
    # ordenado por correlação decrescente
    corrs = [p["correlacao"] for p in a["posicoes_expostas"]]
    assert corrs == sorted(corrs, reverse=True)


def test_alerta_contagio_niveis_alto_vs_moderado():
    # MU-SNDK 0.82 >= 0.70 -> ALTO; NVDA-SMCI 0.51 -> moderado
    a = radar.alerta_contagio("SNDK", ["MU"])
    assert a["posicoes_expostas"][0]["nivel"] == "ALTO"
    b = radar.alerta_contagio("NVDA", ["SMCI"])
    assert b["posicoes_expostas"][0]["nivel"] == "moderado"


# ── checagem de concentração (Passo 3 do guia, casos mínimos) ──────────────

def test_par_mu_sndk_e_mesmo_trade():
    dups = radar.sinais_duplicados(["MU", "SNDK"])
    assert len(dups) == 1
    assert dups[0]["correlacao"] >= radar.CORR_ALTA


def test_par_mu_ceg_nao_e_concentracao():
    # corr 0.22 nos dados -- bem abaixo de CORR_ALTA
    assert radar.sinais_duplicados(["MU", "CEG"]) == []
