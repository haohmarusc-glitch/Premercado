"""
Testes de bounded_parallel.py::budget_from_deadline.

Motivação (visto em produção 02/08/2026): os checkers de spike e bounce
estouraram o timeout de 60s do Node com 1 ms de diferença entre eles --
spawnados juntos, nenhum dos dois entregou -- mesmo com budget interno de 45s
e o map respeitando esse budget.

A causa não era o map: era o budget ser contado a partir do INÍCIO DO MAP, de
modo que a folga de 15s até o timeout externo tinha que cobrir todo o startup
do processo. Só `import agent.get_intraday_spikes` custa ~7,7s numa máquina
ociosa (pandas 5,8s + numpy 2,9s + yfinance 1,6s); sob contenção de CPU, com
três checkers subindo juntos, a folga acabava.

budget_from_deadline() roda DEPOIS dos imports, então o relógio dela já embute
o startup e o orçamento passa a ser o tempo que de fato resta.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_budget_from_deadline.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import time

import pytest

from agent import bounded_parallel as bp


@pytest.fixture(autouse=True)
def _sem_deadline(monkeypatch):
    monkeypatch.delenv(bp.DEADLINE_ENV, raising=False)


def _deadline_daqui_a(segundos: float) -> str:
    return str(int((time.time() + segundos) * 1000))


def test_sem_env_usa_o_fallback():
    """Execução manual do script continua com o budget fixo de antes."""
    assert bp.budget_from_deadline(45) == 45


def test_env_invalido_cai_no_fallback(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, "nao-e-numero")
    assert bp.budget_from_deadline(45) == 45


def test_orcamento_e_o_tempo_restante_menos_a_reserva(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(60))
    budget = bp.budget_from_deadline(45)
    # 60s restantes - 3s de reserva, com folga pro tempo de execução do teste.
    assert 56.0 < budget < 57.1


def test_startup_lento_encolhe_o_orcamento(monkeypatch):
    """O caso real: o processo demorou a chegar aqui, então sobra menos que o
    budget fixo -- e é justamente isso que a constante não enxergava."""
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(20))
    budget = bp.budget_from_deadline(45)
    assert budget < 45, "não pode usar o budget fixo quando resta menos que ele"
    assert 16.0 < budget < 17.1


def test_deadline_estourado_usa_o_piso(monkeypatch):
    """Sem piso o orçamento ficaria negativo e o map devolveria vazio na hora."""
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(-10))
    assert bp.budget_from_deadline(45) == bp.MIN_BUDGET_S


def test_deadline_apertado_usa_o_piso(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(4))
    assert bp.budget_from_deadline(45) == bp.MIN_BUDGET_S


def test_piso_avisa_no_stderr(monkeypatch, capsys):
    """O aviso é o sinal que faltava: sem ele, 'startup comeu o orçamento' não
    aparecia em lugar nenhum -- só o timeout genérico do lado Node."""
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(1))
    bp.budget_from_deadline(45, label="get_intraday_spikes")
    err = capsys.readouterr().err
    assert "get_intraday_spikes" in err
    assert "startup consumiu a folga" in err


def test_reserva_configuravel(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(60))
    budget = bp.budget_from_deadline(45, reserve_s=10)
    assert 49.0 < budget < 50.1


def test_orcamento_menor_que_o_timeout_externo(monkeypatch):
    """Invariante que o módulo existe pra garantir: o Python sempre desiste
    ANTES do Node, com margem pra escrever a saída."""
    for timeout_s in (30, 60, 120):
        monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(timeout_s))
        assert bp.budget_from_deadline(1000) < timeout_s


# ------------------------------------------------- deadline_exceeded ---
#
# Usado pelos scripts que percorrem tickers em SÉRIE (get_performance.py,
# get_earnings.py, earnings_reaction_analysis.py, via routes/scenarios.ts) e
# por isso não têm onde encaixar o bounded_parallel_map. Sem o guard eles
# rodavam até o Node matar o processo, e o trabalho já feito ia junto.


def test_sem_env_nunca_estoura():
    """Execução manual do script segue sem limite nenhum."""
    assert bp.deadline_exceeded() is False


def test_env_invalido_nao_estoura():
    import os
    os.environ[bp.DEADLINE_ENV] = "abacaxi"
    try:
        assert bp.deadline_exceeded() is False
    finally:
        del os.environ[bp.DEADLINE_ENV]


def test_deadline_distante_nao_estoura(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(60))
    assert bp.deadline_exceeded() is False


def test_deadline_vencido_estoura(monkeypatch):
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(-1))
    assert bp.deadline_exceeded() is True


def test_estoura_antes_do_deadline_pela_reserva(monkeypatch):
    """Precisa sobrar tempo pra serializar e escrever a saída -- parar
    exatamente no deadline não adiantaria, o Node já teria matado."""
    monkeypatch.setenv(bp.DEADLINE_ENV, _deadline_daqui_a(1))
    assert bp.deadline_exceeded() is True
    assert bp.deadline_exceeded(reserve_s=0) is False
