"""
Testes de provider_health.py + market_data_provider.py + stooq_provider.py.

Convenção do repo: pytest com monkeypatch em vez de mock de biblioteca, mesmo
padrão simples do restante da suíte. Nenhum teste toca a rede.

Import de PACOTE (`from agent import ...`), nunca inserindo `src/agent` no
sys.path: existe um `agent.py` além do pacote `agent/`, e colocar o diretório
do pacote no path faz o nome `agent` resolver para o módulo solto, quebrando
`from agent.x import y` em qualquer teste coletado depois. O conftest.py já
põe `src/` no path.
"""
import time

import pandas as pd
import pytest

from agent import market_data_provider as mdp
from agent import provider_health


@pytest.fixture(autouse=True)
def _isolated_health_file(tmp_path, monkeypatch):
    """Cada teste usa seu próprio arquivo de estado — sem isso, um teste que
    abre o disjuntor contaminaria o seguinte, e a ordem de coleta passaria a
    decidir o resultado."""
    monkeypatch.setattr(provider_health, "_PATH", str(tmp_path / "health.json"))
    yield


# ── disjuntor ───────────────────────────────────────────────────────────────

def test_breaker_starts_closed():
    assert provider_health.is_open("yfinance") is False


def test_breaker_opens_after_threshold_failures():
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is True


def test_breaker_stays_closed_below_threshold():
    for _ in range(provider_health.FAILURE_THRESHOLD - 1):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is False


def test_success_resets_breaker():
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is True
    provider_health.record_success("yfinance")
    assert provider_health.is_open("yfinance") is False


def test_breaker_closes_after_cooldown():
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is True

    future = time.time() + provider_health.COOLDOWN_S + 1
    assert provider_health.is_open("yfinance", now=future) is False


def test_breaker_is_per_provider():
    """Yahoo fora do ar não pode desviar tráfego do Stooq junto."""
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is True
    assert provider_health.is_open("stooq") is False


def test_status_reports_open_state():
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    st = provider_health.status()["yfinance"]
    assert st["open_now"] is True
    assert st["seconds_until_close"] > 0
    assert st["consecutive_failures"] == provider_health.FAILURE_THRESHOLD


def test_reset_clears_one_provider():
    provider_health.record_failure("yfinance")
    provider_health.record_failure("stooq")
    provider_health.reset("yfinance")
    assert "yfinance" not in provider_health.status()
    assert "stooq" in provider_health.status()


def test_health_file_unreadable_fails_open(monkeypatch, tmp_path):
    """Estado corrompido não pode derrubar quem chama — na pior hipótese o
    breaker esquece o que sabia e a próxima chamada tenta a rede de novo."""
    ruim = tmp_path / "corrompido.json"
    ruim.write_text("isto não é json")
    monkeypatch.setattr(provider_health, "_PATH", str(ruim))
    assert provider_health.is_open("yfinance") is False
    provider_health.record_failure("yfinance")  # não pode lançar


# ── cadeia de fallback ──────────────────────────────────────────────────────

def _fake_df(prices):
    idx = pd.date_range("2026-08-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices,
        "Close": prices, "Volume": [1_000_000] * len(prices),
    }, index=idx)


@pytest.fixture
def _yf_morto(monkeypatch):
    monkeypatch.setattr(mdp, "_yf_history_with_retry", lambda *a, **k: None)


