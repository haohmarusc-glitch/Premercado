"""
Testes de duas correções em tools.get_technical_indicators, ambas nascidas do
mesmo snapshot de NBIS (17/08/2026), três pregões depois do balanço de 12/08:

1. RSI divergente entre painéis. A tela mostrava "RSI 64,6" no painel
   Tendência e "RSI 67,2" no painel Técnica, mesmo ticker, mesmo instante --
   porque get_trend usa Wilder (ewm alpha=1/14) e este aqui usava
   `rolling(14).mean()`, que é a variante de Cutler: um indicador DIFERENTE,
   não um arredondamento. Agora os dois calculam Wilder, e o teste amarra um
   ao outro (mesmo padrão de test_backtest_confluencia.py, que já compara
   backtest._rsi_wilder_series com get_trend.rsi_wilder).

2. RVOL deprimido por um mês após qualquer earnings. A base de comparação era
   a MÉDIA de volume de 20 pregões; um dia de balanço negocia 2-3x o normal e
   inflava esse denominador por 20 pregões seguidos, justo no período mais
   ativo do papel. NBIS fechou +8,88% em 14/08 com rvol 1,01 ("normal").
   Trocado por MEDIANA, que ignora o outlier sem precisar detectá-lo.

Tudo com DataFrame sintético e yf.Ticker mockado -- sem rede, mesma linha dos
outros testes de tools.py.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_technicals_rsi_rvol.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import cache as cache_module
from agent import get_trend
from agent import tools


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    # Sem isto o segundo teste leria o resultado do primeiro (@cached em disco).
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)


def _hist(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-02", periods=n, freq="B")
    close = pd.Series(closes, index=idx, dtype=float)
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {
            "Open": close,
            # High/Low precisam existir pro ATR; range simétrico de 1%.
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": pd.Series(volumes, index=idx, dtype=float),
        },
        index=idx,
    )


def _mock_yf(monkeypatch, hist: pd.DataFrame):
    """yf.Ticker que devolve `hist` no diário e nada no intradiário (5m) --
    intradiário vazio é o caminho de mercado fechado, deixa rvol/vwap None e
    não interfere nos indicadores diários."""
    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, *args, **kwargs):
            if kwargs.get("interval") == "5m":
                return pd.DataFrame()
            return hist

    monkeypatch.setattr(tools.yf, "Ticker", FakeTicker)


# ── RSI: as duas telas têm que devolver o MESMO número ───────────────────────

def _serie_com_altos_e_baixos(n: int = 120) -> list[float]:
    """Série determinística com alternância de alta/baixa -- precisa ter
    quedas de verdade, senão avg_loss=0 e o RSI satura em 100 nos dois
    cálculos, o que faria o teste passar sem comparar nada."""
    rng = np.random.default_rng(20260817)
    passos = rng.normal(loc=0.4, scale=3.0, size=n)
    precos, p = [], 100.0
    for passo in passos:
        p = max(5.0, p + passo)
        precos.append(round(p, 2))
    return precos


def test_rsi_do_painel_tecnica_bate_com_o_do_painel_tendencia(monkeypatch):
    closes = _serie_com_altos_e_baixos()
    hist = _hist(closes)
    _mock_yf(monkeypatch, hist)

    out = tools.get_technical_indicators("NBIS", period="6mo")
    assert "error" not in out, out

    esperado = get_trend.rsi_wilder(hist["Close"])
    assert esperado is not None
    # Mesmo cálculo dos dois lados -- tolerância só de arredondamento (ambos
    # fazem round(...,2) sobre o mesmo float).
    assert out["rsi_14"] == pytest.approx(esperado, abs=0.01)


def test_rsi_wilder_difere_de_cutler_o_bastante_pra_o_teste_acima_ter_valor(monkeypatch):
    """Guarda-corpo do teste anterior: se Wilder e a média simples dessem
    sempre o mesmo número, aquela asserção não provaria nada. Aqui a
    diferença entre as duas contas é medida explicitamente."""
    closes = _serie_com_altos_e_baixos()
    close = pd.Series(closes, dtype=float)
    delta = close.diff()

    wilder_gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean().iloc[-1]
    wilder_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean().iloc[-1]
    wilder = 100 - 100 / (1 + wilder_gain / wilder_loss)

    cutler_gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    cutler_loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    cutler = 100 - 100 / (1 + cutler_gain / cutler_loss)

    assert abs(wilder - cutler) > 1.0


# ── RVOL: um dia de earnings não pode derrubar a base por 20 pregões ─────────

def test_base_de_volume_ignora_o_pico_de_um_unico_pregao(monkeypatch):
    """Reprodução do caso NBIS: 19 pregões em ~24M e um dia de balanço em
    63,5M. Pela média o denominador sobe ~10%; pela mediana, nada."""
    closes = _serie_com_altos_e_baixos()
    volumes = [24_000_000.0] * len(closes)
    volumes[-3] = 63_500_000.0  # o pregão do balanço, dentro da janela de 20

    hist = _hist(closes, volumes)
    _mock_yf(monkeypatch, hist)
    out = tools.get_technical_indicators("NBIS", period="6mo")
    assert "error" not in out, out

    base_media = float(pd.Series(volumes[-20:]).mean())
    base_mediana = float(pd.Series(volumes[-20:]).median())
    assert base_mediana == pytest.approx(24_000_000.0)
    assert base_media > base_mediana * 1.05  # o pico realmente distorce a média

    # volume_ratio_5d_vs_20d usa a mesma base; com a mediana ele reflete os
    # 5 dias recentes contra o volume TÍPICO, não contra a média inflada.
    esperado = round(float(pd.Series(volumes[-5:]).mean()) / base_mediana, 2)
    assert out["volume_ratio_5d_vs_20d"] == pytest.approx(esperado, abs=0.01)


def test_volume_ratio_normal_quando_nao_ha_pico(monkeypatch):
    """Sem outlier, mediana e média coincidem -- a troca não muda o caso comum."""
    closes = _serie_com_altos_e_baixos()
    hist = _hist(closes, [30_000_000.0] * len(closes))
    _mock_yf(monkeypatch, hist)

    out = tools.get_technical_indicators("NBIS", period="6mo")
    assert out["volume_ratio_5d_vs_20d"] == pytest.approx(1.0, abs=0.01)
