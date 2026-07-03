"""
Testes de get_institutional_filings.py — resolução da lista de gestores
(default vs override por env) e o parsing da resposta de submissions.json da
SEC (mockado, sem rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_get_institutional_filings.py -v
"""
import io
import json
from contextlib import contextmanager
from unittest import mock

import pytest

from agent import get_institutional_filings as gif


class TestResolveFilers:
    def test_uses_default_list_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("INSTITUTIONAL_CIKS", raising=False)
        assert gif.resolve_filers() == gif.DEFAULT_FILERS

    def test_uses_default_list_when_env_blank(self, monkeypatch):
        monkeypatch.setenv("INSTITUTIONAL_CIKS", "   ")
        assert gif.resolve_filers() == gif.DEFAULT_FILERS

    def test_parses_env_override(self, monkeypatch):
        monkeypatch.setenv("INSTITUTIONAL_CIKS", "1067983:Berkshire, 37389:Renaissance")
        result = gif.resolve_filers()
        assert result == [("0001067983", "Berkshire"), ("0000037389", "Renaissance")]

    def test_ignores_malformed_entries_but_keeps_valid_ones(self, monkeypatch):
        monkeypatch.setenv("INSTITUTIONAL_CIKS", "notanumber:Bad,,1067983:Berkshire")
        result = gif.resolve_filers()
        assert result == [("0001067983", "Berkshire")]

    def test_falls_back_to_default_when_all_entries_malformed(self, monkeypatch):
        monkeypatch.setenv("INSTITUTIONAL_CIKS", "abc:Bad,xyz:AlsoBad")
        assert gif.resolve_filers() == gif.DEFAULT_FILERS


@contextmanager
def _mock_urlopen(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with mock.patch.object(gif.urllib.request, "urlopen", return_value=_Resp(body)):
        yield


class TestFetchFiler:
    def test_returns_latest_and_previous_13f_hr(self):
        payload = {
            "name": "Berkshire Hathaway Inc",
            "filings": {"recent": {
                "form": ["4", "13F-HR", "13F-HR/A", "13F-HR"],
                "filingDate": ["2026-06-01", "2026-05-15", "2026-05-01", "2026-02-14"],
                "accessionNumber": ["0000000000-26-000001", "0000000000-26-000002", "0000000000-26-000003", "0000000000-26-000004"],
            }},
        }
        with _mock_urlopen(payload):
            result = gif.fetch_filer("1067983", "Berkshire Hathaway")

        assert result["name"] == "Berkshire Hathaway Inc"
        assert result["latestFiling"]["accessionNumber"] == "0000000000-26-000002"
        assert result["previousFiling"]["accessionNumber"] == "0000000000-26-000003"
        assert "sec.gov/Archives/edgar/data/1067983/" in result["latestFiling"]["url"]

    def test_reports_error_when_no_13f_hr_present(self):
        payload = {"name": "Some Fund", "filings": {"recent": {
            "form": ["4", "8-K"],
            "filingDate": ["2026-06-01", "2026-05-01"],
            "accessionNumber": ["0000000000-26-000001", "0000000000-26-000002"],
        }}}
        with _mock_urlopen(payload):
            result = gif.fetch_filer("9999999", "Some Fund")
        assert "error" in result

    def test_reports_error_on_network_failure(self):
        with mock.patch.object(gif.urllib.request, "urlopen", side_effect=OSError("boom")):
            result = gif.fetch_filer("1067983", "Berkshire Hathaway")
        assert "error" in result
        assert result["cik"] == "0001067983"
