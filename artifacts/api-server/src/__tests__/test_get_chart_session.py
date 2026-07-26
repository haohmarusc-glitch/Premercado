"""
Testes de get_chart._session_for() -- classifica cada candle intradiário
(1d/5d) como "pre" | "regular" | "post" pra colorir o gráfico de linha por
sessão. Períodos diários/semanais (1mo+) não têm essa distinção, sempre
"regular" (cada candle já é um pregão inteiro).

get_chart.py roda como script standalone (não faz parte do pacote `agent`
importável, é spawnado direto por chart.ts) -- carrega via importlib pra
não precisar de um __init__ novo só pra isso.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_get_chart_session.py -v
"""
import datetime
import importlib.util
import os

import pytest

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "agent", "get_chart.py")
_spec = importlib.util.spec_from_file_location("get_chart", _MODULE_PATH)
get_chart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(get_chart)


class _FakeTimestamp:
    def __init__(self, hour: int, minute: int):
        self._time = datetime.time(hour, minute)

    def time(self):
        return self._time


class TestSessionFor:
    def test_not_intraday_is_always_regular(self):
        assert get_chart._session_for(_FakeTimestamp(0, 0), intraday=False) == "regular"
        assert get_chart._session_for(_FakeTimestamp(23, 59), intraday=False) == "regular"

    @pytest.mark.parametrize("hour,minute", [(4, 0), (8, 0), (9, 29)])
    def test_before_market_open_is_pre(self, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True) == "pre"

    @pytest.mark.parametrize("hour,minute", [(9, 30), (12, 0), (15, 59)])
    def test_during_regular_hours_is_regular(self, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True) == "regular"

    @pytest.mark.parametrize("hour,minute", [(16, 0), (18, 0), (19, 59)])
    def test_after_market_close_is_post(self, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True) == "post"


class TestSessionForB3:
    """Tickers .SA (B3): pregão 10h-17h horário de Brasília, sem pré-mercado."""

    @pytest.mark.parametrize("symbol", ["PETR4.SA", "petr4.sa", "VALE3.SA"])
    @pytest.mark.parametrize("hour,minute", [(0, 0), (8, 0), (9, 59)])
    def test_before_b3_open_is_pre(self, symbol, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True, symbol=symbol) == "pre"

    @pytest.mark.parametrize("hour,minute", [(10, 0), (13, 0), (16, 59)])
    def test_during_b3_hours_is_regular(self, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True, symbol="PETR4.SA") == "regular"

    @pytest.mark.parametrize("hour,minute", [(17, 0), (18, 0), (23, 59)])
    def test_after_b3_close_is_post(self, hour, minute):
        assert get_chart._session_for(_FakeTimestamp(hour, minute), intraday=True, symbol="PETR4.SA") == "post"

    def test_us_ticker_unaffected_by_b3_hours(self):
        # 10h30 é "regular" na B3 mas ainda "pre" no horário de NY (9h30 ET).
        assert get_chart._session_for(_FakeTimestamp(9, 0), intraday=True, symbol="NVDA") == "pre"
