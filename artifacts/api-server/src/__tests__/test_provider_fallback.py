"""
Testes de provider_health.py + market_data_provider.py + alpha_vantage_provider.py.

Convenção do repo: pytest com monkeypatch em vez de mock de biblioteca, mesmo
padrão simples do restante da suíte. Nenhum teste toca a rede.

Import de PACOTE (`from agent import ...`), nunca inserindo `src/agent` no
sys.path: existe um `agent.py` além do pacote `agent/`, e colocar o diretório
do pacote no path faz o nome `agent` resolver para o módulo solto, quebrando
`from agent.x import y` em qualquer teste coletado depois. O conftest.py já
põe `src/` no path.
"""
import datetime
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
    """Yahoo fora do ar não pode abrir o disjuntor da fonte externa junto."""
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    assert provider_health.is_open("yfinance") is True
    assert provider_health.is_open("alphavantage") is False


def test_status_reports_open_state():
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    st = provider_health.status()["yfinance"]
    assert st["open_now"] is True
    assert st["seconds_until_close"] > 0
    assert st["consecutive_failures"] == provider_health.FAILURE_THRESHOLD


def test_reset_clears_one_provider():
    provider_health.record_failure("yfinance")
    provider_health.record_failure("alphavantage")
    provider_health.reset("yfinance")
    assert "yfinance" not in provider_health.status()
    assert "alphavantage" in provider_health.status()


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
        mdp.alpha_vantage_provider, "fetch_daily_history",
        lambda *a, **k: pytest.fail("não deveria chamar Alpha Vantage com yfinance vivo"),
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


def test_history_uses_stale_cache_before_fonte_externa(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([50.0, 51.0]))
    monkeypatch.setattr(mdp, "_conferir_cache_vencido", lambda *a, **k: None)

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "cache_stale"
    assert result.is_stale is True


def test_history_falls_back_to_alpha_vantage_when_yfinance_and_cache_fail(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_daily_history",
        lambda ticker, period: _fake_df([100.0, 101.0, 102.0]),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "alphavantage"
    assert any("Alpha Vantage" in w for w in result.warnings)


def test_history_returns_none_source_when_everything_fails(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(mdp.alpha_vantage_provider, "fetch_daily_history", lambda *a, **k: None)

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


def test_stale_cache_is_cross_checked_against_fonte_externa(monkeypatch, _yf_morto):
    """O caso que o plano original deixava passar: a comparação precisa rodar
    no ramo do cache vencido, que é onde as duas pontas existem. No ramo do
    Alpha Vantage o cache é sempre None por construção, e o cross-check nunca rodava."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([100.0]))
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_daily_history", lambda *a, **k: _fake_df([120.0]),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.source == "cache_stale"
    assert any("Divergência" in w for w in result.warnings)


def test_stale_cache_served_even_if_cross_check_source_fails(monkeypatch, _yf_morto):
    """Alpha Vantage fora do ar significa "sem segunda opinião", não "sem dado"."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([100.0]))
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_daily_history",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rede fora")),
    )

    result = mdp.get_daily_history("NVDA", period="3mo")

    assert result.ok
    assert result.source == "cache_stale"
    assert not any("Divergência" in w for w in result.warnings)


# ── cotação ─────────────────────────────────────────────────────────────────

def test_quote_falls_back_to_alpha_vantage_marked_as_delayed(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_last_close",
        lambda *a, **k: {"price": 10.0, "asOf": "2026-08-14", "previousClose": 9.0,
                         "change": 1.0, "changePct": 11.1, "volume": 1000},
    )

    result = mdp.get_quote("NVDA")

    assert result.source == "alphavantage_eod"
    # is_delayed é o que impede a UI de mostrar fechamento de ontem como
    # preço ao vivo — o ponto inteiro do fallback de cotação.
    assert result.is_delayed is True
    assert any("atrasado" in w for w in result.warnings)


def test_quote_returns_none_when_all_sources_fail(monkeypatch):
    for _ in range(provider_health.FAILURE_THRESHOLD):
        provider_health.record_failure("yfinance")
    monkeypatch.setattr(mdp.alpha_vantage_provider, "fetch_last_close", lambda *a, **k: None)

    result = mdp.get_quote("NVDA")

    assert result.quote is None
    assert result.source == "none"




# ── orçamento diário compartilhado com o feed de notícias ───────────────────

