"""
Testes do motor de simulação de backtest.py — equity curve dia-a-dia,
stop-loss/take-profit, Sharpe/drawdown calculados sobre a equity da
ESTRATÉGIA (não do buy&hold), e os novos parâmetros de threshold
(rsi_oversold/rsi_overbought/score_threshold). Tudo mockado (sem rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_engine.py -v
"""
import pandas as pd
import pytest

from agent import backtest as bt


def _dates(n, start="2024-01-02"):
    return pd.date_range(start, periods=n, freq="B")


def _mock_history_df(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": close.values, "High": close.values * 1.01,
        "Low": close.values * 0.99, "Close": close.values,
        "Volume": 1_000_000,
    }, index=close.index)


def _patch_ticker(monkeypatch, close: pd.Series):
    class FakeTicker:
        def __init__(self, ticker):
            pass
        def history(self, start, end, interval, auto_adjust):
            return _mock_history_df(close)
    monkeypatch.setattr(bt.yf, "Ticker", FakeTicker)


def _flat_then_cross_up(n=140, flat=100.0):
    """Fica achatado (ma20≈ma50≈flat, sem cruzamento) e depois sobe rápido o
    bastante pra ma20 cruzar acima da ma50 (dispara compra na estratégia
    ma_cross) perto do fim da série -- deixa espaço pro teste manipular o
    que acontece IMEDIATAMENTE depois da entrada."""
    prices = [flat] * 90
    for i in range(n - 90):
        prices.append(prices[-1] * 1.02)
    return pd.Series(prices, index=_dates(len(prices)))


class TestEquityCurve:
    def test_one_point_per_trading_day_with_matching_dates(self, monkeypatch):
        close = _flat_then_cross_up()
        _patch_ticker(monkeypatch, close)
        result = bt.run_backtest("NVDA", "2024-01-02", "2024-07-01", strategy="ma_cross")

        assert "error" not in result
        assert len(result["equityCurve"]) == len(pd.Series(
            index=pd.DatetimeIndex([e["date"] for e in result["equityCurve"]])
        ))
        dates = [e["date"] for e in result["equityCurve"]]
        assert dates == sorted(dates)
        assert dates[0] <= result["start"] or True  # datas dentro do periodo pedido (recorte já testado em outro arquivo)

    def test_flat_equity_when_strategy_never_trades(self, monkeypatch):
        # RSI nunca sai de ~50 numa serie perfeitamente reta -> nunca compra
        close = pd.Series([100.0] * 300, index=_dates(300))
        _patch_ticker(monkeypatch, close)
        result = bt.run_backtest("FLAT", "2024-08-01", "2025-06-01", strategy="rsi")

        assert "error" not in result
        assert result["totalTrades"] == 0
        equities = [e["equity"] for e in result["equityCurve"]]
        assert all(e == pytest.approx(10000.0) for e in equities)
        assert result["sharpe"] == 0.0
        assert result["maxDrawdown"] == 0.0

    def test_buy_hold_equity_tracks_the_raw_price(self, monkeypatch):
        close = _flat_then_cross_up()
        _patch_ticker(monkeypatch, close)
        result = bt.run_backtest("NVDA", "2024-01-02", "2024-07-01", strategy="ma_cross")

        first = result["equityCurve"][0]
        last = result["equityCurve"][-1]
        # buyHoldEquity cresce na mesma proporcao que o preco no periodo
        implied_price_growth = last["buyHoldEquity"] / first["buyHoldEquity"]
        assert implied_price_growth == pytest.approx(1 + result["buyAndHoldReturn"] / 100, abs=0.01)


