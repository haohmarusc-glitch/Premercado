"""
Testes da estratégia "confluencia" em backtest.py — verifica que
_price_structure_at/_rsi_wilder_series batem exatamente com o mesmo cálculo
em get_trend.py (a estratégia é uma reimplementação da fórmula de sinal
técnico já usada em produção, sem a camada de notícias), que o backtest
gera sinal em séries sintéticas com tendência clara, e que o runner de
cesta agrega corretamente. Tudo mockado (sem rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_confluencia.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import backtest as bt
from agent import get_trend


def _dates(n, start="2024-01-02"):
    return pd.date_range(start, periods=n, freq="B")


def _sawtooth_uptrend(n=300, base=100.0, step_up=0.03, pullback=0.012, period=10):
    """Sobe em dentes-de-serra: `period-1` dias de alta seguidos de 1 dia de
    correção -- gera topos e fundos ascendentes claros (estrutura 'alta')
    mantendo MAs/MACD também inclinados pra cima."""
    prices = [base]
    for i in range(1, n):
        if i % period == 0:
            prices.append(prices[-1] * (1 - pullback))
        else:
            prices.append(prices[-1] * (1 + step_up))
    return pd.Series(prices, index=_dates(n))


def _sawtooth_downtrend(n=300, base=200.0, drift=-0.012, vol=0.018, seed=1):
    """Queda com ruído diário (drift negativo + volatilidade) -- ao contrário
    de um decaimento percentual perfeitamente suave, isso produz um MACD
    genuinamente bearish na maior parte do tempo (um decaimento geométrico
    "liso" tende a estabilizar o histograma do MACD perto de zero/positivo em
    regime permanente, já que o indicador mede aceleração da tendência, não
    só a direção -- ruído real de mercado evita esse artefato)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    prices = [base]
    for r in rets[1:]:
        prices.append(prices[-1] * (1 + r))
    return pd.Series(prices, index=_dates(n))


def _flat_choppy(n=300, base=100.0, seed=3):
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.004, n)
    prices = [base]
    for r in noise[1:]:
        prices.append(prices[-1] * (1 + r))
    return pd.Series(prices, index=_dates(n))


class TestEquivalenceWithGetTrend:
    def test_price_structure_matches_get_trend_uptrend(self):
        s = _sawtooth_uptrend(120)
        assert bt._price_structure_at(s) == get_trend.price_structure(s)

    def test_price_structure_matches_get_trend_downtrend(self):
        s = _sawtooth_downtrend(120)
        assert bt._price_structure_at(s) == get_trend.price_structure(s)

    def test_price_structure_matches_get_trend_choppy(self):
        s = _flat_choppy(120)
        assert bt._price_structure_at(s) == get_trend.price_structure(s)

    def test_rsi_wilder_series_last_value_matches_get_trend_scalar(self):
        for s in (_sawtooth_uptrend(80), _sawtooth_downtrend(80), _flat_choppy(80)):
            series_val = bt._rsi_wilder_series(s).iloc[-1]
            scalar_val = get_trend.rsi_wilder(s)
            assert series_val == pytest.approx(scalar_val, abs=0.01)


class TestConfluenceSignals:
    # Sem teste de "nenhum sinal em mercado choppy": SMA20/50, MACD e a
    # estrutura de 60 pregões são indicadores de swing de médio prazo -- eles
    # legitimamente disparam em oscilações dentro de um range mais amplo
    # (é o próprio objetivo de um sinal de swing trade). Um teste assim
    # estaria testando uma premissa errada sobre como a fórmula real funciona.

    def test_buy_signal_on_strong_sustained_uptrend(self):
        close = _sawtooth_uptrend(300)
        buy_signal, sell_signal = bt._confluence_signals(close)
        # Depois de aquecido (SMA200 valida), uma alta sustentada forte deve
        # bater o score >= 60 em algum ponto.
        assert buy_signal.iloc[210:].any()

    def test_sell_signal_on_strong_sustained_downtrend(self):
        close = _sawtooth_downtrend(300)
        buy_signal, sell_signal = bt._confluence_signals(close)
        assert sell_signal.iloc[210:].any()

    def test_no_score_before_warmup(self):
        close = _sawtooth_uptrend(300)
        buy_signal, sell_signal = bt._confluence_signals(close)
        assert not buy_signal.iloc[:60].any()
        assert not sell_signal.iloc[:60].any()


def _mock_history_df(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": close.values, "High": close.values * 1.01,
        "Low": close.values * 0.99, "Close": close.values,
        "Volume": 1_000_000,
    }, index=close.index)


class TestRunBacktestConfluencia:
    def test_fetches_warmup_buffer_before_requested_start(self, monkeypatch):
        close = _sawtooth_uptrend(400)
        captured = {}

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                captured["start"] = start
                captured["end"] = end
                return _mock_history_df(close)

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_backtest("NVDA", "2024-11-01", "2025-01-15", strategy="confluencia")

        assert "error" not in result
        # o start pedido pro yfinance deve ser MUITO anterior ao start pedido
        # pelo usuario (aquecimento de ~320 dias corridos)
        assert pd.Timestamp(captured["start"]) < pd.Timestamp("2024-11-01") - pd.Timedelta(days=300)
        assert captured["end"] == "2025-01-15"
        # o periodo reportado e' o pedido pelo usuario, nao o periodo com aquecimento
        assert result["start"] == "2024-11-01"
        assert result["end"] == "2025-01-15"

    def test_produces_trades_on_a_clean_uptrend(self, monkeypatch):
        close = _sawtooth_uptrend(400)

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(close)

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_backtest("NVDA", "2024-08-01", "2025-06-01", strategy="confluencia")

        assert "error" not in result
        assert result["strategy"] == "confluencia"
        assert result["totalTrades"] >= 1

    def test_reports_error_when_requested_window_has_too_little_data(self, monkeypatch):
        close = _sawtooth_uptrend(400)

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                # Simula o yfinance so devolvendo dados ate um certo ponto
                # (ticker recem-listado / fim do periodo pedido sem dados)
                return _mock_history_df(close.iloc[:65])

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_backtest("NEWCO", "2024-08-01", "2025-06-01", strategy="confluencia")
        assert "error" in result


