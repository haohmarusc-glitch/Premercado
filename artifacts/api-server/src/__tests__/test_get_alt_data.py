"""
Testes de get_alt_data.py — congress_trades() e dark_pool_flow() ambos
"fail closed" (configured: false) sem API key, e filtram/normalizam
corretamente a resposta dos provedores quando a key está presente (mockado,
sem rede real).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_get_alt_data.py -v
"""
from unittest import mock

from agent import get_alt_data as gad


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestCongressTrades:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("QUIVER_API_KEY", raising=False)
        result = gad.congress_trades({"NVDA"})
        assert result["configured"] is False
        assert "trades" not in result

    def test_filters_to_requested_tickers_and_normalizes_house_and_senate_rows(self, monkeypatch):
        monkeypatch.setenv("QUIVER_API_KEY", "test-key")
        payload = [
            {"Ticker": "NVDA", "Representative": "Jane Doe", "Transaction": "Purchase", "Range": "$1,001 - $15,000", "TransactionDate": "2026-06-01", "Filed": "2026-06-10"},
            {"Ticker": "AAPL", "Representative": "Someone Else", "Transaction": "Sale", "Range": "$15,001 - $50,000", "TransactionDate": "2026-06-02"},
            {"Ticker": "MU", "Senator": "John Smith", "Transaction": "Purchase", "Amount": "$50,001 - $100,000", "TransactionDate": "2026-06-03"},
        ]
        with mock.patch.object(gad.requests, "get", return_value=_FakeResponse(payload)) as m:
            result = gad.congress_trades({"NVDA", "MU"})

        assert result["configured"] is True
        tickers = {t["ticker"] for t in result["trades"]}
        assert tickers == {"NVDA", "MU"}
        by_ticker = {t["ticker"]: t for t in result["trades"]}
        assert by_ticker["NVDA"]["chamber"] == "house"
        assert by_ticker["MU"]["chamber"] == "senate"
        assert by_ticker["MU"]["range"] == "$50,001 - $100,000"
        # Authorization header sent correctly
        _, kwargs = m.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"

    def test_reports_error_but_stays_configured_on_request_failure(self, monkeypatch):
        monkeypatch.setenv("QUIVER_API_KEY", "test-key")
        with mock.patch.object(gad.requests, "get", side_effect=OSError("timeout")):
            result = gad.congress_trades({"NVDA"})
        assert result["configured"] is True
        assert "error" in result


class TestDarkPoolFlow:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("UNUSUAL_WHALES_API_KEY", raising=False)
        result = gad.dark_pool_flow({"NVDA"})
        assert result["configured"] is False

    def test_filters_to_requested_tickers_from_wrapped_data_field(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        payload = {"data": [
            {"ticker": "NVDA", "price": "150.0", "size": 10000, "premium": "1500000", "executed_at": "2026-06-01T14:00:00Z"},
            {"ticker": "AAPL", "price": "200.0", "size": 5000, "premium": "1000000", "executed_at": "2026-06-01T14:05:00Z"},
        ]}
        with mock.patch.object(gad.requests, "get", return_value=_FakeResponse(payload)):
            result = gad.dark_pool_flow({"NVDA"})

        assert result["configured"] is True
        assert len(result["trades"]) == 1
        assert result["trades"][0]["ticker"] == "NVDA"

    def test_handles_bare_list_response_not_wrapped_in_data(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        payload = [{"ticker": "NVDA", "price": "150.0", "size": 10000, "premium": "1500000", "executedAt": "2026-06-01T14:00:00Z"}]
        with mock.patch.object(gad.requests, "get", return_value=_FakeResponse(payload)):
            result = gad.dark_pool_flow({"NVDA"})
        assert result["configured"] is True
        assert len(result["trades"]) == 1

    def test_reports_error_but_stays_configured_on_request_failure(self, monkeypatch):
        monkeypatch.setenv("UNUSUAL_WHALES_API_KEY", "test-key")
        with mock.patch.object(gad.requests, "get", side_effect=OSError("timeout")):
            result = gad.dark_pool_flow({"NVDA"})
        assert result["configured"] is True
        assert "error" in result


class TestSanitizeTicker:
    def test_rejects_invalid_ticker(self):
        import pytest
        with pytest.raises(ValueError):
            gad.sanitize_ticker("")

    def test_uppercases_and_strips_junk_chars(self):
        assert gad.sanitize_ticker("nvda") == "NVDA"