def test_orcamento_autoriza_ate_o_limite():
    assert all(provider_health.consumir_orcamento_diario("alphavantage", 3) for _ in range(3))
    assert provider_health.consumir_orcamento_diario("alphavantage", 3) is False


def test_orcamento_conta_o_consumo():
    provider_health.consumir_orcamento_diario("alphavantage", 5)
    provider_health.consumir_orcamento_diario("alphavantage", 5)
    assert provider_health.orcamento_usado("alphavantage") == 2


def test_orcamento_zera_na_virada_do_dia():
    for _ in range(3):
        provider_health.consumir_orcamento_diario("alphavantage", 3, hoje="2026-08-15")
    assert provider_health.consumir_orcamento_diario("alphavantage", 3, hoje="2026-08-15") is False
    # Dia novo, cota nova.
    assert provider_health.consumir_orcamento_diario("alphavantage", 3, hoje="2026-08-16") is True


def test_orcamento_conta_mesmo_com_dia_diferente_do_relogio():
    """Regressão do bug que quebrou o CI à meia-noite UTC.

    A primeira versão gravava o dia derivando do relógio real e comparava com
    o dia PASSADO por parâmetro. Bastava os dois discordarem — teste rodando
    às 00:00 UTC com `hoje` de ontem — para o contador zerar a cada chamada e
    o teto nunca segurar nada. Um dia qualquer que não seja hoje tem que
    contar igual.
    """
    ontem = (today_brt() - datetime.timedelta(days=1)).isoformat()
    for _ in range(2):
        assert provider_health.consumir_orcamento_diario("alphavantage", 2, hoje=ontem) is True
    assert provider_health.consumir_orcamento_diario("alphavantage", 2, hoje=ontem) is False


def test_orcamento_usa_o_dia_em_brt_por_padrao(monkeypatch):
    """Sem `hoje` explícito, o dia é o de Brasília — `date.today()` cru usaria
    o fuso do processo (UTC no container) e viraria 3h cedo demais, zerando a
    cota no meio da noite de quem opera aqui."""
    provider_health.consumir_orcamento_diario("alphavantage", 5)
    st = provider_health._load()["_orcamento:alphavantage"]  # noqa: SLF001
    assert st.orcamento_dia == today_brt().isoformat()


def test_orcamento_e_por_provedor():
    provider_health.consumir_orcamento_diario("alphavantage", 1)
    assert provider_health.consumir_orcamento_diario("alphavantage", 1) is False
    assert provider_health.consumir_orcamento_diario("outro", 1) is True


def test_orcamento_nao_polui_o_status_dos_provedores():
    """A contagem vive sob uma chave própria (`_orcamento:...`) — se vazasse
    para o status, um provedor saudável apareceria com falhas acumuladas."""
    provider_health.consumir_orcamento_diario("alphavantage", 5)
    assert "alphavantage" not in provider_health.status()


def test_orcamento_falha_aberta_autoriza(monkeypatch):
    """Contador quebrado não pode custar o dado: perder histórico por causa de
    um arquivo ilegível seria pior que estourar a cota."""
    monkeypatch.setattr(provider_health, "_load", lambda: (_ for _ in ()).throw(OSError("disco")))
    assert provider_health.consumir_orcamento_diario("alphavantage", 1) is True


# ── Alpha Vantage: 200 OK que não é dado ────────────────────────────────────

class _RespostaFalsa:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _av(monkeypatch, payload, *, chave="fake"):
    from agent import alpha_vantage_provider as av
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", chave)
    monkeypatch.setattr(av.SESSION, "get", lambda *a, **k: _RespostaFalsa(payload))
    return av


# Datas RELATIVAS a hoje, não fixas: `fetch_daily_history` corta por período
# (hoje - N dias), então uma série com data cravada passaria agora e começaria
# a devolver vazio sozinha quando o calendário andasse — o mesmo tipo de bomba
# de data congelada que já mordeu o radar.
from agent.brt import today_brt  # noqa: E402

_ONTEM = (today_brt() - datetime.timedelta(days=1)).isoformat()
_ANTEONTEM = (today_brt() - datetime.timedelta(days=2)).isoformat()

