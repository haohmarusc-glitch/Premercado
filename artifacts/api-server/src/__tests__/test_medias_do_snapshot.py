"""
MM50/MM200 do painel Níveis vêm da MESMA série que o painel Técnica.

Incidente real (SNDK, 26/08/2026, TERCEIRA ocorrência): Técnica mostrava MM50
US$ 1624,02 e Níveis US$ 1636,42, para o mesmo papel no mesmo instante. Antes
disso, 106,02 contra 106,85. Sempre ~0,8%, sempre o Níveis acima -- assinatura
de duas DEFINIÇÕES, não de ruído.

    get_technicals.py    close.rolling(50).mean()   -- 50 pregões da série
    get_ticker_snapshot  fi.fifty_day_average       -- campo pronto do Yahoo

O campo do Yahoo é caixa-preta: não dá para saber quantas barras entraram nem
qual ajuste foi aplicado, então não dá para dizer qual dos dois está certo.
Duas respostas para a mesma pergunta na mesma tela é pior que uma resposta
imperfeita.

E havia consequência além da estética: `SMA50_DISTANCIA_ERRADA` compara a
prosa contra o `pct_above_sma50` do agente (que usa `rolling(50)`). Com a
prosa citando o número do painel Níveis, o apontamento sairia contra um texto
CORRETO -- falso positivo, o defeito mais caro que este validador tem.
"""
import sys
import types

import numpy as np
import pandas as pd
import pytest

from agent import get_ticker_snapshot as gts


class _Resultado:
    def __init__(self, df, ok=True, source="yfinance"):
        self.df, self.ok, self.source = df, ok, source


def _serie(n=260, inicio=100.0, passo=1.0):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"Close": [inicio + i * passo for i in range(n)]}, index=idx)


def test_media_sai_da_serie_e_bate_com_o_rolling(monkeypatch):
    df = _serie()
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(df))
    m50, m200, origem, _ate = gts._medias_moveis("SNDK")
    assert origem == "serie"
    # Exatamente o que `get_technicals` calcularia sobre as mesmas barras.
    assert m50 == round(float(df["Close"].rolling(50).mean().iloc[-1]), 2)
    assert m200 == round(float(df["Close"].rolling(200).mean().iloc[-1]), 2)


def test_a_janela_maior_nao_muda_a_mm50(monkeypatch):
    """`rolling(50).iloc[-1]` só olha as últimas 50 barras, então o snapshot
    (2y) e o Técnica (6mo) chegam ao MESMO número -- que é o ponto inteiro
    desta mudança."""
    longa = _serie(500)
    curta = longa.iloc[-126:]          # ~6 meses de pregões
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(longa))
    m50_longa, _, _, _ = gts._medias_moveis("SNDK")
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(curta))
    m50_curta, m200_curta, _, _ = gts._medias_moveis("SNDK")
    assert m50_longa == m50_curta
    assert m200_curta is None, "6mo não tem 200 barras -- é por isso que a MM200 some do Técnica"


def test_barra_do_dia_sem_close_nao_contamina(monkeypatch):
    """O yfinance devolve a barra corrente com Close vazio fora do pregão, e
    ela entra como ÚLTIMA linha. Sem o dropna a média sai NaN."""
    df = _serie()
    limpo = round(float(df["Close"].rolling(50).mean().iloc[-1]), 2)
    df.loc[pd.Timestamp("2026-01-01")] = {"Close": np.nan}
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(df))
    m50, _, origem, _ate = gts._medias_moveis("SNDK")
    assert origem == "serie" and m50 == limpo


def test_flags_da_serie_sao_os_mesmos_do_get_technicals(monkeypatch):
    """auto_adjust=True e permitir_externa=False não são detalhe: a fonte
    externa devolve "as traded", e um desdobramento dentro da janela viraria
    um degrau de preço -- número errado com cara de número certo."""
    visto = {}

    def _espiao(ticker, period, **kw):
        visto.update({"ticker": ticker, "period": period, **kw})
        return _Resultado(_serie())

    monkeypatch.setattr(gts.market_data_provider, "get_daily_history", _espiao)
    gts._medias_moveis("SNDK")
    assert visto["auto_adjust"] is True
    assert visto["permitir_externa"] is False


