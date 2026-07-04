"""
Testes de risk_manager.py — position_size/risk_reward (matemática simples) e
correlation() (correlação de Pearson entre retornos, mockando yf.download pra
não depender de rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_risk_manager.py -v
"""

import numpy as np
import pandas as pd
import pytest

from agent import risk_manager as rm


class TestPositionSize:
    def test_computes_shares_from_risk_amount(self):
        result = rm.position_size(account_size=10_000, risk_pct=1, entry=100, stop=95)
        assert result["riskAmount"] == pytest.approx(100.0)
        assert result["riskPerShare"] == pytest.approx(5.0)
        assert result["shares"] == pytest.approx(20.0)

    def test_rejects_equal_entry_and_stop(self):
        result = rm.position_size(account_size=10_000, risk_pct=1, entry=100, stop=100)
        assert "error" in result


class TestRiskReward:
    def test_computes_ratio(self):
        result = rm.risk_reward(entry=100, stop=95, target=115)
        assert result["ratio"] == pytest.approx(3.0)
        assert result["favorable"] is True

    def test_flags_unfavorable_ratio(self):
        result = rm.risk_reward(entry=100, stop=95, target=102)
        assert result["ratio"] == pytest.approx(0.4)
        assert result["favorable"] is False


def _make_close_frame(tickers_returns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(tickers_returns.values())))
    dates = pd.date_range("2026-01-01", periods=n + 1, freq="B")
    cols = {}
    for ticker, returns in tickers_returns.items():
        prices = [100.0]
        for r in returns:
            prices.append(prices[-1] * (1 + r))
        cols[("Close", ticker)] = prices
    df = pd.DataFrame(cols, index=dates)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class TestCorrelation:
    def test_requires_at_least_two_valid_tickers(self):
        assert "error" in rm.correlation(["NVDA"])
        assert "error" in rm.correlation([])

    def test_perfect_positive_and_negative_correlation(self, monkeypatch):
        rng = np.random.default_rng(42)
        a = rng.normal(0, 0.02, 60)
        frame = _make_close_frame({
            "AAA": a,
            "BBB": a * 2,   # mesmo sinal, escala diferente -> corr = 1
            "CCC": -a,      # espelhado -> corr = -1
        })
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: frame)

        result = rm.correlation(["AAA", "BBB", "CCC"], period="6mo")

        assert result["tickers"] == ["AAA", "BBB", "CCC"]
        pairs = {(p["a"], p["b"]): p["correlation"] for p in result["pairs"]}
        assert pairs[("AAA", "BBB")] == pytest.approx(1.0, abs=1e-6)
        assert pairs[("AAA", "CCC")] == pytest.approx(-1.0, abs=1e-6)
        assert pairs[("BBB", "CCC")] == pytest.approx(-1.0, abs=1e-6)
        # diagonal da matriz e' sempre 1 (correlacao de um ticker com ele mesmo)
        for i in range(3):
            assert result["matrix"][i][i] == pytest.approx(1.0)

    def test_high_correlation_pairs_use_absolute_value_threshold(self, monkeypatch):
        rng = np.random.default_rng(7)
        a = rng.normal(0, 0.02, 60)
        frame = _make_close_frame({"AAA": a, "BBB": -a})  # corr = -1, |corr| >= 0.8
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: frame)

        result = rm.correlation(["AAA", "BBB"])

        assert len(result["highCorrelationPairs"]) == 1
        assert result["highCorrelationPairs"][0]["correlation"] == pytest.approx(-1.0, abs=1e-6)

    def test_deduplicates_and_normalizes_ticker_case(self, monkeypatch):
        rng = np.random.default_rng(1)
        a = rng.normal(0, 0.02, 60)
        b = rng.normal(0, 0.02, 60)
        frame = _make_close_frame({"AAA": a, "BBB": b})
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: frame)

        result = rm.correlation(["aaa", "AAA", "bbb"])

        assert result["tickers"] == ["AAA", "BBB"]

    def test_reports_error_when_download_returns_insufficient_data(self, monkeypatch):
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: pd.DataFrame())
        result = rm.correlation(["AAA", "BBB"])
        assert "error" in result
