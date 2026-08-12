"""
Testes de risk_manager.py — position_size/risk_reward (matemática simples) e
correlation() (correlação de Pearson entre retornos, mockando yf.download pra
não depender de rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_risk_manager.py -v
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

# risk_manager.py faz `from security import sanitize_ticker` (import "flat",
# pensado pra quando o script roda standalone -- ver routes/risk.ts, que
# spawna pelo caminho direto do arquivo, e Python bota o diretório do script
# em sys.path[0] nesse caso). `from agent import ...` não replica isso:
# conftest.py só põe `src/` em sys.path, não `src/agent/`. Mesmo padrão de
# test_get_news_feed.py.
_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)
_spec = importlib.util.spec_from_file_location("risk_manager", os.path.join(_AGENT_DIR, "risk_manager.py"))
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)


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


class TestPortfolioRiskMetrics:
    def test_rejects_empty_or_zero_value_portfolio(self):
        assert "error" in rm.portfolio_risk_metrics([])
        assert "error" in rm.portfolio_risk_metrics([{"ticker": "AAA", "investedAmount": 0}])

    def test_computes_sharpe_max_drawdown_and_var_matching_closed_form(self, monkeypatch):
        rng = np.random.default_rng(42)
        n = 120
        ret_a = rng.normal(0.001, 0.02, n)
        ret_b = rng.normal(-0.0005, 0.03, n)
        frame = _make_close_frame({"AAA": ret_a, "BBB": ret_b})
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: frame)

        positions = [
            {"ticker": "AAA", "investedAmount": 700.0},
            {"ticker": "BBB", "investedAmount": 300.0},
        ]
        result = rm.portfolio_risk_metrics(positions, period="120d", risk_free_rate=0.045)

        # Recalcula com numpy puro (mesmos pesos por valor investido: 70/30)
        # a partir da mesma série sintética, pra validar a fórmula fechada
        # sem repetir a lógica interna da função.
        prices = frame["Close"]
        returns_a = prices["AAA"].pct_change().dropna()
        returns_b = prices["BBB"].pct_change().dropna()
        portfolio_ret = 0.7 * returns_a + 0.3 * returns_b
        mean_d, std_d = portfolio_ret.mean(), portfolio_ret.std()
        expected_sharpe = round(float((mean_d * 252 - 0.045) / (std_d * np.sqrt(252))), 3)
        equity = (1 + portfolio_ret).cumprod()
        expected_max_dd = round(float(((equity - equity.cummax()) / equity.cummax()).min()) * 100, 2)
        expected_var95 = round(float(np.percentile(portfolio_ret, 5)) * 100, 2)

        assert result["sharpeRatio"] == pytest.approx(expected_sharpe)
        assert result["maxDrawdownPct"] == pytest.approx(expected_max_dd)
        assert result["var95Pct"] == pytest.approx(expected_var95)
        assert result["totalInvested"] == pytest.approx(1000.0)
        assert result["tickers"] == ["AAA", "BBB"]
        # numpy.float64 vazando na resposta quebra json.dumps (não serializável)
        assert isinstance(result["sharpeRatio"], float)
        assert isinstance(result["maxDrawdownPct"], float)
        assert isinstance(result["var95Pct"], float)
        assert isinstance(result["annualizedVolatilityPct"], float)

    def test_skips_tickers_with_insufficient_history(self, monkeypatch):
        rng = np.random.default_rng(1)
        a = rng.normal(0, 0.02, 60)
        frame = _make_close_frame({"AAA": a})
        monkeypatch.setattr(rm.yf, "download", lambda *a_, **kw: frame)

        positions = [
            {"ticker": "AAA", "investedAmount": 500.0},
            {"ticker": "ZZZ", "investedAmount": 500.0},  # sem dado no mock -> skipped
        ]
        result = rm.portfolio_risk_metrics(positions, period="60d")
        assert result["tickers"] == ["AAA"]
        assert "ZZZ" in result["skipped"]

    def test_ignores_invalid_ticker_and_non_positive_invested_amount(self):
        positions = [
            {"ticker": "", "investedAmount": 500.0},
            {"ticker": "AAA", "investedAmount": -100.0},
        ]
        result = rm.portfolio_risk_metrics(positions)
        assert "error" in result