_SERIE_OK = {
    "Time Series (Daily)": {
        _ONTEM: {"1. open": "100.0", "2. high": "102.0", "3. low": "99.0",
                 "4. close": "101.0", "5. volume": "1000000"},
        _ANTEONTEM: {"1. open": "98.0", "2. high": "100.0", "3. low": "97.0",
                     "4. close": "99.0", "5. volume": "900000"},
    }
}


def test_av_parseia_serie_diaria(monkeypatch):
    av = _av(monkeypatch, _SERIE_OK)
    df = av.fetch_daily_history("AAPL", period="1y")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    # Ordem cronológica: o JSON da API vem do mais recente pro mais antigo.
    assert df.index[0] < df.index[-1]
    assert float(df["Close"].iloc[-1]) == 101.0


def test_av_converte_numeros_e_nao_deixa_string(monkeypatch):
    av = _av(monkeypatch, _SERIE_OK)
    df = av.fetch_daily_history("AAPL", period="1y")
    assert pd.api.types.is_numeric_dtype(df["Close"])


@pytest.mark.parametrize("aviso", [
    {"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is..."},
    {"Information": "premium endpoint"},
    {"Error Message": "Invalid API call"},
    {},
])
def test_av_trata_200_sem_serie_como_falha(monkeypatch, aviso):
    """A Alpha Vantage responde 200 com JSON de aviso quando a cota estoura ou
    a chave é inválida. Devolver DataFrame vazio aqui seria confundir "estamos
    cegos" com "o mercado não abriu"."""
    av = _av(monkeypatch, aviso)
    assert av.fetch_daily_history("AAPL", period="1y") is None


def test_av_sem_chave_nao_chama_a_rede(monkeypatch):
    from agent import alpha_vantage_provider as av
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "")
    monkeypatch.setattr(
        av.SESSION, "get",
        lambda *a, **k: pytest.fail("sem chave não deveria tocar a rede"),
    )
    assert av.fetch_daily_history("AAPL") is None


def test_av_respeita_o_orcamento(monkeypatch):
    av = _av(monkeypatch, _SERIE_OK)
    monkeypatch.setattr(av, "_ORCAMENTO_DIARIO", 1)
    assert av.fetch_daily_history("AAPL", period="1y") is not None
    # Segunda chamada no mesmo dia: cota esgotada, sem ir à rede.
    assert av.fetch_daily_history("AAPL", period="1y") is None


def test_av_last_close_calcula_variacao(monkeypatch):
    av = _av(monkeypatch, _SERIE_OK)
    q = av.fetch_last_close("AAPL")
    assert q["price"] == 101.0
    assert q["previousClose"] == 99.0
    assert q["change"] == 2.0
    assert round(q["changePct"], 2) == 2.02
    assert q["asOf"] == _ONTEM


# ── corte da fonte externa para série ajustada ──────────────────────────────

def test_permitir_externa_false_para_no_cache_vencido(monkeypatch, _yf_morto):
    """Quem pede série AJUSTADA não pode receber "as traded": um split na
    janela viraria degrau de preço, e RSI/médias sairiam com um salto que
    nunca existiu. O cache vencido continua valendo — foi gravado do yfinance,
    já ajustado."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: _fake_df([50.0]))
    monkeypatch.setattr(mdp, "_conferir_cache_vencido", lambda *a, **k: None)

    r = mdp.get_daily_history("NVDA", period="6mo", auto_adjust=True, permitir_externa=False)

    assert r.ok
    assert r.source == "cache_stale"


def test_permitir_externa_false_nao_chama_a_fonte_externa(monkeypatch, _yf_morto):
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_daily_history",
        lambda *a, **k: pytest.fail("série ajustada não pode usar a fonte externa"),
    )

    r = mdp.get_daily_history("NVDA", period="6mo", auto_adjust=True, permitir_externa=False)

    assert not r.ok
    assert r.source == "none"
    assert any("as traded" in w for w in r.warnings)


def test_permitir_externa_true_e_o_padrao(monkeypatch, _yf_morto):
    """O corte é opt-in: quem não pede continua com a cadeia inteira."""
    monkeypatch.setattr(mdp.hist_cache, "carregar", lambda *a, **k: None)
    monkeypatch.setattr(mdp, "_load_stale_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        mdp.alpha_vantage_provider, "fetch_daily_history", lambda *a, **k: _fake_df([100.0]),
    )

    r = mdp.get_daily_history("NVDA", period="6mo")

    assert r.source == "alphavantage"
