"""
Testes da integração do get_trend.py com a cadeia de fallback.

Duas propriedades, e a segunda é a que quase virou um bug:

1. Série AJUSTADA => a cadeia para no cache vencido (`permitir_externa=False`).
   Mesma razão do get_technicals: a fonte externa é "as traded" e um split no
   ano viraria degrau de preço.
2. Quando o dado vem degradado, o resultado sai MARCADO. O módulo já tinha um
   stale-if-error que marcava; plugar a cadeia sem propagar teria trocado
   "resultado velho e marcado" por "resultado fresco calculado sobre série
   velha, sem marca" — pior que não integrar.

Import de PACOTE (`from agent import ...`) — ver o cabeçalho de
test_provider_fallback.py.
"""
import numpy as np
import pandas as pd
import pytest

from agent import get_trend as gtr
from agent import market_data_provider as mdp


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """As notícias são outra fonte (yf.Ticker.news) e não passam pela cadeia —
    neutralizadas para o teste não depender de rede."""
    monkeypatch.setattr(
        gtr, "news_sentiment",
        lambda *a, **k: {"label": "neutro", "score": 0.0, "positivas": 0,
                         "negativas": 0, "analisadas": 0, "destaques": []},
    )
    yield


def _serie(n=250, inclinacao=30.0):
    """Série longa e em alta clara — o módulo exige 60+ candles."""
    idx = pd.date_range("2025-09-01", periods=n, freq="D")
    precos = 100.0 + np.linspace(0, inclinacao, n)
    return pd.DataFrame({
        "Open": precos, "High": precos + 1, "Low": precos - 1,
        "Close": precos, "Volume": [1_000_000] * n,
    }, index=idx)


def _mock(monkeypatch, source, is_stale=False, df=None):
    visto = {}

    def _falso(ticker, period, **kwargs):
        visto.update(kwargs)
        visto["period"] = period
        return mdp.HistoryResult(
            df=_serie() if df is None else df, source=source, is_stale=is_stale,
        )

    monkeypatch.setattr(gtr.market_data_provider, "get_daily_history", _falso)
    return visto


# ── a cadeia é usada, e cortada ─────────────────────────────────────────────

def test_usa_a_cadeia_com_serie_ajustada_e_sem_fonte_externa(monkeypatch):
    visto = _mock(monkeypatch, "yfinance")
    out = gtr.for_ticker("NVDA")
    assert out.get("error") is None
    assert visto["period"] == "1y"
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False


def test_sem_dado_devolve_erro(monkeypatch):
    monkeypatch.setattr(
        gtr.market_data_provider, "get_daily_history",
        lambda *a, **k: mdp.HistoryResult(df=None, source="none"),
    )
    assert gtr.for_ticker("NVDA")["error"] == "Dados insuficientes"


def test_serie_curta_ainda_e_erro(monkeypatch):
    _mock(monkeypatch, "yfinance", df=_serie(n=30))
    assert gtr.for_ticker("NVDA")["error"] == "Dados insuficientes"


# ── marcação de degradação ──────────────────────────────────────────────────

def test_yfinance_nao_marca_stale(monkeypatch):
    _mock(monkeypatch, "yfinance")
    out = gtr.for_ticker("NVDA")
    assert "stale" not in out
    assert "fonteHistorico" not in out


def test_cache_no_ttl_nao_marca_stale(monkeypatch):
    """Cache dentro do TTL é caminho normal do repo, não degradação."""
    _mock(monkeypatch, "yfinance_cache")
    assert "stale" not in gtr.for_ticker("NVDA")


def test_cache_vencido_marca_stale(monkeypatch):
    """A propriedade que quase se perdeu: sinal calculado sobre série vencida
    tem que sair marcado. Um 'compra' em cima do fechamento de ontem, sem
    aviso, é exatamente o que não pode acontecer."""
    _mock(monkeypatch, "cache_stale", is_stale=True)
    out = gtr.for_ticker("NVDA")
    assert out["stale"] is True
    assert out["fonteHistorico"] == "cache_stale"


def test_marcacao_reusa_o_campo_que_ja_existia(monkeypatch):
    """O módulo já emitia `stale` no stale-if-error do __main__ — usar o mesmo
    campo evita dois vocabulários de degradação para quem consome."""
    _mock(monkeypatch, "cache_stale", is_stale=True)
    out = gtr.for_ticker("NVDA")
    assert isinstance(out["stale"], bool)


def test_resultado_degradado_ainda_traz_o_sinal_completo(monkeypatch):
    """Degradado não é vazio: os componentes continuam lá, só rotulados."""
    _mock(monkeypatch, "cache_stale", is_stale=True)
    out = gtr.for_ticker("NVDA")
    assert out["sinal"] in ("compra", "venda", "aguardar")
    assert out["components"]["maCruzamento"] in ("alta", "baixa")
    assert out["trend"]


def test_ticker_invalido_nao_busca_dado(monkeypatch):
    monkeypatch.setattr(
        gtr.market_data_provider, "get_daily_history",
        lambda *a, **k: pytest.fail("ticker inválido não deveria buscar dado"),
    )
    assert "error" in gtr.for_ticker("../etc/passwd")
