"""
Testes da integração do market_alerts._history com a cadeia de fallback.

O ponto central aqui é diferente do get_quotes: não é "por lote", é POR
PERÍODO. Só os períodos cacheáveis (3mo+) vão para a cadeia; os curtos (5d,
1mo, 2mo) seguem no yfinance direto, porque são pedidos em laço por ticker e
drenariam sozinhos a cota diária compartilhada com o feed de notícias.

Import de PACOTE (`from agent import ...`) — ver o cabeçalho de
test_provider_fallback.py.
"""
import pandas as pd
import pytest

from agent import hist_cache
from agent import market_alerts as ma
from agent import market_data_provider as mdp


@pytest.fixture(autouse=True)
def _limpa_estado(monkeypatch):
    """Os caches e o contador de fontes são de módulo — sem limpar, um teste
    enxerga o resultado do anterior e a ordem passa a decidir o resultado."""
    ma._HIST_CACHE.clear()
    ma._FONTES_DEGRADADAS.clear()
    monkeypatch.setattr(hist_cache, "carregar", lambda *a, **k: None)
    yield
    ma._HIST_CACHE.clear()
    ma._FONTES_DEGRADADAS.clear()


def _df(precos):
    idx = pd.date_range("2026-08-01", periods=len(precos), freq="D")
    return pd.DataFrame({
        "Open": precos, "High": precos, "Low": precos,
        "Close": precos, "Volume": [1_000] * len(precos),
    }, index=idx)


def _resultado(source, df=None, warnings=None):
    return mdp.HistoryResult(
        df=df if df is not None else _df([10.0, 11.0]),
        source=source,
        is_stale=source == "cache_stale",
        warnings=warnings or [],
    )


# ── qual período vai para a cadeia ──────────────────────────────────────────

@pytest.mark.parametrize("period", ["3mo", "6mo", "1y", "2y"])
def test_periodo_cacheavel_usa_a_cadeia(monkeypatch, period):
    chamou = {}

    def _falso(t, p, **k):
        chamou["args"] = (t, p)
        return _resultado("yfinance")

    monkeypatch.setattr(mdp, "get_daily_history", _falso)
    df = ma._history("NVDA", period=period)
    assert df is not None
    assert chamou["args"] == ("NVDA", period)


@pytest.mark.parametrize("period", ["5d", "1mo", "2mo"])
def test_periodo_curto_nao_gasta_cota(monkeypatch, period):
    """`5d` é pedido em laço por ticker; mandá-lo para a fonte externa esgotaria
    as 15 chamadas do dia antes do 6mo/1y, que é o que alimenta os
    indicadores."""
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda *a, **k: pytest.fail(f"{period} não deveria ir para a cadeia"),
    )

    class _TkFalso:
        def __init__(self, *a, **k): pass
        def history(self, **k): return _df([1.0, 2.0])

    monkeypatch.setattr(ma.yf, "Ticker", _TkFalso)
    assert ma._history("NVDA", period=period) is not None


def test_criterio_e_o_mesmo_do_hist_cache():
    """A regra de "vai para a cadeia" reusa hist_cache.cacheavel() — se as duas
    divergirem, um período ganha fallback sem ter cache para protegê-lo."""
    assert hist_cache.cacheavel("6mo") is True
    assert hist_cache.cacheavel("5d") is False


# ── caches vêm antes de tudo ────────────────────────────────────────────────

def test_cache_de_memoria_curto_circuita(monkeypatch):
    ma._HIST_CACHE["NVDA:6mo"] = _df([99.0])
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda *a, **k: pytest.fail("cache em memória deveria ter servido"),
    )
    assert float(ma._history("NVDA", period="6mo")["Close"].iloc[0]) == 99.0


def test_cache_de_disco_curto_circuita(monkeypatch):
    monkeypatch.setattr(hist_cache, "carregar", lambda *a, **k: _df([77.0]))
    monkeypatch.setattr(
        mdp, "get_daily_history",
        lambda *a, **k: pytest.fail("cache em disco deveria ter servido"),
    )
    assert float(ma._history("NVDA", period="6mo")["Close"].iloc[0]) == 77.0


# ── contagem de fonte degradada ─────────────────────────────────────────────

def test_yfinance_nao_conta_como_degradado(monkeypatch):
    monkeypatch.setattr(mdp, "get_daily_history", lambda *a, **k: _resultado("yfinance"))
    ma._history("NVDA", period="6mo")
    assert ma._FONTES_DEGRADADAS == {}


def test_cache_dentro_do_ttl_nao_conta_como_degradado(monkeypatch):
    """Cache no TTL é o caminho normal do repo, não degradação."""
    monkeypatch.setattr(mdp, "get_daily_history", lambda *a, **k: _resultado("yfinance_cache"))
    ma._history("NVDA", period="6mo")
    assert ma._FONTES_DEGRADADAS == {}


def test_falha_total_nao_conta_como_degradado(monkeypatch):
    """"Nenhuma fonte respondeu" é ausência de dado, não dado ruim — quem
    chama já trata o None."""
    monkeypatch.setattr(
        mdp, "get_daily_history", lambda *a, **k: mdp.HistoryResult(df=None, source="none"),
    )
    assert ma._history("NVDA", period="6mo") is None
    assert ma._FONTES_DEGRADADAS == {}


@pytest.mark.parametrize("source", ["cache_stale", "alphavantage"])
def test_fonte_degradada_e_contada(monkeypatch, source):
    monkeypatch.setattr(mdp, "get_daily_history", lambda *a, **k: _resultado(source))
    ma._history("NVDA", period="6mo")
    assert ma._FONTES_DEGRADADAS == {source: 1}


def test_conta_uma_vez_por_serie(monkeypatch):
    monkeypatch.setattr(mdp, "get_daily_history", lambda *a, **k: _resultado("cache_stale"))
    ma._history("NVDA", period="6mo")
    ma._history("MU", period="6mo")
    ma._history("STX", period="1y")
    assert ma._FONTES_DEGRADADAS == {"cache_stale": 3}


# ── o alerta de dado degradado ──────────────────────────────────────────────

def test_sem_degradacao_nao_gera_alerta():
    assert ma.check_dado_degradado() == []


def test_alerta_descreve_a_fonte_e_a_contagem():
    ma._FONTES_DEGRADADAS.update({"cache_stale": 2, "alphavantage": 1})
    [alerta] = ma.check_dado_degradado()
    assert alerta.severity == ma.Severity.ATENCAO
    assert "cache vencido" in alerta.detail
    assert "Alpha Vantage" in alerta.detail
    assert alerta.value == 3.0


def test_alerta_e_atencao_nao_critico():
    """O sistema está funcionando, só não com a fonte primária — marcar como
    crítico faria o alerta de verdade se perder no meio."""
    ma._FONTES_DEGRADADAS["alphavantage"] = 1
    [alerta] = ma.check_dado_degradado()
    assert alerta.severity != ma.Severity.CRITICO


def test_alerta_serializa():
    ma._FONTES_DEGRADADAS["cache_stale"] = 1
    d = ma.check_dado_degradado()[0].to_dict()
    assert d["severity"] == ma.Severity.ATENCAO.value
    assert d["category"] == ma.Category.TECNICO.value
