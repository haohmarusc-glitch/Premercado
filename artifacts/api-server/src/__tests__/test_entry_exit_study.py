"""
Testes de entry_exit_study.py -- Estudo de Entrada e Saída (probabilidade de
um ticker bater um preço-alvo até uma data-alvo, com drift zero).

entry_exit_study.py roda como script standalone (spawnado direto por
routes/entry-exit-study.ts) com imports "flat" (`from security import ...`),
mesmo padrão de test_get_news_feed.py -- carrega via importlib e adiciona
src/agent/ ao sys.path pra replicar o cwd real de quando o script roda
sozinho.

Todas as dependências de rede (yfinance, get_scenario_params, get_earnings,
earnings_reaction_analysis, get_news_feed) são mockadas -- este teste valida
só a matemática (Phi, mínima/média de baixa, salto de earnings dentro/fora
da janela) e a integração entre as peças, não dados reais de mercado.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_entry_exit_study.py -v
"""
import os
import sys
import math
import importlib.util
from datetime import date, timedelta

import pandas as pd
import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_MODULE_PATH = os.path.join(_AGENT_DIR, "entry_exit_study.py")
_spec = importlib.util.spec_from_file_location("entry_exit_study", _MODULE_PATH)
ees = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ees)


# ── _phi (CDF normal padrão) ────────────────────────────────────────────────

def test_phi_zero_is_meio():
    assert ees._phi(0.0) == pytest.approx(0.5, abs=1e-9)


def test_phi_extremos():
    assert ees._phi(6.0) == pytest.approx(1.0, abs=1e-6)
    assert ees._phi(-6.0) == pytest.approx(0.0, abs=1e-6)


# ── _low_stats (média/mínima de baixa) ──────────────────────────────────────

def test_low_stats_media_e_minima():
    serie = pd.Series([10.0, 8.0, 12.0, 9.0])
    avg, minimo = ees._low_stats(serie)
    assert avg == pytest.approx(9.75)
    assert minimo == pytest.approx(8.0)


def test_low_stats_ignora_nan():
    serie = pd.Series([10.0, float("nan"), 6.0])
    avg, minimo = ees._low_stats(serie)
    assert avg == pytest.approx(8.0)
    assert minimo == pytest.approx(6.0)


def test_low_stats_vazio_devolve_none():
    assert ees._low_stats(pd.Series([], dtype=float)) == (None, None)


# ── _study_for (integração, com yfinance e módulos irmãos mockados) ────────

class _FakeFastInfo:
    def __init__(self, last_price):
        self.last_price = last_price


class _FakeTicker:
    def __init__(self, current_price=100.0, lows=None):
        self._current_price = current_price
        self.fast_info = _FakeFastInfo(current_price)
        n = 260
        lows = lows if lows is not None else [90.0] * n
        idx = pd.date_range(end=pd.Timestamp.today(), periods=len(lows), freq="B")
        self._hist = pd.DataFrame({
            "Open": lows, "High": [l + 5 for l in lows],
            "Low": lows, "Close": lows, "Volume": [1_000_000] * len(lows),
        }, index=idx)

    def history(self, period=None, auto_adjust=None):
        return self._hist


def _patch_deps(monkeypatch, *, current_price=100.0, lows=None, vol_annual=0.4,
                 beta_sector=1.2, earnings_date=None, jump_std_pct=None):
    monkeypatch.setattr(ees.yf, "Ticker", lambda t: _FakeTicker(current_price, lows))
    monkeypatch.setattr(
        ees, "compute_scenario_params",
        lambda tickers, benchmark: {"params": {tickers[0]: {"volAnnual": vol_annual, "betaSector": beta_sector}}},
    )
    monkeypatch.setattr(
        ees, "get_earnings",
        lambda tickers: [{"ticker": tickers[0], "earningsDate": earnings_date}],
    )
    monkeypatch.setattr(
        ees, "analyze_earnings_reaction",
        lambda ticker: {"summary": {"close_pct_std": jump_std_pct}} if jump_std_pct else {"error": "sem histórico"},
    )
    monkeypatch.setattr(ees, "_company_names", lambda tickers: {tickers[0]: "Fake Corp"})
    monkeypatch.setattr(ees, "news_for_ticker", lambda ticker, n, all_t, names: {"ticker": ticker, "news": []})


