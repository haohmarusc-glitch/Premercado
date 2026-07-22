"""
Testes das fontes de dados adicionais (gratuitas / tier limitado) --
get_macro_indicators, get_retail_sentiment, get_gamma_exposure,
get_earnings_transcript, get_fundamentals_valuation, get_insider_trades e
_fetch_short_volume_ratio (FINRA). Todas fail-open: sem a env var
configurada, ou se o provedor falhar/estourar o limite, devolvem
configured=false ou um "error" em vez de derrubar o resto do agente.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_market_extras.py -v
"""
from unittest import mock

import pytest

from agent import tools
from agent import config as agent_config


@pytest.fixture(autouse=True)
def _disable_cache(monkeypatch):
    # get_macro_indicators/get_retail_sentiment/get_gamma_exposure/
    # get_earnings_transcript/get_fundamentals_valuation são @cached em disco
    # (chave fixa ou por ticker) -- sem isso, o primeiro teste de cada grupo
    # grava o resultado mockado no cache compartilhado do processo e os
    # testes seguintes (com mocks diferentes) leem o valor velho em vez de
    # rodar de novo.
    monkeypatch.setattr(agent_config, "CACHE_ENABLED", False)


class _FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestMacroIndicators:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        result = tools.get_macro_indicators()
        assert result["configured"] is False

    def test_parses_latest_observation_per_series(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "test-key")
        payload = {"observations": [{"date": "2026-07-01", "value": "3.1"}]}
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(payload)
            result = tools.get_macro_indicators()
        assert result["configured"] is True
        assert result["cpi_index"] == 3.1
        assert result["cpi_index_date"] == "2026-07-01"

    def test_one_series_failing_does_not_break_others(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "test-key")

        def fake_get(url, params=None, timeout=None):
            if params["series_id"] == "UNRATE":
                raise OSError("timeout")
            return _FakeResponse({"observations": [{"date": "2026-07-01", "value": "4.0"}]})

        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.side_effect = fake_get
            result = tools.get_macro_indicators()
        assert result["configured"] is True
        assert result["unemployment_rate_pct"] is None
        assert result["cpi_index"] == 4.0
        assert "errors" in result


class TestRetailSentiment:
    def test_finds_ticker_on_first_page(self, monkeypatch):
        payload = {"pages": 3, "results": [{"ticker": "SMCI", "rank": 2, "mentions": 500, "mentions_24h_ago": 300, "upvotes": 1200, "rank_24h_ago": 5}]}
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(payload)
            result = tools.get_retail_sentiment("SMCI")
        assert result["found"] is True
        assert result["mentions"] == 500
        mreq.get.assert_called_once()

    def test_not_found_after_exhausting_pages(self, monkeypatch):
        payload = {"pages": 1, "results": [{"ticker": "AAPL", "mentions": 100}]}
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(payload)
            result = tools.get_retail_sentiment("SMCI")
        assert result["found"] is False

    def test_rejects_invalid_ticker(self):
        result = tools.get_retail_sentiment("")
        assert "error" in result


class TestGammaExposure:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("FLASHALPHA_API_KEY", raising=False)
        result = tools.get_gamma_exposure("SPY")
        assert result["configured"] is False

    def test_handles_daily_rate_limit_gracefully(self, monkeypatch):
        monkeypatch.setenv("FLASHALPHA_API_KEY", "test-key")
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(status=429)
            result = tools.get_gamma_exposure("SPY")
        assert result["configured"] is True
        assert "error" in result
        assert "5 req" in result["error"] or "limite" in result["error"].lower()

    def test_passes_through_raw_payload_on_success(self, monkeypatch):
        monkeypatch.setenv("FLASHALPHA_API_KEY", "test-key")
        payload = {"symbol": "SPY", "net_gex": 123456.0, "call_wall": 550}
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(payload)
            result = tools.get_gamma_exposure("SPY")
        assert result["configured"] is True
        assert result["net_gex"] == 123456.0
        assert result["call_wall"] == 550


class TestEarningsTranscript:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ROIC_API_KEY", raising=False)
        result = tools.get_earnings_transcript("TSLA")
        assert result["configured"] is False

    def test_handles_rate_limit_gracefully(self, monkeypatch):
        monkeypatch.setenv("ROIC_API_KEY", "test-key")
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(status=429)
            result = tools.get_earnings_transcript("TSLA")
        assert result["configured"] is True
        assert "error" in result

    def test_truncates_long_content(self, monkeypatch):
        monkeypatch.setenv("ROIC_API_KEY", "test-key")
        payload = {"symbol": "TSLA", "year": 2026, "quarter": 2, "date": "2026-07-20", "content": "x" * 7000}
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(payload)
            result = tools.get_earnings_transcript("TSLA", max_chars=6000)
        assert result["content"].endswith("[TRUNCADO]")
        assert len(result["content"]) < 7000


class TestFundamentalsValuation:
    def test_returns_not_configured_without_api_key(self, monkeypatch):
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        result = tools.get_fundamentals_valuation("NVDA")
        assert result["configured"] is False

    def test_computes_implied_upside(self, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "test-key")
        dcf_payload = [{"symbol": "NVDA", "dcf": 220.0, "Stock Price": 200.0}]
        metrics_payload = [{"peRatioTTM": 45.2, "pbRatioTTM": 30.1, "roeTTM": 0.9, "evToEbitdaTTM": 40.0}]

        def fake_get(url, params=None, timeout=None):
            if "discounted-cash-flow" in url:
                return _FakeResponse(dcf_payload)
            return _FakeResponse(metrics_payload)

        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.side_effect = fake_get
            result = tools.get_fundamentals_valuation("NVDA")
        assert result["configured"] is True
        assert result["dcf_fair_value"] == 220.0
        assert result["dcf_implied_upside_pct"] == 10.0
        assert result["pe_ratio_ttm"] == 45.2


class TestInsiderTrades:
    def test_delegates_to_get_alt_data(self, monkeypatch):
        with mock.patch.object(tools._alt_data, "insider_trades", return_value={"configured": True, "trades": [{"ticker": "MU"}]}) as m:
            result = tools.get_insider_trades("MU")
        assert result["configured"] is True
        m.assert_called_once_with({"MU"})

    def test_rejects_invalid_ticker(self):
        result = tools.get_insider_trades("")
        assert "error" in result


class TestFinraShortVolume:
    def test_returns_none_when_no_file_found_in_window(self, monkeypatch):
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(status=404)
            ratio, note = tools._fetch_short_volume_ratio("MU")
        assert ratio is None
        assert "FINRA" in note

    def test_parses_matching_ticker_row(self, monkeypatch):
        text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n2026-07-21|MU|4000|0|10000|Q\n2026-07-21|AAPL|100|0|500|Q\n"
        with mock.patch.object(tools, "requests") as mreq:
            mreq.get.return_value = _FakeResponse(text=text)
            ratio, note = tools._fetch_short_volume_ratio("MU")
        assert ratio == 40.0
        assert "FINRA" in note
