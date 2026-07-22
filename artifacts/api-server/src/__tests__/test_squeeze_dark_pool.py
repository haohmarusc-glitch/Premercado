"""
Testes de tools._fetch_dark_pool_activity() — enriquecimento opcional do
check_squeeze_setup via Unusual Whales (get_alt_data.dark_pool_flow).
Fail-open sem UNUSUAL_WHALES_API_KEY, e resume corretamente os trades quando
a chave está presente (mockado, sem rede real).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_squeeze_dark_pool.py -v
"""
from unittest import mock

from agent import tools


class TestFetchDarkPoolActivity:
    def test_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("UNUSUAL_WHALES_API_KEY", raising=False)
        activity, note = tools._fetch_dark_pool_activity("SMCI")
        assert activity is None
        assert "UNUSUAL_WHALES_API_KEY" in note

    def test_returns_none_with_note_when_no_trades_found(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        with mock.patch.object(tools._alt_data, "dark_pool_flow", return_value={"configured": True, "trades": []}):
            activity, note = tools._fetch_dark_pool_activity("SMCI")
        assert activity is None
        assert "dark pool" in note.lower()

    def test_summarizes_trade_count_and_total_premium(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        trades = [
            {"ticker": "SMCI", "price": "30.75", "size": 10000, "premium": "150000.50", "executedAt": "2026-07-21T20:00:00Z"},
            {"ticker": "SMCI", "price": "31.00", "size": 5000, "premium": "50000.25", "executedAt": "2026-07-21T20:05:00Z"},
        ]
        with mock.patch.object(tools._alt_data, "dark_pool_flow", return_value={"configured": True, "trades": trades}):
            activity, note = tools._fetch_dark_pool_activity("SMCI")
        assert activity == {"trade_count": 2, "total_premium": 200000.75}
        assert "Unusual Whales" in note

    def test_fails_open_when_provider_reports_error(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        with mock.patch.object(tools._alt_data, "dark_pool_flow", return_value={"configured": True, "error": "timeout"}):
            activity, note = tools._fetch_dark_pool_activity("SMCI")
        assert activity is None
        assert "timeout" in note

    def test_fails_open_on_unexpected_exception(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        with mock.patch.object(tools._alt_data, "dark_pool_flow", side_effect=OSError("boom")):
            activity, note = tools._fetch_dark_pool_activity("SMCI")
        assert activity is None
        assert "boom" in note
