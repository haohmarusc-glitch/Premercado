"""RVOL medido pelo relógio da sessão regular, não pela contagem de barras.

Incidente real (NVDA, 26/08/2026). O painel Técnica mostrou `RVOL 8.89 —
alto` num dia em que a NVDA negociou 141M de ações, com mediana de 20 pregões
na casa das centenas de milhões. Com a sessão inteira no frame a fração vale
1,0 e o rvol vira `volume_do_dia / mediana20` = 0,78. Para dar 8,89 a mediana
teria de ser ~16M — duas ordens de grandeza abaixo do real.

A conta antiga derivava o tempo decorrido da CONTAGEM de barras
(`min(1.0, len(intraday)/78)`), então barras de fora do pregão entravam no
numerador com seu volume enquanto o denominador as tratava como tempo de
pregão. Num balanço AMC o pós-mercado da NVDA move centenas de milhões.
"""

import datetime as dt
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.volume_intradiario import (  # noqa: E402
    MINUTOS_DO_PREGAO,
    barras_da_sessao,
    rvol_da_sessao,
)

MEDIANA20 = 180_000_000


def _frame(hora, minuto, n_barras, volume_total, dia=26):
    """Frame de 5min começando em (hora, minuto), volume dividido igualmente."""
    base = dt.datetime(2026, 8, dia, hora, minuto)
    idx = pd.DatetimeIndex([base + dt.timedelta(minutes=5 * i)
                            for i in range(n_barras)])
    por_barra = volume_total / n_barras if n_barras else 0
    return pd.DataFrame({"Volume": [por_barra] * n_barras,
                         "High": [1.0] * n_barras,
                         "Low": [1.0] * n_barras,
                         "Close": [1.0] * n_barras}, index=idx)


# ── o incidente ─────────────────────────────────────────────────────────────

def test_so_barras_de_pos_mercado_nao_produzem_rvol():
    """O caso NVDA: 16 barras de pós-mercado com 328M davam rvol 8,88 na conta
    antiga. Sem barra de pregão, a resposta honesta é "não sei" — e None não é
    zero: quem mostra tem que escrever "—", nunca "volume baixo"."""
    rvol, fracao = rvol_da_sessao(_frame(16, 20, 16, 328_000_000), MEDIANA20)
    assert rvol is None and fracao == 0.0


def test_sessao_inteira_da_o_numero_conferivel_a_olho():
    """No fim do pregão a fração vale 1,0 e o rvol converge para
    volume_do_dia / mediana20 — que dá para conferir contra o painel de
    volume sem calculadora."""
    rvol, fracao = rvol_da_sessao(_frame(9, 30, 78, 141_000_000), MEDIANA20)
    assert fracao == 1.0
    assert rvol == round(141_000_000 / MEDIANA20, 2) == 0.78


def test_pre_mercado_tambem_fica_de_fora():
    rvol, fracao = rvol_da_sessao(_frame(7, 0, 20, 50_000_000), MEDIANA20)
    assert rvol is None and fracao == 0.0


def test_barras_de_fora_nao_inflam_o_rvol_do_pregao():
    """Frame misto: uma hora de pregão mais o pós-mercado pesado. Só a hora de
    pregão conta, dos dois lados da divisão."""
    pregao = _frame(9, 30, 12, 40_000_000)
    pos = _frame(16, 20, 16, 328_000_000)
    rvol, fracao = rvol_da_sessao(pd.concat([pregao, pos]), MEDIANA20)
    esperado = 40_000_000 / (MEDIANA20 * (60 / MINUTOS_DO_PREGAO))
    assert fracao == pytest.approx(60 / MINUTOS_DO_PREGAO, abs=1e-9)
    assert rvol == pytest.approx(round(esperado, 2), abs=0.01)


# ── a fração vem do relógio ─────────────────────────────────────────────────

@pytest.mark.parametrize("n_barras,minutos", [(1, 5), (12, 60), (39, 195),
                                              (78, 390)])
def test_a_fracao_e_tempo_decorrido_e_nao_contagem(n_barras, minutos):
    """A barra de 5min é rotulada pelo INÍCIO do intervalo; o tempo decorrido
    vai até o FIM dela."""
    _, fracao = rvol_da_sessao(_frame(9, 30, n_barras, 1_000_000), MEDIANA20)
    assert fracao == pytest.approx(minutos / MINUTOS_DO_PREGAO, abs=1e-9)


def test_buraco_no_meio_nao_encolhe_o_tempo():
    """Frame com barras faltando no meio: a fração continua vindo do horário
    da última barra, não de quantas chegaram. Era esse buraco que a contagem
    de barras interpretava como "começo de pregão"."""
    cheio = _frame(9, 30, 78, 141_000_000)
    # Um terço das barras, MAS preservando a última: é ela que marca o tempo
    # decorrido. (`iloc[::3]` sozinho para em 15h45 e a fração cai para 0,974
    # com razão -- o pregão realmente não chegou ao fim naquele frame.)
    esburacado = cheio.iloc[list(range(0, len(cheio), 3)) + [len(cheio) - 1]]
    _, fracao_cheia = rvol_da_sessao(cheio, MEDIANA20)
    _, fracao_furada = rvol_da_sessao(esburacado, MEDIANA20)
    assert fracao_cheia == fracao_furada == 1.0


# ── bordas ──────────────────────────────────────────────────────────────────

def test_frame_vazio_nao_estoura():
    assert rvol_da_sessao(pd.DataFrame({"Volume": []}), MEDIANA20) == (None, 0.0)
    assert rvol_da_sessao(None, MEDIANA20) == (None, 0.0)


def test_sem_base_devolve_none_com_a_fracao():
    """Sem mediana não dá para dividir, mas a fração é fato e serve ao
    `_rvol_signal` de quem chama."""
    rvol, fracao = rvol_da_sessao(_frame(9, 30, 78, 141_000_000), 0)
    assert rvol is None and fracao == 1.0


def test_frame_sem_horario_volta_inteiro():
    """Filtrar sem saber a hora seria jogar dado fora às cegas — pior que o
    problema que isto conserta."""
    df = pd.DataFrame({"Volume": [1.0, 2.0]})
    assert len(barras_da_sessao(df)) == 2


def test_fechamento_e_exclusivo():
    """A barra das 16h00 já é pós-fechamento: o pregão vai até 15h55."""
    assert len(barras_da_sessao(_frame(16, 0, 1, 1_000_000))) == 0
    assert len(barras_da_sessao(_frame(15, 55, 1, 1_000_000))) == 1
