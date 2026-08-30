"""
Testes de brt.py — helpers de data/hora em horário de Brasília, extraídos de
agent.py para que tools.py também possa usá-los (tools.py não pode importar
llm_runtime.py: llm_runtime.py importa tools.py, seria circular).

O bug original está documentado em test_agent_brt_time.py: `date.today()` cru
devolve o fuso do processo (UTC no container), então entre 21h e 23h59 BRT o
"hoje" do container já virou. Aqui o alvo é o consumo em
tools.py::get_earnings_calendar, onde o off-by-one vira `days_until_earnings`
errado -- e esse campo alimenta o gate de earnings do rótulo (≤ 5 dias) no
relatório diário, então o erro deixa de ser cosmético e passa a trocar o
rótulo do ativo.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_brt_helpers.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import datetime

from agent import llm_runtime as agent_module
from agent import brt


def test_today_brt_devolve_date_e_respeita_o_offset():
    """00h30 UTC de 31/07 == 21h30 BRT de 30/07 -- ainda é dia 30 em BRT."""
    now_utc = datetime.datetime(2026, 7, 31, 0, 30, 0)
    assert brt.today_brt(now_utc) == datetime.date(2026, 7, 30)


def test_today_brt_no_meio_do_dia_coincide_com_utc():
    now_utc = datetime.datetime(2026, 7, 30, 19, 27, 0)
    assert brt.today_brt(now_utc) == datetime.date(2026, 7, 30)


def test_days_until_earnings_nao_perde_um_dia_perto_da_meia_noite():
    """O caso que motivou o fix: earnings em 05/08 visto às 21h30 BRT de 30/07.

    Faltam 6 dias (gate de earnings NÃO dispara, teto é 5). Com date.today()
    cru o processo já estaria em 31/07 e contaria 5 -- disparando o gate e
    rebaixando o rótulo do ativo sem motivo.
    """
    now_utc = datetime.datetime(2026, 7, 31, 0, 30, 0)
    earnings = datetime.date(2026, 8, 5)

    dias_brt = (earnings - brt.today_brt(now_utc)).days
    dias_utc_cru = (earnings - now_utc.date()).days

    assert dias_brt == 6
    assert dias_utc_cru == 5
    assert dias_brt != dias_utc_cru


def test_agent_mantem_a_fachada_dos_helpers_antigos():
    """llm_runtime.py expõe os nomes privados de antes -- ~10 usos no módulo e o
    test_agent_brt_time.py dependem deles."""
    now_utc = datetime.datetime(2026, 7, 31, 0, 30, 0)
    assert agent_module._today_brt_str(now_utc) == "30/07/2026"
    assert agent_module._now_brt(now_utc) == brt.now_brt(now_utc)
    assert agent_module._BRT_OFFSET == brt.BRT_OFFSET
