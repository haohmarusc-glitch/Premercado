"""Sinal direcional na véspera de balanço vira "aguardar".

A tela de Previsão de Vol já avisava, no mesmo papel e no mesmo dia:

    ⚠ Balanço em 0 dia(s) (2026-08-26) — na véspera de earnings a previsão
      certa vem do threshold da Reação a Earnings, não da banda de vol.

E a Análise Rápida de NVDA, com o balanço saindo depois do fechamento DAQUELE
dia, recomendou COMPRA — "técnico de alta forte sem notícias contrárias". O
aviso existia numa tela e a vizinha nasceu sem ele: quinto caso do mesmo
padrão em 26/08/2026.

O técnico não está errado; ele só não sabe do evento.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import get_trend  # noqa: E402
from agent.get_trend import EARNINGS_VETO_DIAS, balanco_que_veta  # noqa: E402


@pytest.fixture
def calendario(monkeypatch):
    """Substitui a consulta de rede por um valor fixo."""
    def _por(dias, data="2026-08-26"):
        monkeypatch.setattr(
            get_trend, "_earnings_proximo",
            lambda _t: None if dias is None else {"dias": dias, "data": data})
    return _por


# ── o veto atua ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sinal", ["compra", "venda"])
@pytest.mark.parametrize("dias", [0, 1, 2])
def test_sinal_direcional_na_vespera_e_vetado(calendario, sinal, dias):
    calendario(dias)
    assert balanco_que_veta(sinal, "NVDA") == {"dias": dias,
                                               "data": "2026-08-26"}


def test_o_caso_nvda_verbatim(calendario):
    """Balanço no próprio dia, depois do fechamento: a reação é amanhã."""
    calendario(0, "2026-08-26")
    assert balanco_que_veta("compra", "NVDA") is not None


# ── o veto NÃO atua ─────────────────────────────────────────────────────────

def test_balanco_fora_da_janela_nao_veta(calendario):
    calendario(EARNINGS_VETO_DIAS + 1)
    assert balanco_que_veta("compra", "NVDA") is None


def test_sem_balanco_no_calendario_nao_veta(calendario):
    calendario(None)
    assert balanco_que_veta("compra", "ARM") is None


@pytest.mark.parametrize("sinal", ["aguardar", "", None])
def test_sinal_nao_direcional_nao_consulta_a_rede(monkeypatch, sinal):
    """A guarda é de latência E de sentido: sem sinal para vetar, não há o que
    perguntar ao calendário. Uma chamada de rede por ticker para mudar nada,
    no caminho que existe para ser rápido."""
    def _explode(_t):
        raise AssertionError("consultou o calendário sem sinal para vetar")
    monkeypatch.setattr(get_trend, "_earnings_proximo", _explode)
    assert balanco_que_veta(sinal, "NVDA") is None


def test_dias_ausente_nao_derruba(monkeypatch):
    """Calendário torto não pode virar exceção no caminho do sinal."""
    monkeypatch.setattr(get_trend, "_earnings_proximo",
                        lambda _t: {"data": "2026-08-26"})
    assert balanco_que_veta("compra", "NVDA") is None


# ── a janela não pode divergir da do veredito ───────────────────────────────

def test_a_janela_bate_com_a_do_veredito():
    """`EARNINGS_VETO_DIAS` é cópia de `EARNINGS_PROXIMO_DIAS` porque
    get_trend roda por spawn e não importa o pacote de validação. Duas cópias
    da mesma convenção divergindo é como o produto ganha duas respostas para
    a mesma pergunta -- mesmo padrão de test_rvol_abertura.py."""
    from agent.veredito_validator import EARNINGS_PROXIMO_DIAS
    assert EARNINGS_VETO_DIAS == EARNINGS_PROXIMO_DIAS


# ── balanço já divulgado, reação ainda não precificada (MRVL, 28/08/2026) ───
#
# `_earnings_proximo` só enxerga o calendário FUTURO do yfinance, que vira de
# trimestre assim que a empresa reporta -- no exato momento em que a proteção
# mais importa. Estes testes cobrem o segundo caminho de `balanco_que_veta`,
# que olha o balanço PASSADO mais recente via earnings_dates.buscar().

import pandas as pd

from agent.get_trend import _reacao_do_ultimo_balanco_pendente


def _earnings_df(quando: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex([quando]).tz_localize("America/New_York")
    return pd.DataFrame({"EPS Estimate": [1.0]}, index=idx)


def _hist(datas: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"Close": [100.0] * len(datas)},
                         index=pd.DatetimeIndex(datas))


@pytest.fixture
def sem_calendario_futuro(monkeypatch):
    """Simula o calendário futuro já virado de trimestre -- o caminho que
    `balanco_que_veta` testa PRIMEIRO não pode ser o que está vetando aqui."""
    monkeypatch.setattr(get_trend, "_earnings_proximo", lambda _t: None)


def test_amc_com_proximo_pregao_ainda_em_curso_veta(monkeypatch, sem_calendario_futuro):
    """AMC em 27/08; o pregão de 28/08 (a reação) ainda não fechou."""
    df = _earnings_df("2026-08-27 16:05:00")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (df, "yfinance", None))
    monkeypatch.setattr(get_trend, "_janela_da_reacao", lambda _ts: ("seguinte", False))
    monkeypatch.setattr(get_trend, "_sessao_de_hoje_ainda_em_curso", lambda *_a, **_k: True)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) == \
        {"data": "2026-08-27", "tipo": "reacao_pendente"}


def test_amc_com_reacao_ja_fechada_nao_veta(monkeypatch, sem_calendario_futuro):
    """Mesmo evento, mas o pregão de 28/08 já fechou -- reação precificada."""
    df = _earnings_df("2026-08-27 16:05:00")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (df, "yfinance", None))
    monkeypatch.setattr(get_trend, "_janela_da_reacao", lambda _ts: ("seguinte", False))
    monkeypatch.setattr(get_trend, "_sessao_de_hoje_ainda_em_curso", lambda *_a, **_k: False)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) is None


def test_amc_sem_pregao_seguinte_na_serie_ainda_veta(monkeypatch, sem_calendario_futuro):
    """Balanço tão recente que o próprio pregão seguinte nem apareceu no
    histórico -- claramente pendente, sem precisar perguntar à sessão em
    curso (que nem tem barra pra checar)."""
    df = _earnings_df("2026-08-27 16:05:00")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (df, "yfinance", None))
    monkeypatch.setattr(get_trend, "_janela_da_reacao", lambda _ts: ("seguinte", False))
    def _explode(*_a, **_k):
        raise AssertionError("não deveria checar sessão em curso sem pregão seguinte")
    monkeypatch.setattr(get_trend, "_sessao_de_hoje_ainda_em_curso", _explode)
    hist = _hist(["2026-08-27"])
    assert balanco_que_veta("compra", "MRVL", hist) == \
        {"data": "2026-08-27", "tipo": "reacao_pendente"}


def test_bmo_com_sessao_do_anuncio_em_curso_veta(monkeypatch, sem_calendario_futuro):
    """BMO: a reação é o PRÓPRIO pregão do anúncio -- se ele ainda está em
    curso, o resultado do dia ainda não fechou."""
    df = _earnings_df("2026-08-28 08:00:00")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (df, "yfinance", None))
    monkeypatch.setattr(get_trend, "_janela_da_reacao", lambda _ts: ("anuncio", False))
    monkeypatch.setattr(get_trend, "_sessao_de_hoje_ainda_em_curso", lambda *_a, **_k: True)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("venda", "MRVL", hist) == \
        {"data": "2026-08-28", "tipo": "reacao_pendente"}


def test_sem_balanco_passado_no_earnings_dates_nao_veta(monkeypatch, sem_calendario_futuro):
    """Só balanços FUTUROS no retorno (ou nenhum) -- nada para vetar por
    este caminho."""
    df = _earnings_df("2099-01-01 08:00:00")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (df, "yfinance", None))
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) is None


def test_earnings_dates_vazio_nao_veta(monkeypatch, sem_calendario_futuro):
    monkeypatch.setattr(get_trend._earnings_dates, "buscar",
                        lambda *a, **k: (pd.DataFrame(), "yfinance", None))
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) is None


def test_sem_hist_nao_veta_e_nao_consulta_earnings_dates(monkeypatch, sem_calendario_futuro):
    """Sem histórico de preço não há como saber se o próximo pregão fechou
    -- a checagem se cala em vez de arriscar um veto sem base."""
    def _explode(*_a, **_k):
        raise AssertionError("consultou earnings_dates sem hist")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar", _explode)
    assert balanco_que_veta("compra", "MRVL") is None
    assert balanco_que_veta("compra", "MRVL", pd.DataFrame()) is None


def test_falha_na_rede_de_earnings_dates_nao_derruba(monkeypatch, sem_calendario_futuro):
    def _explode(*_a, **_k):
        raise ConnectionError("rede fora")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar", _explode)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) is None


def test_indice_e_etf_nao_consultam_earnings_dates(monkeypatch, sem_calendario_futuro):
    def _explode(*_a, **_k):
        raise AssertionError("índice/ETF não tem balanço, não deveria consultar")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar", _explode)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert _reacao_do_ultimo_balanco_pendente("^GSPC", hist) is None


def test_calendario_futuro_tem_prioridade_sobre_reacao_pendente(monkeypatch):
    """Quando as DUAS checagens teriam motivo pra vetar, o motivo mostrado é
    o da véspera (mais específico: tem `dias`) -- e a segunda checagem nem
    precisa rodar."""
    monkeypatch.setattr(get_trend, "_earnings_proximo",
                        lambda _t: {"dias": 1, "data": "2026-08-29"})
    def _explode(*_a, **_k):
        raise AssertionError("não deveria checar reação pendente com véspera já vetando")
    monkeypatch.setattr(get_trend._earnings_dates, "buscar", _explode)
    hist = _hist(["2026-08-27", "2026-08-28"])
    assert balanco_que_veta("compra", "MRVL", hist) == \
        {"dias": 1, "data": "2026-08-29"}