class TestRunBasketBacktest:
    def test_aggregates_across_multiple_tickers(self, monkeypatch):
        up = _sawtooth_uptrend(400)
        down = _sawtooth_downtrend(400)

        # Mocka yf.Ticker despachando a série certa por nome de ticker, assim
        # run_basket_backtest chama a run_backtest real (sem reimplementar a
        # simulação de trades aqui).
        class DispatchingFakeTicker:
            def __init__(self, ticker):
                self._ticker = ticker
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(up if self._ticker == "UP" else down)

        monkeypatch.setattr(bt.yf, "Ticker", DispatchingFakeTicker)

        result = bt.run_basket_backtest(["UP", "DOWN"], "2024-08-01", "2025-06-01", strategy="confluencia")

        assert result["tickersRequested"] == 2
        assert result["tickersOk"] == 2
        assert "aggregate" in result
        assert len(result["results"]) == 2
        # ranqueado por totalReturn desc
        returns = [r["totalReturn"] for r in result["results"]]
        assert returns == sorted(returns, reverse=True)

    def test_isolates_failure_of_one_ticker_from_the_rest(self, monkeypatch):
        up = _sawtooth_uptrend(400)

        class DispatchingFakeTicker:
            def __init__(self, ticker):
                self._ticker = ticker
            def history(self, start, end, interval, auto_adjust):
                if self._ticker == "BAD":
                    return pd.DataFrame()
                return _mock_history_df(up)

        monkeypatch.setattr(bt.yf, "Ticker", DispatchingFakeTicker)

        result = bt.run_basket_backtest(["UP", "BAD"], "2024-08-01", "2025-06-01", strategy="confluencia")

        assert result["tickersOk"] == 1
        assert len(result["failed"]) == 1
        assert result["failed"][0]["ticker"] == "BAD"

    def test_groups_results_by_sector(self, monkeypatch):
        up = _sawtooth_uptrend(400)
        down = _sawtooth_downtrend(400)

        # MU e WDC -> setor "memory"; TSLA nao esta em nenhum grupo -> "other"
        class DispatchingFakeTicker:
            def __init__(self, ticker):
                self._ticker = ticker
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(up if self._ticker in ("MU", "WDC") else down)

        monkeypatch.setattr(bt.yf, "Ticker", DispatchingFakeTicker)

        result = bt.run_basket_backtest(["MU", "WDC", "TSLA"], "2024-08-01", "2025-06-01", strategy="confluencia")

        by_sector = {s["sector"]: s for s in result["bySector"]}
        assert set(by_sector.keys()) == {"memory", "other"}
        assert by_sector["memory"]["tickerCount"] == 2
        assert by_sector["memory"]["label"] == "Memória"
        assert by_sector["other"]["tickerCount"] == 1
        assert by_sector["other"]["label"] == "Outros"


class TestSensitivityAnalysis:
    def test_fetches_data_only_once_regardless_of_variation_count(self, monkeypatch):
        close = _sawtooth_uptrend(400)
        fetch_count = {"n": 0}

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                fetch_count["n"] += 1
                return _mock_history_df(close)

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_sensitivity_analysis("NVDA", "2024-08-01", "2025-06-01", strategy="rsi")

        assert "error" not in result
        assert fetch_count["n"] == 1

    def test_rsi_strategy_varies_rsi_thresholds_and_sl_tp(self, monkeypatch):
        close = _sawtooth_uptrend(400)

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(close)

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_sensitivity_analysis("NVDA", "2024-08-01", "2025-06-01", strategy="rsi")

        params_tested = {v["param"] for v in result["variations"]}
        assert params_tested == {"rsiOversold", "rsiOverbought", "stopLossPct", "takeProfitPct"}
        assert "scoreThreshold" not in params_tested
        assert "totalReturn" in result["baseline"]
        for v in result["variations"]:
            assert "totalReturn" in v
            assert "param" in v and "value" in v

    def test_confluencia_strategy_varies_score_threshold_instead_of_rsi(self, monkeypatch):
        close = _sawtooth_uptrend(400)

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(close)

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_sensitivity_analysis("NVDA", "2024-08-01", "2025-06-01", strategy="confluencia")

        params_tested = {v["param"] for v in result["variations"]}
        assert "scoreThreshold" in params_tested
        assert "rsiOversold" not in params_tested
        assert "rsiOverbought" not in params_tested

    def test_reports_error_when_fetch_fails_entirely(self, monkeypatch):
        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                return pd.DataFrame()

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_sensitivity_analysis("NEWCO", "2024-08-01", "2025-06-01", strategy="rsi")
        assert "error" in result

    def test_baseline_reports_error_when_requested_window_has_too_little_data(self, monkeypatch):
        # 65 dias passam no minimo do fetch (50), mas o recorte pro periodo
        # pedido fica abaixo do minimo de 20 dias que _simulate exige -- o
        # erro aparece dentro de cada run (baseline/variations), nao no topo.
        close = _sawtooth_uptrend(400)

        class FakeTicker:
            def __init__(self, ticker):
                pass
            def history(self, start, end, interval, auto_adjust):
                return _mock_history_df(close.iloc[:65])

        monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)
        result = bt.run_sensitivity_analysis("NEWCO", "2024-08-01", "2025-06-01", strategy="rsi")
        assert "error" not in result
        assert "error" in result["baseline"]
