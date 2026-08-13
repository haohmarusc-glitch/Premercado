"""
Testes de get_earnings.py -- trava que get_earnings() DEVOLVE o resultado (em
vez de só imprimir e retornar None implicitamente).

Por que este teste existe: visto em produção (13/08) -- entry_exit_study.py
chama get_earnings([ticker]) como função de biblioteca esperando o valor de
volta, mas a função só fazia `print(json.dumps(result))` sem `return result`.
Dois efeitos, os dois reais:
  1. earningsDate saía sempre None no chamador (o valor existia, só nunca
     chegava -- `if earnings_info:` era sempre False contra um None).
  2. O print poluía o stdout do PRÓPRIO processo do chamador (entry_exit_
     study.py também imprime JSON em stdout no fim -- o Node falhava o
     JSON.parse porque o array de get_earnings vinha ANTES do JSON real).
O fix moveu o print pra dentro do bloco `if __name__ == "__main__":`
(mesmo padrão de get_scenario_params.py), deixando get_earnings() puro:
devolve, não imprime.

get_earnings.py roda como script standalone com imports "flat", mesmo padrão
de test_get_news_feed.py -- carrega via importlib.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_get_earnings.py -v
"""
import os
import sys
import io
import contextlib
import importlib.util

import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_MODULE_PATH = os.path.join(_AGENT_DIR, "get_earnings.py")
_spec = importlib.util.spec_from_file_location("get_earnings", _MODULE_PATH)
ge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ge)


class _FakeCalendar:
    empty = False
    columns = ["2026-11-03"]

    def tolist(self):
        return self.columns


class _FakeTicker:
    def __init__(self, ticker):
        self.info = {"shortName": "Super Micro Computer, Inc.", "epsForward": 4.07, "sector": "Technology"}

        class _Cal:
            empty = False
            columns = type("Cols", (), {"tolist": lambda self: ["2026-11-03"]})()

        self.calendar = _Cal()


def test_get_earnings_devolve_lista_nao_none(monkeypatch):
    monkeypatch.setattr(ge, "deadline_exceeded", lambda: False)
    monkeypatch.setattr(ge.yf, "Ticker", lambda t: _FakeTicker(t))

    resultado = ge.get_earnings(["SMCI"])

    assert resultado is not None
    assert isinstance(resultado, list)
    assert resultado[0]["ticker"] == "SMCI"
    assert resultado[0]["earningsDate"] == "2026-11-03"


def test_get_earnings_nao_imprime_nada_em_stdout(monkeypatch):
    """A função pura não deve ter efeito colateral de I/O -- só o bloco
    __main__ imprime. Um chamador (ex.: entry_exit_study.py) que também
    imprime JSON em stdout no fim do próprio processo quebraria se
    get_earnings() poluísse esse canal."""
    monkeypatch.setattr(ge, "deadline_exceeded", lambda: False)
    monkeypatch.setattr(ge.yf, "Ticker", lambda t: _FakeTicker(t))

    captura = io.StringIO()
    with contextlib.redirect_stdout(captura):
        ge.get_earnings(["SMCI"])

    assert captura.getvalue() == ""


def test_get_earnings_ticker_sem_dados_devolve_none_no_campo(monkeypatch):
    monkeypatch.setattr(ge, "deadline_exceeded", lambda: False)
    resultado = ge.get_earnings(["SPY"])  # está em _NO_EARNINGS_TICKERS
    assert resultado[0]["earningsDate"] is None