def test_history_prefers_yfinance(monkeypatch):
    monkeypatch.setattr(mdp, "_yf_history_with_retry", lambda *a, **k: _fake_df([10.0, 11.0]))
    monkeypatch.setattr(mdp.hist_cache, "guardar", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp.stooq_provider, "fetch_daily_history",
        lambda *a, **k: pytest.fail("não deveria chamar Stooq com yfinance vivo"),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "yfinance"
    assert result.warnings == []


def test_history_uses_cache_within_ttl_before_anything_else(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: _fake_df([20.0, 21.0]))
    monkeypatch.setattr(
        mdp, "_load_stale_cache",
        lambda *a, **k: pytest.fail("cache dentro do TTL deveria ter servido"),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.source == "yfinance_cache"
    assert result.is_stale is False


def test_history_uses_stale_cache_before_stooq(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([50.0, 51.0]))
    monkeypatch.setattr(mdp, "_conferir_cache_vencido", lambda *a, **k: None)

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "cache_stale"
    assert result.is_stale is True


def test_history_falls_back_to_stooq_when_yfinance_and_cache_fail(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp.stooq_provider, "fetch_daily_history",
        lambda ticker, period: _fake_df([100.0, 101.0, 102.0]),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "stooq"
    assert any("Stooq" in w for w in result.warnings)


def test_history_returns_none_source_when_everything_fails(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(mdp.stooq_provider, "fetch_daily_history", lambda *a, **k: None)

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert not result.ok
    assert result.source == "none"
    assert result.df is None


def test_history_skips_yfinance_when_breaker_open(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(
        mdp, "_yf_history_with_retry",
        lambda *a, **k: pytest.fail("disjuntor aberto: não deveria tentar a rede"),
    )
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: _fake_df([30.0]))

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.source == "yfinance_cache"
    assert any("cooldown" in w for w in result.warnings)


# ── checagem cruzada ────────────────────────────────────────────────────────

def test_cross_check_flags_large_divergence():
    warnings: list[str] = []
    mdp._cross_check_last_close("NVDA", _fake_df([100.0]), _fake_df([110.0]), warnings)
    assert any("Divergência" in w for w in warnings)


def test_cross_check_silent_within_tolerance():
    warnings: list[str] = []
    mdp._cross_check_last_close("NVDA", _fake_df([100.0]), _fake_df([100.5]), warnings)
    assert warnings == []


def test_cross_check_silent_without_reference():
    """Sem segunda fonte não há o que comparar — e "não sei" nunca vira aviso
    de divergência."""
    warnings: list[str] = []
    mdp._cross_check_last_close("NVDA", None, _fake_df([100.0]), warnings)
    assert warnings == []


def test_stale_cache_is_cross_checked_against_stooq(monkeypatch, _yf_morto):
    """O caso que o plano original deixava passar: a comparação precisa rodar
    no ramo do cache vencido, que é onde as duas pontas existem. No ramo do
    Stooq o cache é sempre None por construção, e o cross-check nunca rodava."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([100.0]))
    monkeypatch.setattr(
        mdp.stooq_provider, "fetch_daily_history", lambda *a, **k: _fake_df([120.0]),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.source == "cache_stale"
    assert any("Divergência" in w for w in result.warnings)


def test_stale_cache_served_even_if_cross_check_source_fails(monkeypatch, _yf_morto):
    """Stooq fora do ar significa "sem segunda opinião", não "sem dado"."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([100.0]))
    monkeypatch.setattr(
        mdp.stooq_provider, "fetch_daily_history",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rede fora")),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "cache_stale"
    assert not any("Divergência" in w for w in result.warnings)


# ── cotação ─────────────────────────────────────────────────────────────────

def test_quote_falls_back_to_stooq_marked_as_delayed(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(
        mdp.stooq_provider, "fetch_last_close",
        lambda *a, **k: {"price": 10.0, "asOf": "2026-08-14", "previousClose": 9.0,
                         "change": 1.0, "changePct": 11.1, "volume": 1000},
    )

    result = mdp.get_quote("NVDA")

    assert result.source == "stooq_eod"
    # is_delayed é o que impede a UI de mostrar fechamento de ontem como
    # preço ao vivo — o ponto inteiro do fallback de cotação.
    assert result.is_delayed is True
    assert any("atrasado" in w for w in result.warnings)


def test_quote_returns_none_when_all_sources_fail(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(mdp.stooq_provider, "fetch_last_close", lambda *a, **k: None)

    result = mdp.get_quote("NVDA")

    assert result.quote is None
    assert result.source == "none"


# ── símbolos do Stooq ───────────────────────────────────────────────────────

def test_stooq_symbol_normalization():
    from agent.stooq_provider import _stooq_symbol
    assert _stooq_symbol("NVDA") == "nvda.us"
    assert _stooq_symbol("BRK.B") == "brk-b.us"
    assert _stooq_symbol("  msft ") == "msft.us"