# ── o fallback existe, mas nunca em silêncio ────────────────────────────────

def _fast_info(m50=1636.42, m200=952.03):
    fi = types.SimpleNamespace(fifty_day_average=m50, two_hundred_day_average=m200)
    return types.SimpleNamespace(fast_info=fi)


def test_serie_fora_do_ar_cai_no_yahoo_marcado(monkeypatch, capsys):
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(None, ok=False, source="sem_rede"))
    monkeypatch.setattr(gts.yf, "Ticker", lambda t: _fast_info())
    m50, m200, origem, _ate = gts._medias_moveis("SNDK")
    assert (m50, m200) == (1636.42, 952.03)
    assert origem == "yahoo", "recusar o dado trocaria 0,8% de imprecisão por um traço"
    assert "medias do fast_info" in capsys.readouterr().err


def test_serie_curta_demais_tambem_cai_marcado(monkeypatch):
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(_serie(20)))
    monkeypatch.setattr(gts.yf, "Ticker", lambda t: _fast_info())
    assert gts._medias_moveis("SNDK")[2] == "yahoo"


def test_as_duas_fontes_fora_do_ar_nao_estouram(monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("sem rede")
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history", _explode)
    monkeypatch.setattr(gts.yf, "Ticker", _explode)
    assert gts._medias_moveis("SNDK") == (None, None, "indisponivel", None)


# ═══ 29/08/2026 — sábado virou "sessão", e o detector apontou atraso falso ══
#
# O `dadosAte` do snapshot nasceu (28/08) carimbando `datetime.now(NY).date()`.
# Num SÁBADO isso deu "2026-08-29" -- uma data sem pregão nenhum. O painel
# Técnica dizia "2026-08-28" (a sexta, sua última barra real), e
# `_defasagem_entre_paineis` apontou um dia de atraso que NÃO existia: os dois
# painéis estavam na mesma sessão, um deles só se datava pelo calendário.
#
# Relógio não sabe de fim de semana nem de feriado. A série sabe, porque ela
# só tem barra onde houve pregão. Regra única para os quatro painéis:
# `dadosAte` é a última barra FECHADA que aquele painel usou.

def _serie_ate(fim: str, n: int = 60):
    """Série de `n` pregões terminando em `fim` -- só dias úteis, como o
    mercado."""
    idx = pd.bdate_range(end=fim, periods=n)
    return pd.DataFrame({"Close": [100.0 + i for i in range(n)]}, index=idx)


def test_dados_ate_vem_da_ultima_barra_e_nao_do_relogio(monkeypatch):
    """A série termina na SEXTA (28/08). Não importa que hoje seja sábado."""
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(_serie_ate("2026-08-28")))
    _m50, _m200, origem, ate = gts._medias_moveis("SNDK")
    assert origem == "serie"
    assert ate == "2026-08-28"


def test_sabado_nao_vira_sessao():
    """O carimbo de relógio dava "2026-08-29" -- sábado, sem pregão nenhum. A
    série não tem essa barra porque ela não existe."""
    import datetime
    assert datetime.date.fromisoformat("2026-08-29").weekday() == 5, "é sábado"
    assert "2026-08-29" not in [str(d.date()) for d in _serie_ate("2026-08-28").index]


def test_sem_serie_o_painel_nao_declara_sessao(monkeypatch):
    """Caindo no fast_info não há barra. Melhor não declarar do que declarar
    uma data que não veio de dado nenhum -- o detector simplesmente não conta
    este painel."""
    monkeypatch.setattr(gts.market_data_provider, "get_daily_history",
                        lambda *a, **k: _Resultado(None, ok=False, source="x"))
    monkeypatch.setattr(gts.yf, "Ticker", lambda t: _fast_info())
    _m50, _m200, origem, ate = gts._medias_moveis("SNDK")
    assert origem == "yahoo"
    assert ate is None
