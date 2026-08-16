"""
Testes do get_ticker_snapshot.py (tela Análise Rápida, terceiro painel).

O contrato importante é o de FALHA PARCIAL: as duas fontes (fast_info ao
vivo e cadeia de cenário) são independentes, e a tela mostra o que veio.
"error" cheio só quando as duas falham — um Yahoo fora do ar não pode
esconder o vol/beta que a cadeia ainda sabe servir do cache.

Carregado por caminho (imports planos) — ver test_entry_exit_study_fallback.py.
"""
import importlib.util
import json
import os
import sys

import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_spec = importlib.util.spec_from_file_location(
    "get_ticker_snapshot", os.path.join(_AGENT_DIR, "get_ticker_snapshot.py")
)
gts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gts)


class _FastInfo:
    last_price = 102.5
    year_low = 22.78
    year_high = 142.35
    fifty_day_average = 109.4
    two_hundred_day_average = 70.14


def _mock_quote(monkeypatch, info=_FastInfo(), erro=None):
    class _Tk:
        def __init__(self, *a, **k):
            if erro:
                raise RuntimeError(erro)
        fast_info = info
    monkeypatch.setattr(gts.yf, "Ticker", _Tk)


def _cenario_ok(tickers, benchmark):
    return {
        "params": {tickers[0]: {"volAnnual": 0.7925, "betaSector": 1.4147, "daysUsed": 250}},
        "sectorMomentum": {"benchmark": benchmark, "momentumAnnualPct": 109.17, "lookbackDays": 90},
    }


def test_caminho_feliz_junta_as_duas_fontes(monkeypatch):
    _mock_quote(monkeypatch)
    monkeypatch.setattr(gts, "compute_cenario", _cenario_ok)
    out = gts.snapshot("INTC", "SMH")
    assert out["price"] == 102.5
    assert out["yearLow"] == 22.78
    assert out["yearHigh"] == 142.35
    assert out["sma200"] == 70.14
    assert out["volAnnual"] == pytest.approx(0.7925)
    assert out["betaSector"] == pytest.approx(1.4147)
    assert out["sectorMomentum"]["benchmark"] == "SMH"
    assert "error" not in out
    assert "quoteError" not in out
    assert "cenarioError" not in out
    json.dumps(out)  # vai por stdout pro Node


def test_yahoo_fora_preserva_o_cenario(monkeypatch):
    """Falha parcial: sem fast_info a tela ainda mostra vol/beta da cadeia."""
    _mock_quote(monkeypatch, erro="Yahoo fora")
    monkeypatch.setattr(gts, "compute_cenario", _cenario_ok)
    out = gts.snapshot("INTC", "SMH")
    assert out["quoteError"] == "Yahoo fora"
    assert out["volAnnual"] == pytest.approx(0.7925)
    assert "error" not in out


def test_cenario_com_erro_preserva_a_cotacao(monkeypatch):
    _mock_quote(monkeypatch)
    monkeypatch.setattr(
        gts, "compute_cenario",
        lambda ts, b: {"params": {ts[0]: {"error": "Sem dados de preço"}}, "sectorMomentum": None},
    )
    out = gts.snapshot("XXXX", "SMH")
    assert out["cenarioError"] == "Sem dados de preço"
    assert out["price"] == 102.5
    assert "error" not in out


def test_tudo_falho_vira_erro_cheio(monkeypatch):
    _mock_quote(monkeypatch, erro="Yahoo fora")
    monkeypatch.setattr(
        gts, "compute_cenario",
        lambda ts, b: (_ for _ in ()).throw(RuntimeError("cadeia fora")),
    )
    out = gts.snapshot("INTC", "SMH")
    assert out["quoteError"] and out["cenarioError"]
    assert "error" in out


def test_preco_nan_vira_none_com_aviso(monkeypatch):
    class _Nan(_FastInfo):
        last_price = float("nan")
    _mock_quote(monkeypatch, info=_Nan())
    monkeypatch.setattr(gts, "compute_cenario", _cenario_ok)
    out = gts.snapshot("INTC", "SMH")
    assert out["price"] is None
    assert out["quoteError"] == "Sem preço no yfinance"


def test_fontes_degradadas_propagadas(monkeypatch):
    _mock_quote(monkeypatch)

    def _degradado(ts, b):
        cen = _cenario_ok(ts, b)
        cen["fontesDegradadas"] = {ts[0]: "cache_stale"}
        return cen

    monkeypatch.setattr(gts, "compute_cenario", _degradado)
    out = gts.snapshot("INTC", "SMH")
    assert out["fontesDegradadas"] == {"INTC": "cache_stale"}
