"""
Testes do lote de fechamentos no provider + integração do risk_manager.

O que há de novo aqui é o formato: correlation e portfolio_risk_metrics
consomem VÁRIOS tickers numa chamada (yf.download em lote), e a cadeia só
falava um ticker por vez. get_daily_closes_batch mantém o lote no caminho
feliz (uma chamada de rede, como antes) e desce a cadeia POR TICKER quando o
lote falha — com `fontes` dizendo de onde veio cada coluna.

Import de PACOTE para o provider; o risk_manager tem imports planos e é
carregado por caminho (ver test_entry_exit_study_fallback.py).
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

from agent import hist_cache
from agent import market_data_provider as mdp
from agent import provider_health

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_spec = importlib.util.spec_from_file_location(
    "risk_manager", os.path.join(_AGENT_DIR, "risk_manager.py")
)
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)


@pytest.fixture(autouse=True)
def _isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_health, "_PATH", str(tmp_path / "health.json"))
    monkeypatch.setattr(hist_cache, "guardar", lambda *a, **k: None)
    monkeypatch.setattr(hist_cache, "carregar", lambda *a, **k: None)
    yield


def _download_falso(tickers, n=120):
    """Simula o retorno MultiIndex (campo × ticker) do yf.download em lote."""
    idx = pd.date_range("2026-02-01", periods=n, freq="D")
    rng = np.random.default_rng(7)
    frames = {}
    for i, t in enumerate(tickers):
        base = 100.0 * (i + 1) + np.cumsum(rng.normal(0, 1, n))
        frames[t] = pd.DataFrame({
            "Open": base, "High": base + 1, "Low": base - 1,
            "Close": base, "Volume": np.full(n, 1_000_000),
        }, index=idx)
    wide = pd.concat(frames, axis=1)          # (ticker, campo)
    return wide.swaplevel(axis=1)             # (campo, ticker) como o yf.download


def _serie_individual(n=120, base=50.0):
    idx = pd.date_range("2026-02-01", periods=n, freq="D")
    precos = base + np.linspace(0, 5, n)
    return pd.DataFrame({
        "Open": precos, "High": precos + 1, "Low": precos - 1,
        "Close": precos, "Volume": [1_000] * n,
    }, index=idx)


# ── caminho feliz do lote ───────────────────────────────────────────────────

def test_lote_feliz_faz_uma_chamada_so(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        mdp.yf, "download",
        lambda ts, **k: chamadas.append(list(ts)) or _download_falso(ts),
    )
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda *a, **k: pytest.fail("lote saudável não deveria descer à cadeia por ticker"),
    )

    r = mdp.get_daily_closes_batch(["NVDA", "MU", "STX"], "1y", auto_adjust=True)

    assert r.ok
    assert chamadas == [["NVDA", "MU", "STX"]]
    assert sorted(r.closes.columns) == ["MU", "NVDA", "STX"]
    assert r.fontes == {"NVDA": "yfinance", "MU": "yfinance", "STX": "yfinance"}
    assert r.degradadas == {}


def test_lote_feliz_alimenta_o_cache_por_ticker(monkeypatch):
    """O lote de hoje é o fallback de amanhã: cada recorte OHLCV completo vai
    para o hist_cache com a chave normal."""
    gravados = []
    monkeypatch.setattr(mdp.yf, "download", lambda ts, **k: _download_falso(ts))
    monkeypatch.setattr(
        hist_cache, "guardar",
        lambda t, p, df, **k: gravados.append((t, p, sorted(df.columns))),
    )

    mdp.get_daily_closes_batch(["NVDA", "MU"], "1y", auto_adjust=True)

    assert sorted(t for t, _, _ in gravados) == ["MU", "NVDA"]
    # Frame COMPLETO, nunca parcial: o cache é compartilhado com
    # get_technicals/get_trend, que esperam OHLCV inteiro.
    for _, _, cols in gravados:
        assert cols == ["Close", "High", "Low", "Open", "Volume"]


def test_recorte_parcial_nao_e_gravado():
    """Melhor não gravar do que gravar um frame com metade das colunas —
    corrupção silenciosa no cache compartilhado."""
    df = pd.DataFrame({"Close": [1.0, 2.0]})
    assert mdp._extrair_ohlcv_por_ticker(df, "NVDA") is None


def test_lote_vazio_nao_quebra():
    r = mdp.get_daily_closes_batch([], "1y")
    assert not r.ok


def test_lote_dedup_de_tickers(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        mdp.yf, "download",
        lambda ts, **k: chamadas.append(list(ts)) or _download_falso(ts),
    )
    mdp.get_daily_closes_batch(["NVDA", "NVDA", "MU"], "1y")
    assert chamadas == [["NVDA", "MU"]]


# ── fallback por ticker quando o lote falha ─────────────────────────────────

def test_lote_falho_desce_a_cadeia_por_ticker(monkeypatch):
    monkeypatch.setattr(
        mdp.yf, "download",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Yahoo fora")),
    )
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda t, p, **k: mdp.HistoryResult(
            df=_serie_individual(), source="cache_stale", is_stale=True,
        ),
    )

    r = mdp.get_daily_closes_batch(["NVDA", "MU"], "1y", auto_adjust=True,
                                   permitir_externa=False)

    assert r.ok
    assert sorted(r.closes.columns) == ["MU", "NVDA"]
    assert r.degradadas == {"NVDA": "cache_stale", "MU": "cache_stale"}


def test_lote_falho_com_cadeia_parcial(monkeypatch):
    """Metade do lote com cache, metade sem: as colunas que existem entram,
    as que não existem ficam FORA em vez de virar coluna de NaN — e `fontes`
    diz exatamente quem sobreviveu."""
    monkeypatch.setattr(mdp.yf, "download", lambda *a, **k: None)

    def _por_ticker(t, p, **k):
        if t == "NVDA":
            return mdp.HistoryResult(df=_serie_individual(), source="cache_stale", is_stale=True)
        return mdp.HistoryResult(df=None, source="none")

    monkeypatch.setattr(mdp, "get_daily_history", _por_ticker)

    r = mdp.get_daily_closes_batch(["NVDA", "XXXX"], "1y")

    assert list(r.closes.columns) == ["NVDA"]
    assert "XXXX" not in r.fontes


def test_disjuntor_aberto_pula_o_lote(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(
        mdp.yf, "download",
        lambda *a, **k: pytest.fail("disjuntor aberto: o lote não deveria tocar a rede"),
    )
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda t, p, **k: mdp.HistoryResult(df=_serie_individual(), source="cache_stale"),
    )

    r = mdp.get_daily_closes_batch(["NVDA"], "1y")
    assert r.ok
    assert any("cooldown" in w for w in r.warnings)


def test_tudo_falho_devolve_vazio_explicito(monkeypatch):
    monkeypatch.setattr(mdp.yf, "download", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda *a, **k: mdp.HistoryResult(df=None, source="none"),
    )
    r = mdp.get_daily_closes_batch(["NVDA", "MU"], "1y")
    assert not r.ok
    assert any("lote inteiro" in w for w in r.warnings)


# ── risk_manager consome o lote ─────────────────────────────────────────────

def _mock_lote(monkeypatch, fontes, tickers=("NVDA", "MU")):
    def _falso(ts, period, **kwargs):
        data = _download_falso(list(ts))
        closes = data["Close"]
        return mdp.BatchClosesResult(closes=closes, fontes=dict(fontes))
    monkeypatch.setattr(rm.market_data_provider, "get_daily_closes_batch", _falso)


def test_correlation_sem_degradacao_nao_marca(monkeypatch):
    _mock_lote(monkeypatch, {"NVDA": "yfinance", "MU": "yfinance"})
    out = rm.correlation(["NVDA", "MU"])
    assert "error" not in out
    assert "fontesDegradadas" not in out


def test_correlation_degradada_marca_por_ticker(monkeypatch):
    _mock_lote(monkeypatch, {"NVDA": "cache_stale", "MU": "yfinance"})
    out = rm.correlation(["NVDA", "MU"])
    assert out["fontesDegradadas"] == {"NVDA": "cache_stale"}


def test_portfolio_risk_metrics_usa_o_lote(monkeypatch):
    _mock_lote(monkeypatch, {"NVDA": "yfinance", "MU": "yfinance"})
    out = rm.portfolio_risk_metrics([
        {"ticker": "NVDA", "investedAmount": 1000},
        {"ticker": "MU", "investedAmount": 500},
    ])
    assert "error" not in out
    assert "sharpeRatio" in out
    assert "maxDrawdownPct" in out


def test_stop_distance_usa_a_cadeia_sem_externa(monkeypatch):
    visto = {}

    def _falso(t, p, **kwargs):
        visto.update(kwargs)
        return mdp.HistoryResult(df=_serie_individual(), source="yfinance")

    monkeypatch.setattr(rm.market_data_provider, "get_daily_history", _falso)
    out = rm.stop_distance("NVDA")
    assert "error" not in out
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False
