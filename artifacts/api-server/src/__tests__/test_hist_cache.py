"""Cache em disco do histórico diário, compartilhado entre processos.

Cada ciclo de 5 minutos baixava o mesmo 6mo dos mesmos tickers em processos
diferentes que não se enxergam (o cache do market_alerts é um dict em memória).
Produção 04/08: "$NVDA: possibly delisted; no price data found" para NVDA, AVGO,
MRVL, ARM e HCC -- assinatura de bloqueio do Yahoo por volume, não deslistagem.

Os testes aqui separam duas famílias de risco:
  - o cache servir dado ERRADO (pior que não existir);
  - o cache quebrar e derrubar a chamada (também pior que não existir).
"""
import os
import time

import pandas as pd
import pytest

from agent import hist_cache


@pytest.fixture(autouse=True)
def dir_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(hist_cache, "_DIR", str(tmp_path / "hist"))


def _serie(n: int = 5, base: float = 100.0) -> pd.DataFrame:
    """DataFrame com a mesma forma do que o yfinance devolve: índice de datas
    COM timezone e colunas float."""
    idx = pd.date_range("2026-08-01", periods=n, freq="D", tz="America/New_York")
    return pd.DataFrame(
        {"Open": [base + i for i in range(n)],
         "Close": [base + i + 0.5 for i in range(n)],
         "Volume": [1_000_000 + i for i in range(n)]},
        index=idx,
    )


# ── O que NÃO pode ser cacheado ───────────────────────────────────────────────


def test_periodo_curto_nunca_e_cacheado():
    """5d e 1d carregam a variação DO DIA, que vai direto pro relatório.
    Trocar chamada de rede por número velho ali é bem pior que a chamada."""
    for periodo in ("1d", "5d", "1mo"):
        assert hist_cache.cacheavel(periodo) is False
        hist_cache.guardar("NVDA", periodo, _serie())
        assert hist_cache.carregar("NVDA", periodo) is None


def test_intradiario_nunca_e_cacheado():
    """Cachear 1m mascararia justamente o pico que o checker procura."""
    assert hist_cache.cacheavel("6mo", interval="1m") is False
    assert hist_cache.cacheavel("6mo", interval="5m") is False
    hist_cache.guardar("NVDA", "6mo", _serie(), interval="1m")
    assert hist_cache.carregar("NVDA", "6mo", interval="1m") is None


def test_periodo_longo_e_cacheado():
    for periodo in ("3mo", "6mo", "1y", "max"):
        assert hist_cache.cacheavel(periodo) is True


# ── Fidelidade do dado ────────────────────────────────────────────────────────


def test_round_trip_preserva_timezone_e_dtypes():
    """Pickle e não JSON: a data do último candle decide "hoje" (playbook §6),
    e to_json/read_json não devolve DatetimeIndex com tz fielmente."""
    original = _serie()
    hist_cache.guardar("NVDA", "6mo", original)
    lido = hist_cache.carregar("NVDA", "6mo")

    pd.testing.assert_frame_equal(original, lido)
    assert str(lido.index.tz) == "America/New_York"
    assert lido["Close"].dtype == original["Close"].dtype


def test_auto_adjust_faz_parte_da_chave():
    """market_alerts busca com auto_adjust=False e get_technicals com True --
    séries DIFERENTES (o ajustado desconta dividendos e splits). Uma chave que
    ignorasse isso serviria a série errada sem erro nenhum, e o defeito só
    apareceria no número final."""
    bruta = _serie(base=100.0)
    ajustada = _serie(base=95.0)

    hist_cache.guardar("NVDA", "6mo", bruta, auto_adjust=False)
    hist_cache.guardar("NVDA", "6mo", ajustada, auto_adjust=True)

    assert hist_cache.carregar("NVDA", "6mo", auto_adjust=False)["Open"].iloc[0] == 100.0
    assert hist_cache.carregar("NVDA", "6mo", auto_adjust=True)["Open"].iloc[0] == 95.0


def test_tickers_diferentes_nao_se_misturam():
    hist_cache.guardar("NVDA", "6mo", _serie(base=200.0))
    hist_cache.guardar("HCC", "6mo", _serie(base=80.0))
    assert hist_cache.carregar("NVDA", "6mo")["Open"].iloc[0] == 200.0
    assert hist_cache.carregar("HCC", "6mo")["Open"].iloc[0] == 80.0


# ── Expiração ─────────────────────────────────────────────────────────────────


def test_expira_pelo_ttl(monkeypatch):
    hist_cache.guardar("NVDA", "6mo", _serie())
    assert hist_cache.carregar("NVDA", "6mo") is not None

    monkeypatch.setattr(hist_cache, "TTL_S", 0)
    time.sleep(0.01)
    assert hist_cache.carregar("NVDA", "6mo") is None


def test_sem_arquivo_devolve_none():
    assert hist_cache.carregar("NUNCA_VISTO", "6mo") is None


# ── Falha aberta ──────────────────────────────────────────────────────────────


def test_arquivo_corrompido_nao_levanta():
    """Cache quebrado tem que virar 'sem cache', nunca uma exceção que sobe pro
    checker -- foi exatamente esse o custo do handler de SIGTERM com print()."""
    hist_cache.guardar("NVDA", "6mo", _serie())
    caminho = hist_cache._caminho("NVDA", "6mo", False, "1d")
    with open(caminho, "wb") as f:
        f.write(b"isso nao e um pickle")
    assert hist_cache.carregar("NVDA", "6mo") is None


def test_diretorio_impossivel_nao_levanta(monkeypatch):
    monkeypatch.setattr(hist_cache, "_DIR", "/proc/nao/da/pra/escrever")
    hist_cache.guardar("NVDA", "6mo", _serie())  # não pode levantar
    assert hist_cache.carregar("NVDA", "6mo") is None


def test_dataframe_vazio_nao_e_gravado():
    """Resposta vazia do Yahoo (o caso "possibly delisted") não pode virar cache
    -- congelaria o vazio pelo TTL inteiro em vez de tentar de novo."""
    hist_cache.guardar("NVDA", "6mo", pd.DataFrame())
    assert hist_cache.carregar("NVDA", "6mo") is None


def test_grava_de_forma_atomica():
    """Dois processos do mesmo ciclo podem gravar a mesma chave ao mesmo tempo;
    sem rename atômico um leitor pegaria pickle pela metade."""
    hist_cache.guardar("NVDA", "6mo", _serie())
    sobras = [f for f in os.listdir(hist_cache._DIR) if f.endswith(".tmp")]
    assert sobras == []
