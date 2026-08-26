"""
Auditoria travada em teste: ATR, MACD e VWAP têm implementações duplicadas
neste repo (mesma razão de sempre -- os scripts `get_*.py` rodam por spawn e
não importam do pacote). Diferente do RSI, que tinha QUATRO cópias e duas
contas distintas (Wilder × Cutler, ver #298/#303), essas três estavam
consistentes quando auditadas em 17/08/2026.

Este arquivo existe para que continuem. O RSI mostrou que "está consistente
hoje" não é garantia de nada: a divergência entrou sem ninguém notar porque
os dois números eram plausíveis e ninguém comparava os painéis lado a lado.

O que cada teste fixa:

  ATR   -- True Range = max(h-l, |h-cAnt|, |l-cAnt|), suavizado por MÉDIA
           SIMPLES de 14. Note que NÃO é o ATR clássico de Wilder (que usa
           ewm alpha=1/14); é a variante SMA. Está assim nas quatro cópias,
           e é a consistência que importa -- trocar para Wilder mexeria nos
           limiares de alerta de todo ticker de uma vez.
  MACD  -- ewm(span=12/26/9).mean() com o `adjust=True` padrão do pandas,
           igual nas quatro cópias.
  VWAP  -- as duas cópias de sessão (tools/get_technicals) são idênticas.
           confluence_engine.vwap_rolling é OUTRO indicador de propósito, e
           está nomeado e documentado como tal -- exatamente o que a §2b do
           playbook manda fazer quando a variante é intencional.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_indicadores_consistentes.py -v
"""
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

from agent import cache as cache_module
from agent import ciclo_volatilidade as cv
from agent import tools

_AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "agent"

# confluence_engine usa imports planos (`from security import ...`), então
# precisa de src/agent no sys.path -- mesma convenção de
# test_confluence_engine_fallback.py. O conftest já fixou o PACOTE `agent` em
# sys.modules antes disto, então inserir o diretório aqui não sequestra o nome.
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)