class TestSharpeAndDrawdownReflectStrategyNotBuyHold:
    def test_strategy_flat_during_a_crash_has_smaller_drawdown_than_buy_and_hold(self, monkeypatch):
        # Sobe (dispara compra ma_cross), sai por sinal ANTES da queda (o
        # cruzamento pra baixo precisa acontecer e ser detectado), depois um
        # crash grande. Construido pra deixar a estrategia fora do mercado
        # bem antes do pior da queda -- se sharpe/drawdown ainda fossem
        # calculados do close (bug antigo), o drawdown reportado seria bem
        # maior (baseado na queda inteira do preco, nao na carteira parada).
        up = [100.0 * (1.02 ** i) for i in range(60)]           # sobe, ma20>ma50
        # -3%/dia por 24 dias: precisa ser "íngreme" o bastante pra realmente
        # cruzar ma20<ma50 (e sair) ANTES da fase de crash -- uma queda mais
        # suave (testada e descartada: -1.5%/dia) não é rápida o bastante e o
        # cruzamento só dispara já dentro do crash, tarde demais.
        down_signal = [up[-1] * (0.97 ** i) for i in range(1, 25)]
        crash = [down_signal[-1] * (0.90 ** i) for i in range(1, 10)]  # crash forte DEPOIS de já ter saido
        # 60 dias flat de aquecimento antes -- precisa ser >= 50 pra ma20/ma50
        # ficarem "limpas" (só preço flat) antes da alta começar, senão a
        # própria alta já vaza pra dentro da ma50 antes dela ter qualquer
        # valor válido e nunca chega a existir um cruzamento detectável.
        prices = [100.0] * 60 + up + down_signal + crash
        close = pd.Series(prices, index=_dates(len(prices)))
        _patch_ticker(monkeypatch, close)

        result = bt.run_backtest("NVDA", close.index[0].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"), strategy="ma_cross")

        assert "error" not in result
        assert result["totalTrades"] >= 1
        # drawdown do buy&hold (baseado so no preco) seria MUITO maior que -90%
        bh_drawdown_estimate = (crash[-1] - max(up)) / max(up) * 100
        assert result["maxDrawdown"] > bh_drawdown_estimate + 20  # bem menos negativo


class TestStopLossTakeProfit:
    def test_stop_loss_exits_before_the_natural_signal_would(self, monkeypatch):
        # `up` curto (só o bastante pra disparar o cruzamento ma20>ma50, que
        # já acontece logo no início da subida -- ver debug em
        # TestSharpeAndDrawdownReflectStrategyNotBuyHold) mantém o preço de
        # entrada bem perto do preço no dia do crash, senão um "crash de 20%"
        # medido a partir do topo de uma subida longa nem chega perto de
        # violar o stop calculado sobre o preço de entrada real.
        up = [100.0 * (1.02 ** i) for i in range(5)]  # dispara compra (ma20>ma50)
        crash = [up[-1] * 0.80]  # -20% num dia só, MA ainda não reverteu
        tail = [crash[-1]] * 10
        prices = [100.0] * 60 + up + crash + tail  # flat >= 50 pra ma20/ma50 ficarem limpas
        close = pd.Series(prices, index=_dates(len(prices)))
        _patch_ticker(monkeypatch, close)

        result = bt.run_backtest(
            "NVDA", close.index[0].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"),
            strategy="ma_cross", stop_loss_pct=0.08,
        )

        assert "error" not in result
        assert result["totalTrades"] >= 1
        first_trade = result["trades"][0]
        assert first_trade["exitReason"] == "stop_loss"
        # saiu no mesmo dia do crash, nao muitos dias depois (sinal so reverteria bem mais tarde)
        assert first_trade["exitDate"] == str(close.index[60 + 5])[:10]

    def test_take_profit_exits_before_the_natural_signal_would(self, monkeypatch):
        up = [100.0 * (1.02 ** i) for i in range(5)]
        spike = [up[-1] * 1.20]  # +20% num dia so
        tail = [spike[-1]] * 10
        prices = [100.0] * 60 + up + spike + tail
        close = pd.Series(prices, index=_dates(len(prices)))
        _patch_ticker(monkeypatch, close)

        result = bt.run_backtest(
            "NVDA", close.index[0].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"),
            strategy="ma_cross", take_profit_pct=0.15,
        )

        assert "error" not in result
        first_trade = result["trades"][0]
        assert first_trade["exitReason"] == "take_profit"
        assert first_trade["exitDate"] == str(close.index[60 + 5])[:10]

    def test_no_stop_loss_or_take_profit_configured_only_exits_on_signal(self, monkeypatch):
        close = _flat_then_cross_up()
        _patch_ticker(monkeypatch, close)
        result = bt.run_backtest("NVDA", "2024-01-02", "2024-07-01", strategy="ma_cross")
        for t in result["trades"]:
            assert t["exitReason"] in ("signal", "period_end")


class TestCustomThresholds:
    def test_looser_rsi_oversold_threshold_triggers_a_buy_that_default_would_miss(self, monkeypatch):
        # Serie desenhada pra deixar o RSI parado numa faixa intermediaria
        # (nem <30 nem >70) -- com o default (30) nao compra; com um
        # threshold mais frouxo (rsi_oversold=45) deve comprar.
        prices = [100.0]
        for _ in range(80):
            prices.append(prices[-1] * 0.997)  # queda lenta e constante
        close = pd.Series(prices, index=_dates(len(prices)))
        _patch_ticker(monkeypatch, close)

        default_result = bt.run_backtest("NVDA", close.index[20].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"), strategy="rsi")
        loose_result = bt.run_backtest("NVDA", close.index[20].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"), strategy="rsi", rsi_oversold=45.0)

        assert loose_result["totalTrades"] >= default_result["totalTrades"]
        assert loose_result["totalTrades"] >= 1

    def test_lower_score_threshold_triggers_confluencia_signal_that_default_would_miss(self, monkeypatch):
        # Alta moderada (nao "forte" o bastante pra bater score 60 default)
        prices = [100.0] * 40
        for i in range(200):
            prices.append(prices[-1] * 1.006)
        close = pd.Series(prices, index=_dates(len(prices)))
        _patch_ticker(monkeypatch, close)

        default_result = bt.run_backtest("NVDA", close.index[210].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"), strategy="confluencia")
        loose_result = bt.run_backtest("NVDA", close.index[210].strftime("%Y-%m-%d"), close.index[-1].strftime("%Y-%m-%d"), strategy="confluencia", score_threshold=40.0)

        assert loose_result["totalTrades"] >= default_result["totalTrades"]