def test_study_for_ticker_invalido(monkeypatch):
    _patch_deps(monkeypatch)
    # sanitize_ticker exige 1-8 caracteres alfanuméricos (+ sufixo opcional
    # de bolsa/classe) -- string vazia depois de tirar os símbolos falha.
    out = ees._study_for({"ticker": "!!!!", "targetPrice": 10, "targetDate": "2099-01-01"})
    assert "error" in out


def test_study_for_target_date_no_passado(monkeypatch):
    _patch_deps(monkeypatch)
    ontem = (date.today() - timedelta(days=1)).isoformat()
    out = ees._study_for({"ticker": "SMCI", "targetPrice": 50, "targetDate": ontem})
    assert "error" in out
    assert "futuro" in out["error"]


def test_study_for_calculo_basico_sem_earnings_na_janela(monkeypatch):
    alvo = (date.today() + timedelta(days=30)).isoformat()
    _patch_deps(monkeypatch, current_price=100.0, lows=[90.0] * 260, vol_annual=0.4, earnings_date=None)
    out = ees._study_for({"ticker": "SMCI", "targetPrice": 110.0, "targetDate": alvo})

    assert out["ticker"] == "SMCI"
    assert out["currentPrice"] == pytest.approx(100.0)
    assert out["avgLow1y"] == pytest.approx(90.0)
    assert out["minLow1y"] == pytest.approx(90.0)
    assert out["volAnnual"] == pytest.approx(0.4)
    assert out["betaSector"] == pytest.approx(1.2)
    assert out["daysUntilTarget"] == 30
    assert out["probReachTarget"] is not None
    # drift zero + alvo acima do preço atual -> probabilidade abaixo de 50%
    assert 0 < out["probReachTarget"] < 0.5


def test_study_for_alvo_igual_preco_atual_prob_50pct(monkeypatch):
    alvo = (date.today() + timedelta(days=60)).isoformat()
    _patch_deps(monkeypatch, current_price=100.0, vol_annual=0.5, earnings_date=None)
    out = ees._study_for({"ticker": "SMCI", "targetPrice": 100.0, "targetDate": alvo})
    # log(alvo/atual) = 0 -> Phi(0) = 0.5 -> prob = 1 - 0.5 = 0.5, drift zero
    assert out["probReachTarget"] == pytest.approx(0.5, abs=1e-6)


def test_study_for_earnings_dentro_da_janela_aumenta_volatilidade(monkeypatch):
    alvo = (date.today() + timedelta(days=30)).isoformat()
    earnings_dentro = (date.today() + timedelta(days=10)).isoformat()

    _patch_deps(monkeypatch, current_price=100.0, vol_annual=0.3,
                earnings_date=earnings_dentro, jump_std_pct=8.0)
    com_salto = ees._study_for({"ticker": "SMCI", "targetPrice": 110.0, "targetDate": alvo})

    _patch_deps(monkeypatch, current_price=100.0, vol_annual=0.3,
                earnings_date=None, jump_std_pct=None)
    sem_salto = ees._study_for({"ticker": "SMCI", "targetPrice": 110.0, "targetDate": alvo})

    # earnings dentro da janela [hoje, alvo] soma variância -> sd maior ->
    # probabilidade de bater um alvo ACIMA do preço sobe (mais massa na cauda).
    assert com_salto["probReachTarget"] > sem_salto["probReachTarget"]


def test_study_for_earnings_fora_da_janela_nao_afeta(monkeypatch):
    alvo = (date.today() + timedelta(days=10)).isoformat()
    earnings_depois_do_alvo = (date.today() + timedelta(days=60)).isoformat()

    _patch_deps(monkeypatch, current_price=100.0, vol_annual=0.3,
                earnings_date=earnings_depois_do_alvo, jump_std_pct=8.0)
    fora = ees._study_for({"ticker": "SMCI", "targetPrice": 110.0, "targetDate": alvo})

    _patch_deps(monkeypatch, current_price=100.0, vol_annual=0.3,
                earnings_date=None, jump_std_pct=None)
    sem_evento = ees._study_for({"ticker": "SMCI", "targetPrice": 110.0, "targetDate": alvo})

    assert fora["probReachTarget"] == pytest.approx(sem_evento["probReachTarget"], abs=1e-9)
