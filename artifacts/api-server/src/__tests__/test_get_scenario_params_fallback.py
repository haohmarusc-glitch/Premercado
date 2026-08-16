"""
Testes da integração do get_scenario_params.py com o lote da cadeia.

Este módulo alimenta vol_annual/beta_sector do Painel de Cenários e do Estudo
de Entrada/Saída — números que mudam devagar, então servir cache de ontem numa
queda do Yahoo é aceitável DESDE QUE marcado. A série é ajustada, logo a fonte
externa fica cortada (um split na janela distorceria vol e beta de uma vez).

O módulo não tinha teste nenhum até aqui: os casos de matemática (vol, beta,
benchmark) entram junto porque a integração mexeu no caminho do dado — sem
eles, um erro de coluna no lote passaria batido.

Carregado por caminho (imports planos), como os demais — ver
test_entry_exit_study_fallback.py.
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

from agent import market_data_provider as mdp

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_spec = importlib.util.spec_from_file_location(
    "get_scenario_params", os.path.join(_AGENT_DIR, "get_scenario_params.py")
)
gsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsp)


def _closes(symbols, n=260):
    """Fechamentos com relação conhecida: cada ticker é o benchmark escalado,
    então o beta esperado é o próprio fator de escala."""
    idx = pd.date_range("2025-08-01", periods=n, freq="B")
    rng = np.random.default_rng(11)
    bench = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    cols = {}
    for i, s in enumerate(symbols):
        fator = 1.0 + 0.5 * i
        serie = 100.0 * np.exp(np.cumsum(fator * np.diff(np.log(bench), prepend=np.log(bench[0]))))
        cols[s] = serie
    return pd.DataFrame(cols, index=idx)


def _mock_lote(monkeypatch, symbols, fontes=None, ok=True):
    visto = {}

    def _falso(ts, period, **kwargs):
        visto["symbols"] = list(ts)
        visto["period"] = period
        visto.update(kwargs)
        if not ok:
            return mdp.BatchClosesResult(closes=None, warnings=["Nenhuma fonte de histórico disponível para o lote inteiro"])
        closes = _closes(symbols)
        return mdp.BatchClosesResult(
            closes=closes,
            fontes=fontes or {s: "yfinance" for s in symbols},
        )

    monkeypatch.setattr(gsp.market_data_provider, "get_daily_closes_batch", _falso)
    return visto


# ── roteamento ──────────────────────────────────────────────────────────────

def test_usa_o_lote_com_serie_ajustada_e_sem_externa(monkeypatch):
    visto = _mock_lote(monkeypatch, ["NVDA", "SMH"])
    out = gsp.compute(["NVDA"], "SMH")
    assert "error" not in out["params"]["NVDA"]
    assert visto["period"] == "1y"
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False
    # benchmark entra no MESMO lote, deduplicado — uma chamada só.
    assert visto["symbols"] == ["NVDA", "SMH"]


def test_lote_falho_devolve_o_contrato_de_erro(monkeypatch):
    """Mesma forma de erro de antes: {params: {ticker: {error}}, ...} — o
    checker que persiste o resultado não precisa mudar."""
    _mock_lote(monkeypatch, [], ok=False)
    out = gsp.compute(["NVDA", "MU"], "SMH")
    assert set(out["params"]) == {"NVDA", "MU"}
    for v in out["params"].values():
        assert "error" in v
    assert out["sectorMomentum"] is None


# ── matemática preservada ───────────────────────────────────────────────────

def test_beta_do_benchmark_e_um(monkeypatch):
    _mock_lote(monkeypatch, ["SMH"])
    out = gsp.compute(["SMH"], "SMH")
    assert out["params"]["SMH"]["betaSector"] == pytest.approx(1.0)


def test_beta_reflete_a_escala_dos_retornos(monkeypatch):
    """NVDA construída como benchmark × 1.5 em retornos log → beta ~1.5."""
    _mock_lote(monkeypatch, ["SMH", "NVDA"])
    out = gsp.compute(["NVDA"], "SMH")
    assert out["params"]["NVDA"]["betaSector"] == pytest.approx(1.5, abs=0.05)
    assert out["params"]["NVDA"]["volAnnual"] > 0


def test_momentum_vem_do_mesmo_lote(monkeypatch):
    _mock_lote(monkeypatch, ["NVDA", "SMH"])
    out = gsp.compute(["NVDA"], "SMH")
    sm = out["sectorMomentum"]
    assert sm is not None
    assert sm["benchmark"] == "SMH"
    assert sm["lookbackDays"] == gsp.MOMENTUM_LOOKBACK_DAYS


def test_ticker_fora_do_lote_ganha_erro_individual(monkeypatch):
    """Um ticker sem coluna (deslistado, sem cache) não derruba os demais."""
    _mock_lote(monkeypatch, ["NVDA", "SMH"])
    out = gsp.compute(["NVDA", "XXXX"], "SMH")
    assert "error" not in out["params"]["NVDA"]
    assert out["params"]["XXXX"]["error"] == "Sem dados de preço"


# ── marcação de degradação ──────────────────────────────────────────────────

def test_fonte_normal_nao_marca(monkeypatch):
    _mock_lote(monkeypatch, ["NVDA", "SMH"])
    out = gsp.compute(["NVDA"], "SMH")
    assert "fontesDegradadas" not in out


def test_fonte_degradada_marca_por_ticker(monkeypatch):
    _mock_lote(
        monkeypatch, ["NVDA", "SMH"],
        fontes={"NVDA": "cache_stale", "SMH": "yfinance"},
    )
    out = gsp.compute(["NVDA"], "SMH")
    assert out["fontesDegradadas"] == {"NVDA": "cache_stale"}
    # Degradado não é vazio: vol/beta continuam calculados, só rotulados.
    assert out["params"]["NVDA"]["volAnnual"] > 0


def test_saida_e_serializavel(monkeypatch):
    """O resultado vai por stdout pro checker Node — np.float64 solto num
    campo novo derrubaria o consumidor inteiro."""
    import json
    _mock_lote(monkeypatch, ["NVDA", "SMH"], fontes={"NVDA": "cache_stale", "SMH": "yfinance"})
    json.dumps(gsp.compute(["NVDA"], "SMH"))
