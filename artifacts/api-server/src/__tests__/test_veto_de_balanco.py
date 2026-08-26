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
