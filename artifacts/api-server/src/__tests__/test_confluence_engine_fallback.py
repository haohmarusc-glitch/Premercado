"""
Testes da integração do confluence_engine._fetch_ohlcv com a cadeia.

Duas decisões fixadas aqui:

1. `18mo` passou a ser cacheável. Sem isso a integração seria inútil — as duas
   camadas de cache devolveriam vazio e, com série ajustada (fonte externa
   cortada), sobraria só o yfinance direto, exatamente como antes.
2. O caminho start/end NÃO usa a cadeia. Ela trabalha em período, e recortar
   uma janela arbitrária dela seria reimplementar o filtro aqui. Esse caminho
   é de investigação manual, não do ciclo automático.

O módulo usa imports planos, então é carregado por caminho — ver o cabeçalho
de test_entry_exit_study_fallback.py para o porquê de isso ser seguro desde
o #279.
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

from agent import hist_cache
from agent import market_data_provider as mdp

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_spec = importlib.util.spec_from_file_location(
    "confluence_engine", os.path.join(_AGENT_DIR, "confluence_engine.py")
)
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)


def _ohlcv(n=400):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    precos = 100.0 + np.linspace(0, 40, n)
    return pd.DataFrame({
        "Open": precos, "High": precos + 1, "Low": precos - 1,
        "Close": precos, "Volume": [1_000_000] * n,
    }, index=idx)


@pytest.fixture(autouse=True)
def _limpa():
    ce._ULTIMA_FONTE = None
    yield
    ce._ULTIMA_FONTE = None


def _mock(monkeypatch, source, ok=True):
    visto = {}

    def _falso(ticker, period, **kwargs):
        visto["ticker"] = ticker
        visto["period"] = period
        visto.update(kwargs)
        return mdp.HistoryResult(df=_ohlcv() if ok else None, source=source)

    monkeypatch.setattr(ce.market_data_provider, "get_daily_history", _falso)
    return visto


# ── 18mo precisa ser cacheável, senão a integração é inútil ─────────────────

def test_18mo_e_cacheavel():
    """É o período padrão do módulo. Fora do conjunto, a cadeia não teria
    nenhuma camada de cache pra servir numa queda do Yahoo — e com a fonte
    externa cortada (série ajustada), integrar não mudaria nada."""
    assert hist_cache.cacheavel("18mo") is True


def test_18mo_nao_quebrou_os_outros_periodos():
    for p in ("3mo", "6mo", "1y", "2y", "5y", "10y", "max"):
        assert hist_cache.cacheavel(p) is True
    for p in ("5d", "1mo", "2mo"):
        assert hist_cache.cacheavel(p) is False


def test_intradiario_continua_fora_mesmo_com_periodo_longo():
    assert hist_cache.cacheavel("18mo", interval="5m") is False


# ── o caminho por período usa a cadeia ──────────────────────────────────────

def test_periodo_usa_a_cadeia_com_serie_ajustada_e_sem_externa(monkeypatch):
    visto = _mock(monkeypatch, "yfinance")
    df, erro = ce._fetch_ohlcv("NVDA", period="18mo")
    assert erro is None
    assert df is not None
    assert visto["period"] == "18mo"
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False


def test_sem_dado_devolve_erro_do_contrato(monkeypatch):
    """A mensagem é a mesma de antes — quem consome não precisa mudar."""
    _mock(monkeypatch, "none", ok=False)
    df, erro = ce._fetch_ohlcv("NVDA", period="18mo")
    assert df is None
    assert erro == "Sem dados para o período"


def test_cache_vencido_serve(monkeypatch):
    """O ganho da integração: com o Yahoo fora, o sinal ainda sai."""
    _mock(monkeypatch, "cache_stale")
    df, erro = ce._fetch_ohlcv("NVDA", period="18mo")
    assert erro is None
    assert len(df) > 60


# ── start/end fica fora da cadeia, de propósito ─────────────────────────────

def test_start_end_nao_usa_a_cadeia(monkeypatch):
    """Caminho de investigação manual (backtest de um regime específico). A
    cadeia trabalha em período; recortar janela arbitrária dela seria
    reimplementar o filtro aqui."""
    monkeypatch.setattr(
        ce.market_data_provider, "get_daily_history",
        lambda *a, **k: pytest.fail("start/end não deveria usar a cadeia"),
    )

    class _Tk:
        def __init__(self, *a, **k): pass
        def history(self, **k): return _ohlcv()

    monkeypatch.setattr(ce, "yf", type("m", (), {"Ticker": _Tk}), raising=False)
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _Tk)

    df, erro = ce._fetch_ohlcv("NVDA", start="2025-01-01", end="2025-06-01")
    assert erro is None
    assert df is not None


# ── marcação da fonte ───────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["yfinance", "yfinance_cache"])
def test_fonte_normal_nao_marca(monkeypatch, source):
    _mock(monkeypatch, source)
    ce._fetch_ohlcv("NVDA", period="18mo")
    assert ce._ULTIMA_FONTE == source


def test_fonte_degradada_fica_registrada(monkeypatch):
    _mock(monkeypatch, "cache_stale")
    ce._fetch_ohlcv("NVDA", period="18mo")
    assert ce._ULTIMA_FONTE == "cache_stale"


def test_start_end_nao_deixa_fonte_pendurada(monkeypatch):
    """Sem o reset, uma avaliação por start/end herdaria a fonte da chamada
    anterior e marcaria degradação que não aconteceu."""
    _mock(monkeypatch, "cache_stale")
    ce._fetch_ohlcv("NVDA", period="18mo")
    assert ce._ULTIMA_FONTE == "cache_stale"

    class _Tk:
        def __init__(self, *a, **k): pass
        def history(self, **k): return _ohlcv()

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _Tk)
    ce._fetch_ohlcv("NVDA", start="2025-01-01", end="2025-06-01")
    assert ce._ULTIMA_FONTE is None
