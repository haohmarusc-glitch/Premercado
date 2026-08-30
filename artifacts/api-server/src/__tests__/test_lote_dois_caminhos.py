"""Os DOIS caminhos de get_daily_closes_batch têm de devolver a mesma coisa.

A função tem um caminho feliz (um `yf.download` em lote) e um de fallback
(a cadeia por ticker, quando o lote falha ou o disjuntor está aberto). Até
30/08/2026 eles divergiam em duas coisas, e nenhuma aparecia no retorno:

  barra sem Close   o fallback passa por `get_daily_history`, que filtra na
                    FACHADA -- e a fachada existe exatamente para isso, com o
                    comentário "um filtro por ramo dependeria de quem
                    adiciona o sexto lembrar de repetir". O caminho feliz
                    devolvia `data["Close"]` cru, com a barra do dia ainda
                    sem negócio dentro, que vira NaN no `iloc[-1]` de quem
                    consome.

  fuso do índice    o laço do fallback faz `tz_localize(None)`; o caminho
                    feliz devolvia o índice como o yfinance mandou.

Ou seja: a MESMA chamada devolvia séries de comprimento e dtype de índice
diferentes conforme o Yahoo estivesse de pé. Quem consome (vol e beta do
Painel de Cenários, matriz de correlação do risco) não tem como perceber --
os números continuam plausíveis, que é a assinatura do §2b do playbook.

O teste é de ACORDO entre os caminhos, não de valor absoluto: alimenta os
dois com o mesmo dado e exige o mesmo resultado.
"""
import numpy as np
import pandas as pd
import pytest

from agent import hist_cache
from agent import market_data_provider as mdp
from agent import provider_health

_TICKERS = ["NVDA", "MU"]
_COLUNAS = ["Open", "High", "Low", "Close", "Volume"]


@pytest.fixture(autouse=True)
def _isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_health, "_PATH", str(tmp_path / "health.json"))
    monkeypatch.setattr(hist_cache, "guardar", lambda *a, **k: None)
    monkeypatch.setattr(hist_cache, "carregar", lambda *a, **k: None)
    yield


def _ohlcv(ticker: str, n: int = 40) -> pd.DataFrame:
    """OHLCV com índice COM fuso e uma barra incompleta no fim.

    As duas armadilhas de uma vez: o yfinance devolve o índice tz-aware e
    inclui a barra do dia corrente sem Close antes do fechamento.
    """
    idx = pd.date_range("2026-07-01", periods=n, freq="B", tz="America/New_York")
    rng = np.random.default_rng(abs(hash(ticker)) % 2**32)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1,
         "Close": close, "Volume": np.full(n, 1_000_000)},
        index=idx,
    )
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan   # a barra de hoje
    return df


def _download_multiindex(tickers) -> pd.DataFrame:
    """Mesma forma do yf.download em lote: colunas MultiIndex campo × ticker."""
    partes = {}
    for t in tickers:
        for c in _COLUNAS:
            partes[(c, t)] = _ohlcv(t)[c]
    data = pd.DataFrame(partes)
    data.columns = pd.MultiIndex.from_tuples(data.columns)
    return data


def _pelo_caminho_feliz(monkeypatch):
    monkeypatch.setattr(mdp.yf, "download",
                        lambda *a, **k: _download_multiindex(_TICKERS))
    return mdp.get_daily_closes_batch(_TICKERS, "3mo", auto_adjust=True)


def _pelo_fallback(monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("Yahoo fora do ar")
    monkeypatch.setattr(mdp.yf, "download", _explode)
    monkeypatch.setattr(mdp, "_yf_history_with_retry",
                        lambda ticker, period, auto_adjust: _ohlcv(ticker))
    return mdp.get_daily_closes_batch(_TICKERS, "3mo", auto_adjust=True)


def test_caminho_feliz_descarta_a_barra_incompleta(monkeypatch):
    """O defeito principal.

    Escopo exato, porque é fácil exagerar: `sem_barra_incompleta` descarta
    linha com Close NaN, não a barra parcial do meio do pregão (essa tem
    Close de verdade e passa nos dois caminhos, de propósito). O que ela pega
    é a barra que o yfinance devolve ANTES de haver negócio no dia -- e é
    essa que virava NaN no `iloc[-1]` de quem consome, o incidente de
    18/08/2026 que derrubou o JSON da Técnica inteiro."""
    r = _pelo_caminho_feliz(monkeypatch)
    assert r.ok
    for t in _TICKERS:
        assert r.closes[t].notna().all(), (
            f"{t}: barra incompleta sobreviveu ao caminho feliz")
    assert len(r.closes) == 39, "a barra de hoje devia ter saído (40 -> 39)"


def test_caminho_feliz_normaliza_o_fuso_do_indice(monkeypatch):
    """A cadeia por ticker faz tz_localize(None); o lote não fazia. Índices de
    dtype diferente não alinham entre si num `concat`/`join`."""
    r = _pelo_caminho_feliz(monkeypatch)
    assert r.closes.index.tz is None


def test_os_dois_caminhos_devolvem_a_mesma_serie(monkeypatch):
    """O teste que amarra os dois. Mesmo dado de entrada, mesmo resultado --
    a saúde do Yahoo não pode mudar a SEMÂNTICA do que volta, só a origem."""
    feliz = _pelo_caminho_feliz(monkeypatch)
    fallback = _pelo_fallback(monkeypatch)

    assert feliz.ok and fallback.ok
    assert list(feliz.closes.columns) == list(fallback.closes.columns)
    pd.testing.assert_frame_equal(
        feliz.closes, fallback.closes, check_freq=False,
        obj="closes do lote x closes da cadeia por ticker")

    # A origem é "yfinance" nos dois, e está certo: a cadeia por ticker também
    # falou com o yfinance, só que um símbolo por vez. O que separa os dois é o
    # aviso -- é ele que conta ao chamador que o lote caiu.
    assert set(feliz.fontes.values()) == {"yfinance"}
    assert set(fallback.fontes.values()) == {"yfinance"}
    assert not feliz.warnings
    assert any("cadeia por ticker" in w for w in fallback.warnings)


def test_lote_so_de_nan_nao_vira_ok(monkeypatch):
    """`ok` é `closes is not None and not closes.empty`, e um frame cheio de
    NaN passa nos dois. O chamador recebia ok=True e uma matriz inútil; agora
    cai na cadeia por ticker, que ao menos tenta o cache."""
    def _tudo_nan(*a, **k):
        data = _download_multiindex(_TICKERS)
        for t in _TICKERS:
            data[("Close", t)] = np.nan
        return data
    monkeypatch.setattr(mdp.yf, "download", _tudo_nan)
    monkeypatch.setattr(mdp, "_yf_history_with_retry", lambda *a, **k: None)
    r = mdp.get_daily_closes_batch(_TICKERS, "3mo", auto_adjust=True)
    assert not r.ok
    assert any("utilizável" in w for w in r.warnings)