def _ohlc(n: int = 120) -> pd.DataFrame:
    """OHLCV determinístico com gaps de verdade entre fechamento e abertura --
    sem gap, |h-cAnt| e |l-cAnt| nunca superam h-l e o True Range degenera
    para o range simples, o que faria o teste passar sem testar nada."""
    rng = np.random.default_rng(20260817)
    idx = pd.date_range("2026-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.cumsum(rng.normal(0.3, 2.5, n)), index=idx).clip(lower=5)
    gap = pd.Series(rng.normal(0, 1.5, n), index=idx)
    abertura = (close.shift(1).fillna(close.iloc[0]) + gap)
    high = pd.concat([abertura, close], axis=1).max(axis=1) + rng.uniform(0.1, 2.0, n)
    low = pd.concat([abertura, close], axis=1).min(axis=1) - rng.uniform(0.1, 2.0, n)
    return pd.DataFrame(
        {"Open": abertura, "High": high, "Low": low, "Close": close,
         "Volume": pd.Series(rng.uniform(2e7, 3e7, n), index=idx)},
        index=idx,
    )


def _tr_referencia(df: pd.DataFrame) -> pd.Series:
    """A conta escrita em market_alerts, risk_manager e tools -- as três
    idênticas. Serve de referência para as outras cópias."""
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [df["High"] - df["Low"],
         (df["High"] - prev_close).abs(),
         (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def _mock_yf(monkeypatch, hist: pd.DataFrame):
    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, *args, **kwargs):
            return pd.DataFrame() if kwargs.get("interval") == "5m" else hist

    monkeypatch.setattr(tools.yf, "Ticker", FakeTicker)


# ── ATR ─────────────────────────────────────────────────────────────────────

def test_true_range_do_ciclo_volatilidade_bate_com_a_conta_das_outras_copias():
    """ciclo_volatilidade._true_range é escrito em numpy; as outras três em
    pandas. Formulações diferentes do MESMO True Range -- é justamente o tipo
    de par que diverge quando alguém "melhora" um dos lados."""
    df = _ohlc()
    esperado = _tr_referencia(df).to_numpy(dtype=float)
    obtido = cv._true_range(df)

    # A primeira barra não tem fechamento anterior: pandas devolve NaN no
    # |h-cAnt|/|l-cAnt| e o max ignora, sobrando h-l; o numpy faz o mesmo via
    # nanmax. Compara do índice 1 pra frente, onde as duas são plenamente
    # definidas.
    assert obtido[1:] == pytest.approx(esperado[1:], rel=1e-9)


def test_atr_de_tools_e_media_simples_de_14_do_true_range(monkeypatch):
    df = _ohlc()
    _mock_yf(monkeypatch, df)
    out = tools.get_technical_indicators("NBIS", period="6mo")
    assert "error" not in out, out

    esperado = round(float(_tr_referencia(df).rolling(14).mean().iloc[-1]), 2)
    assert out["atr_14"] == pytest.approx(esperado, abs=0.01)


def test_nenhuma_copia_de_atr_migrou_para_suavizacao_de_wilder():
    """Guarda de drift, no espírito de test_nenhum_modulo_do_agente_usa_rsi_de
    _cutler: as quatro cópias usam média simples. Se alguém trocar UMA para
    ewm(alpha=1/14) -- o ATR "correto" de Wilder -- volta a haver dois ATRs
    com o mesmo nome, que foi exatamente o bug do RSI.

    Migrar para Wilder é uma decisão legítima; só não pode ser feita numa
    cópia só. Se for feita nas quatro, atualize este teste junto."""
    culpados = []
    for arquivo in sorted(_AGENT_DIR.rglob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        if "true_range.ewm(" in texto or "tr.ewm(" in texto:
            culpados.append(str(arquivo.relative_to(_AGENT_DIR)))
    assert not culpados, (
        "ATR com suavização de Wilder em: " + ", ".join(culpados)
        + " -- as outras cópias usam média simples de 14; migre todas ou nenhuma"
    )


# ── MACD ────────────────────────────────────────────────────────────────────

def test_macd_de_tools_bate_com_a_formula_das_outras_copias(monkeypatch):
    """get_trend, get_technicals e backtest calculam o histograma inline com
    esta mesma expressão. Só tools é importável e mockável sem rede; a
    igualdade textual das outras está no teste abaixo."""
    df = _ohlc()
    _mock_yf(monkeypatch, df)
    out = tools.get_technical_indicators("NBIS", period="6mo")

    close = df["Close"]
    linha = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    esperado = round(float((linha - linha.ewm(span=9).mean()).iloc[-1]), 4)
    assert out["macd"]["histogram"] == pytest.approx(esperado, abs=1e-4)


def test_todas_as_copias_de_macd_usam_os_mesmos_spans():
    """12/26/9 em toda cópia. Um span diferente em um dos arquivos daria
    MACDs distintos com o mesmo nome -- o bug do RSI noutra roupa."""
    copias = [f for f in sorted(_AGENT_DIR.rglob("*.py"))
              if "ewm(span=12)" in f.read_text(encoding="utf-8")]
    assert len(copias) >= 3, "esperava várias cópias de MACD; a busca quebrou?"
    for arquivo in copias:
        texto = arquivo.read_text(encoding="utf-8")
        assert "ewm(span=26)" in texto, f"{arquivo.name}: span 26 ausente"
        assert "ewm(span=9)" in texto, f"{arquivo.name}: span 9 (sinal) ausente"


# ── VWAP ────────────────────────────────────────────────────────────────────

def test_vwap_de_sessao_e_identico_nas_duas_copias():
    """tools.py e get_technicals.py calculam o VWAP da SESSÃO (barras de 5min
    de hoje, reseta todo dia). A conta tem que ser literalmente a mesma.

    O frame mudou de `intraday` para `sessao` em 26/08/2026: as barras passam
    por `barras_da_sessao` antes, porque o frame cru pode trazer pré e
    pós-mercado. Uma VWAP ponderada pelo pós-mercado de um dia de balanço não
    é a VWAP do pregão -- foi a mesma contaminação que produziu o rvol 8,89
    da NVDA, e ela vinha deste mesmo frame.
    """
    conta = "(typical_price * intraday_volume).sum() / vol_sum"
    for nome in ("tools.py", "get_technicals.py"):
        texto = (_AGENT_DIR / nome).read_text(encoding="utf-8")
        assert conta in texto, f"{nome}: VWAP de sessão mudou de fórmula"
        assert '(sessao["High"] + sessao["Low"] + sessao["Close"]) / 3' in texto
        # A propriedade nova: o preço típico sai das barras FILTRADAS. Sem
        # isto, alguém pode voltar a usar o frame cru sem o teste reclamar,
        # já que as duas cópias continuariam idênticas -- e idênticas erradas.
        assert 'barras_da_sessao(intraday)' in texto, (
            f"{nome}: VWAP voltou a sair do frame cru, com pré/pós-mercado")


def test_vwap_rolling_do_confluence_e_outro_indicador_e_esta_nomeado_assim():
    """confluence_engine.vwap_rolling NÃO é o VWAP de sessão: é janela rolante
    sobre candles diários, para o sinal de fluxo. Divergir é o comportamento
    correto aqui -- o que importa é que o nome diga isso, que é o que a §2b do
    playbook pede quando a variante é intencional."""
    from agent import confluence_engine as ce

    assert hasattr(ce, "vwap_rolling")
    assert not hasattr(ce, "vwap"), (
        "um `vwap` sem qualificador em confluence_engine seria confundido com "
        "o VWAP de sessão de tools/get_technicals"
    )
    assert "janela rolante" in (ce.vwap_rolling.__doc__ or "")


    df = pd.DataFrame({
        "high": [11.0] * 30, "low": [9.0] * 30, "close": [10.0] * 30,
        "volume": [1_000.0] * 30,
    })
    # Preço constante -> VWAP rolante converge para o próprio preço típico.
    assert float(ce.vwap_rolling(df, window=20).iloc[-1]) == pytest.approx(10.0)
