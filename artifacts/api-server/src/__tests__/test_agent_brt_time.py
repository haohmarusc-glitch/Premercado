"""
Testes de agent.py::_now_brt/_today_brt_str/_now_brt_str -- cobre o bug em que
os prompts do agente ("Data de hoje: X", "às Y de X (Y BRT)") usavam
datetime.date.today()/datetime.datetime.now() cru, que retorna o fuso do
processo (UTC no container) em vez de Brasília. No horário BRT 21h-23h59
(= UTC 00h-02h59 do dia seguinte), isso fazia o agente achar que já era o
dia seguinte -- earnings/plano de saída saíam com "dias restantes" errado
em qualquer relatório gerado nessa janela.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_agent_brt_time.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import datetime

from agent import agent as agent_module


def test_today_brt_str_ainda_e_dia_anterior_perto_da_meia_noite_utc():
    """21h30 BRT de 30/07 == 00h30 UTC de 31/07 -- o dia BRT ainda é 30/07,
    mesmo já sendo 31/07 no fuso (UTC) do processo/container."""
    now_utc = datetime.datetime(2026, 7, 31, 0, 30, 0)
    assert agent_module._today_brt_str(now_utc) == "30/07/2026"


def test_today_brt_str_bate_com_utc_no_meio_do_dia():
    """Longe da virada, dia BRT e dia UTC coincidem -- 16h27 BRT de 30/07 ==
    19h27 UTC de 30/07."""
    now_utc = datetime.datetime(2026, 7, 30, 19, 27, 0)
    assert agent_module._today_brt_str(now_utc) == "30/07/2026"


def test_now_brt_str_converte_a_hora_tambem():
    now_utc = datetime.datetime(2026, 7, 30, 19, 27, 0)
    assert agent_module._now_brt_str(now_utc) == "16:27"
