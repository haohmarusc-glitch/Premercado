"""
Testes do ciclo_volatilidade.py (tela Previsão de Volatilidade).

Cada fase é fixada com uma série OHLCV sintética construída para provocá-la —
o critério de cada uma é regra numérica pura, então dá pra testar sem rede.
A checagem de earnings é selada (é aviso opcional, ao vivo, tolerante a
falha — os testes garantem só que a falha dela não derruba o cálculo).

Carregado por caminho (imports planos) — ver test_entry_exit_study_fallback.py.
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_spec = importlib.util.spec_from_file_location(
    "ciclo_volatilidade", os.path.join(_AGENT_DIR, "ciclo_volatilidade.py")
)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    monkeypatch.setattr(cv, "_earnings_proximo", lambda t: None)


def _ohlcv(daily_ranges, volumes=None, n_base_vol=0.01, seed=3):
    """Série com retornos de vol `n_base_vol` e range diário controlado por
    dia — `daily_ranges` é a fração (H-L)/C de cada pregão, do começo ao fim."""
    n = len(daily_ranges)
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, n_base_vol, n)))
    half = close * np.asarray(daily_ranges) / 2
    vol = np.asarray(volumes) if volumes is not None else np.full(n, 1_000_000.0)
    idx = pd.date_range("2025-08-01", periods=n, freq="B")
    return pd.DataFrame({
        "Open": close, "High": close + half, "Low": close - half,
        "Close": close, "Volume": vol,
    }, index=idx)


def _mock_hist(monkeypatch, df, source="yfinance"):
    monkeypatch.setattr(
        cv.market_data_provider, "get_daily_history",
        lambda t, p, **k: cv.market_data_provider.HistoryResult(df=df, source=source),
    )


def _serie_com_vol(vol_recente, n=260, n_recente=30, seed=5):
    """Retornos de 1% a.d. no grosso da janela e `vol_recente` no fim —
    controla a razão EWMA/estrutural diretamente."""
    rng = np.random.default_rng(seed)
    r = np.concatenate([
        rng.normal(0, 0.01, n - n_recente),
        rng.normal(0, vol_recente, n_recente),
    ])
    close = 100.0 * np.exp(np.cumsum(r))
    idx = pd.date_range("2025-08-01", periods=n, freq="B")
    half = close * 0.01
    return pd.DataFrame({
        "Open": close, "High": close + half, "Low": close - half,
        "Close": close, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


# ── fases ───────────────────────────────────────────────────────────────────

def test_normal_em_serie_estacionaria(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 260))
    out = cv.analisar("NVDA")
    assert out["fase"] == "NORMAL"
    assert 0.8 <= out["razaoRegime"] <= 1.2


def test_gatilho_quando_o_range_de_hoje_estoura_com_volume(monkeypatch):
    ranges = [0.02] * 259 + [0.10]           # hoje: range 5× o normal
    volumes = [1_000_000.0] * 259 + [3_000_000.0]
    _mock_hist(monkeypatch, _ohlcv(ranges, volumes))
    out = cv.analisar("NVDA")
    assert out["fase"] == "GATILHO"
    assert out["rvol"] == pytest.approx(3.0, abs=0.1)


def test_expansao_quando_ewma_roda_acima_da_estrutural(monkeypatch):
    _mock_hist(monkeypatch, _serie_com_vol(0.035))
    out = cv.analisar("INTC")
    assert out["fase"] == "EXPANSAO"
    assert out["razaoRegime"] >= cv.RAZAO_EXPANSAO
    assert out["diasParaNormalizar"] is not None
    assert out["diasParaNormalizar"] > 0


def test_decaimento_com_ranges_encolhendo(monkeypatch):
    df = _serie_com_vol(0.035)
    # sobrescreve o fim com ranges estritamente decrescentes
    for i, r in enumerate([0.06, 0.05, 0.04, 0.03][::-1]):
        c = float(df["Close"].iloc[-1 - i])
        df.iloc[-1 - i, df.columns.get_loc("High")] = c * (1 + r / 2)
        df.iloc[-1 - i, df.columns.get_loc("Low")] = c * (1 - r / 2)
    _mock_hist(monkeypatch, df)
    out = cv.analisar("INTC")
    assert out["fase"] == "DECAIMENTO"
    assert out["rangesDecrescentes"] >= cv.RANGES_DECRESCENTES_MIN


def test_comprimida_quando_vol_recente_e_baixa(monkeypatch):
    _mock_hist(monkeypatch, _serie_com_vol(0.004))
    out = cv.analisar("TOL")
    assert out["fase"] == "COMPRIMIDA"
    assert out["razaoRegime"] <= cv.RAZAO_COMPRIMIDA


# ── contratos ───────────────────────────────────────────────────────────────

def test_banda_de_amanha_e_simetrica_no_sigma(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 260))
    out = cv.analisar("NVDA")
    preco = out["preco"]
    sigma = out["sigmaDiaPct"] / 100
    assert out["bandaAmanha"]["high"] == pytest.approx(preco * (1 + sigma), abs=0.01)
    assert out["bandaAmanha"]["low"] == pytest.approx(preco * (1 - sigma), abs=0.01)
    assert out["bandaAmanha"]["high2"] > out["bandaAmanha"]["high"]


def test_historico_insuficiente_vira_erro(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 30))
    out = cv.analisar("NOVO")
    assert "error" in out


def test_cadeia_sem_dado_vira_erro_explicito(monkeypatch):
    monkeypatch.setattr(
        cv.market_data_provider, "get_daily_history",
        lambda t, p, **k: cv.market_data_provider.HistoryResult(
            df=None, source="none", warnings=["Yahoo fora"],
        ),
    )
    out = cv.analisar("NVDA")
    assert out["error"] == "Yahoo fora"


def test_fonte_degradada_marcada(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 260), source="cache_stale")
    out = cv.analisar("NVDA")
    assert out["fonteHistorico"] == "cache_stale"


def test_fonte_normal_nao_marca(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 260), source="yfinance")
    out = cv.analisar("NVDA")
    assert "fonteHistorico" not in out


def test_serie_ajustada_sem_externa(monkeypatch):
    visto = {}

    def _falso(t, p, **kwargs):
        visto.update(kwargs)
        return cv.market_data_provider.HistoryResult(df=_ohlcv([0.02] * 260), source="yfinance")

    monkeypatch.setattr(cv.market_data_provider, "get_daily_history", _falso)
    cv.analisar("NVDA")
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False


def test_earnings_proximo_anexa_aviso_sem_mudar_fase(monkeypatch):
    _mock_hist(monkeypatch, _ohlcv([0.02] * 260))
    monkeypatch.setattr(cv, "_earnings_proximo", lambda t: {"data": "2026-08-18", "dias": 2})
    out = cv.analisar("BIDU")
    assert out["fase"] == "NORMAL"
    assert out["earningsProximo"]["dias"] == 2
    assert any("Reação a Earnings" in m for m in out["motivos"])


def test_saida_e_serializavel(monkeypatch):
    _mock_hist(monkeypatch, _serie_com_vol(0.035), source="cache_stale")
    json.dumps(cv.analisar("INTC"))
