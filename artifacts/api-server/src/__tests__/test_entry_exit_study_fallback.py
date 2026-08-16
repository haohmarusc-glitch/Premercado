"""
Testes da integração do entry_exit_study.py com a cadeia de fallback.

A decisão que estes testes fixam é a separação entre as DUAS buscas:

- O HISTÓRICO (mínimas, níveis de entrada) usa a cadeia inteira, fonte
  externa inclusive. Série não ajustada, e média de mínimas sobre dado de
  ontem continua útil.
- O PREÇO ATUAL continua exigindo yfinance ao vivo. Ele entra em
  log(alvo/preço) e define a probabilidade inteira: servir o fechamento de
  ontem como "preço atual" mudaria a resposta sem mudar a pergunta.

O módulo usa imports planos (`from startup_probe import ...`), então é
carregado por caminho, igual ao test_entry_exit_study.py que já existe. O
conftest.py fixa o pacote `agent` em sys.modules antes de tudo, então mexer
no sys.path aqui não quebra mais `from agent.x import y` nos testes coletados
depois — foi exatamente essa a correção do #279.
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
    "entry_exit_study", os.path.join(_AGENT_DIR, "entry_exit_study.py")
)
ees = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ees)


def _serie(n=252, base=100.0):
    idx = pd.date_range("2025-09-01", periods=n, freq="D")
    precos = base + np.linspace(0, 20, n)
    return pd.DataFrame({
        "Open": precos, "High": precos + 1, "Low": precos - 5,
        "Close": precos, "Volume": [1_000_000] * n,
    }, index=idx)


class _FastInfo:
    last_price = 120.0


class _TkFalso:
    def __init__(self, *a, **k):
        pass

    @property
    def fast_info(self):
        return _FastInfo()


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Neutraliza tudo o que não é o objeto do teste: cenário, earnings,
    reação e notícias fazem rede própria e não passam pela cadeia."""
    monkeypatch.setattr(ees, "yf", type("m", (), {"Ticker": _TkFalso}))
    monkeypatch.setattr(
        ees, "compute_scenario_params",
        lambda *a, **k: {"params": {"NVDA": {"volAnnual": 0.5, "betaSector": 1.2}},
                         "sectorMomentum": {"momentumAnnualPct": 10.0}},
    )
    monkeypatch.setattr(ees, "get_earnings", lambda *a, **k: [])
    monkeypatch.setattr(ees, "analyze_earnings_reaction", lambda *a, **k: {"summary": {}})
    monkeypatch.setattr(ees, "news_for_ticker", lambda *a, **k: {"news": []})
    monkeypatch.setattr(ees, "_company_names", lambda *a, **k: {})
    yield


def _mock_hist(monkeypatch, source, df=None, ok=True):
    visto = {}

    def _falso(ticker, period, **kwargs):
        visto["period"] = period
        visto.update(kwargs)
        return mdp.HistoryResult(
            df=(_serie() if df is None else df) if ok else None,
            source=source,
            is_stale=source == "cache_stale",
        )

    monkeypatch.setattr(ees.market_data_provider, "get_daily_history", _falso)
    return visto


def _estudo(**kw):
    return ees._study_for({"ticker": "NVDA", "targetPrice": 150.0,
                           "targetDate": "2099-01-01", **kw})


# ── o histórico usa a cadeia inteira ────────────────────────────────────────

def test_historico_usa_a_cadeia_com_serie_nao_ajustada(monkeypatch):
    """auto_adjust=False é o que libera a fonte externa: sem ajuste pedido,
    'as traded' serve para média de mínimas."""
    visto = _mock_hist(monkeypatch, "yfinance")
    out = _estudo()
    assert out.get("error") is None
    assert visto["period"] == "1y"
    assert visto["auto_adjust"] is False
    assert "permitir_externa" not in visto  # padrão: cadeia inteira


def test_sem_historico_devolve_erro(monkeypatch):
    _mock_hist(monkeypatch, "none", ok=False)
    assert _estudo()["error"] == "sem histórico de preço"


@pytest.mark.parametrize("source", ["cache_stale", "alphavantage"])
def test_historico_degradado_ainda_calcula(monkeypatch, source):
    """O ponto da integração: com o Yahoo fora, o estudo do dia sai. Antes
    ficava sem snapshot nenhum, sem nada dizendo por quê."""
    _mock_hist(monkeypatch, source)
    out = _estudo()
    assert out.get("error") is None
    assert out["probReachTarget"] is not None
    assert out["avgLow1y"] is not None


# ── marcação da fonte ───────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["yfinance", "yfinance_cache"])
def test_fonte_normal_nao_marca(monkeypatch, source):
    """Campo ausente é o caso normal — nenhum consumidor precisa mudar."""
    _mock_hist(monkeypatch, source)
    assert "fonteHistorico" not in _estudo()


@pytest.mark.parametrize("source", ["cache_stale", "alphavantage"])
def test_fonte_degradada_marca(monkeypatch, source):
    _mock_hist(monkeypatch, source)
    assert _estudo()["fonteHistorico"] == source


# ── o preço atual NÃO cai para o fallback ───────────────────────────────────

def test_preco_atual_vem_do_yfinance_ao_vivo(monkeypatch):
    """Ele entra em log(alvo/preço) e define a probabilidade inteira."""
    _mock_hist(monkeypatch, "yfinance")
    assert _estudo()["currentPrice"] == 120.0


def test_preco_atual_indisponivel_faz_o_estudo_falhar(monkeypatch):
    """Preferir erro a calcular sobre fechamento de ontem disfarçado de preço
    atual: num papel que andou 5% hoje, a probabilidade sairia materialmente
    errada e com cara de número bom."""
    _mock_hist(monkeypatch, "yfinance")

    class _TkMorto:
        def __init__(self, *a, **k): pass
        @property
        def fast_info(self): raise RuntimeError("Yahoo fora")

    monkeypatch.setattr(ees, "yf", type("m", (), {"Ticker": _TkMorto}))
    out = _estudo()
    assert "error" in out
    assert "falha ao buscar preço" in out["error"]


def test_nao_usa_get_quote_da_cadeia(monkeypatch):
    """Se alguém plugar get_quote aqui, o estudo passa a aceitar cotação
    atrasada como preço atual — exatamente o que a separação evita."""
    _mock_hist(monkeypatch, "yfinance")
    monkeypatch.setattr(
        ees.market_data_provider, "get_quote",
        lambda *a, **k: pytest.fail("preço atual não pode vir da cadeia"),
    )
    assert _estudo().get("error") is None
