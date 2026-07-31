"""
Testes dos gates de check_squeeze_setup (tools.py) adicionados depois de uma
análise externa apontar 2 falsos positivos reais:

- SMCI (30/jul/2026): short_pct e short_volume_ratio "perigosos", mas
  borrow_fee em 0,41%/ano (aluguel barato/disponível) -- sem aluguel caro/
  escasso não existe mecânica de squeeze, shorts não têm pressa de cobrir.
  Corrigido pelo gate borrow_fee_cheap (nunca deixa o nível chegar a "alto").
- HCC: DTC de 5,26 e short_volume_ratio de 83% em um papel com ADV baixo
  (~900k ações/dia) -- DTC alto é estrutural (ADV baixo infla o
  denominador), e short_volume_ratio alto em papel fino costuma ser
  internalização/hedge de market maker, não convicção direcional real.
  Corrigido pelo gate de iliquidez (threshold de DTC sobe, short_volume
  para de contar).
- ARM: volume 2,13x perto de uma mínima classificado como "volume de
  pânico no fundo" (bullish) num dia que na verdade fechou colado na
  mínima -- capitulação/distribuição, não acumulação. Corrigido exigindo
  fechamento na metade de cima do range do dia pra essa confirmação contar.

Tudo mockado (yf.Ticker, _fetch_borrow_fee, _fetch_short_volume_ratio) --
sem rede real, sem depender do cache em disco entre execuções.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_squeeze_setup_gates.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import cache as cache_module
from agent import tools


@pytest.fixture(autouse=True)
def _no_cache_no_network(monkeypatch):
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)
    monkeypatch.delenv("UNUSUAL_WHALES_API_KEY", raising=False)


def _dates(n, start="2025-01-02"):
    return pd.date_range(start, periods=n, freq="B")


def _fake_ticker_cls(info: dict, hist: pd.DataFrame):
    class FakeTicker:
        def __init__(self, ticker):
            self.info = info

        def history(self, period, auto_adjust):
            return hist

    return FakeTicker


def _flat_hist(n=120, price=40.0, volume=5_000_000.0):
    idx = _dates(n)
    closes = np.full(n, price)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
        "Close": closes, "Volume": volume,
    }, index=idx)


def _downtrend_with_final_day(n=120, base=300.0, drift=-0.01, final_close=None,
                               final_high=None, final_low=None, final_volume=None,
                               base_volume=1_000_000.0):
    """Série em queda constante (sempre faz mínima nova) até o penúltimo dia,
    com o último dia (hoje) customizável -- pra controlar candle/volume do
    dia que decide se 'volume no fundo' deveria disparar ou não."""
    idx = _dates(n)
    closes = [base]
    for _ in range(1, n - 1):
        closes.append(closes[-1] * (1 + drift))
    closes.append(final_close if final_close is not None else closes[-1] * (1 + drift))
    closes = np.array(closes)
    high = closes * 1.005
    low = closes * 0.995
    volume = np.full(n, base_volume)
    if final_high is not None:
        high[-1] = final_high
    if final_low is not None:
        low[-1] = final_low
    if final_volume is not None:
        volume[-1] = final_volume
    return pd.DataFrame({
        "Open": closes, "High": high, "Low": low, "Close": closes, "Volume": volume,
    }, index=idx)


class TestBorrowFeeCheapGate:
    def test_cheap_available_borrow_caps_risk_below_alto(self, monkeypatch):
        """SMCI-like: short_pct e short_volume 'perigosos', DTC alto, mas
        aluguel barato/disponível -- nunca deve chegar a 'alto'."""
        hist = _flat_hist(volume=8_000_000.0)  # líquido, não entra no gate de iliquidez
        info = {"shortPercentOfFloat": 0.25, "shortRatio": 6.0}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (0.41, "mock"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (60.0, "mock"))

        result = tools.check_squeeze_setup("SMCI")

        risk = result["squeeze_risk"]
        assert risk["n_dangerous"] >= 2  # short_pct, dtc e short_volume batem
        assert risk["borrow_fee_cheap"] is True
        assert risk["level"] != "alto"
        assert result["squeeze_setup_detected"] is False

    def test_expensive_scarce_borrow_still_reaches_alto(self, monkeypatch):
        """Regressão: mesmo cenário mas com aluguel caro -- deve continuar
        chegando a 'alto' normalmente (o gate não deve capar squeezes reais)."""
        hist = _flat_hist(volume=8_000_000.0)
        info = {"shortPercentOfFloat": 0.25, "shortRatio": 6.0}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (40.0, "mock"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (60.0, "mock"))

        result = tools.check_squeeze_setup("REAL")

        risk = result["squeeze_risk"]
        assert risk["borrow_fee_cheap"] is False
        assert risk["level"] == "alto"


class TestIlliquidityGate:
    def test_thin_stock_dtc_and_short_volume_do_not_count(self, monkeypatch):
        """HCC-like: DTC de 5.26 (acima do threshold líquido de 5, abaixo do
        ilíquido de 8) e short_volume_ratio de 83% num papel de ADV baixo --
        nenhum dos dois deveria contar como perigoso."""
        hist = _flat_hist(volume=900_000.0)  # ADV abaixo de 2M
        info = {"shortPercentOfFloat": 0.0833, "shortRatio": 5.26}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (None, "sem dado"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (83.14, "mock"))

        result = tools.check_squeeze_setup("HCC")

        risk = result["squeeze_risk"]
        assert risk["is_illiquid"] is True
        assert risk["days_to_cover_danger_threshold"] == tools._SQUEEZE_DTC_DANGER_ILLIQUID
        assert risk["dtc_dangerous"] is False
        assert risk["short_volume_dangerous"] is False
        assert risk["n_dangerous"] == 0
        assert risk["level"] == "baixo"

    def test_same_dtc_counts_as_dangerous_when_liquid(self, monkeypatch):
        """Regressão: o mesmo DTC de 5.26 deve continuar contando como
        perigoso quando o papel é líquido (ADV alto)."""
        hist = _flat_hist(volume=8_000_000.0)
        info = {"shortPercentOfFloat": 0.0833, "shortRatio": 5.26}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (None, "sem dado"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (83.14, "mock"))

        result = tools.check_squeeze_setup("LIQUID")

        risk = result["squeeze_risk"]
        assert risk["is_illiquid"] is False
        assert risk["days_to_cover_danger_threshold"] == tools._SQUEEZE_DTC_DANGER
        assert risk["dtc_dangerous"] is True
        assert risk["short_volume_dangerous"] is True


class TestVolumeAtBottomBuyingPressure:
    def test_capitulation_close_near_low_does_not_confirm(self, monkeypatch):
        """ARM-like: volume 2.13x perto de uma mínima de 50 dias, mas
        fechando colado na mínima do dia (capitulação) -- não deve contar
        como confirmação de reversão."""
        hist = _downtrend_with_final_day(
            final_volume=2_130_000.0,  # 2.13x a base_volume de 1M
            final_high=None,  # default 0.5% acima do close, será sobrescrito abaixo
        )
        # Preço fecha na mínima do dia (close == low), sem pavio de compra.
        final_close = float(hist["Close"].iloc[-1])
        hist.loc[hist.index[-1], "Low"] = final_close * 0.995
        hist.loc[hist.index[-1], "High"] = final_close * 1.05
        info = {"shortPercentOfFloat": None, "shortRatio": None}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (None, "sem dado"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (None, "sem dado"))

        result = tools.check_squeeze_setup("ARM")

        assert not any("volume" in c for c in result["reversal_confirmations"])

    def test_accumulation_close_near_high_confirms(self, monkeypatch):
        """Mesmo cenário de volume e proximidade da mínima, mas fechando na
        metade de cima do range do dia (comprador absorvendo a venda) --
        deve contar como confirmação."""
        hist = _downtrend_with_final_day(final_volume=2_130_000.0)
        final_close = float(hist["Close"].iloc[-1])
        hist.loc[hist.index[-1], "Low"] = final_close * 0.95
        hist.loc[hist.index[-1], "High"] = final_close * 1.005
        info = {"shortPercentOfFloat": None, "shortRatio": None}
        monkeypatch.setattr(tools.yf, "Ticker", _fake_ticker_cls(info, hist))
        monkeypatch.setattr(tools, "_fetch_borrow_fee", lambda t: (None, "sem dado"))
        monkeypatch.setattr(tools, "_fetch_short_volume_ratio", lambda t: (None, "sem dado"))

        result = tools.check_squeeze_setup("BOUNCE")

        assert any("volume" in c for c in result["reversal_confirmations"])
