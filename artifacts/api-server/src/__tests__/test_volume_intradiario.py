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
    medida_do_rvol,
    pregao_curto,
    rvol_da_sessao,
    situacao_do_rvol,
)

MEDIANA20 = 180_000_000


def _frame(hora, minuto, n_barras, volume_total, dia=26, data=None):
    """Frame de 5min começando em (hora, minuto), volume dividido igualmente.

    `data` sobrepõe o dia padrão (26/08/2026, do incidente NVDA) -- os testes de
    pregão curto precisam de datas específicas do calendário.
    """
    base = dt.datetime.combine(data or dt.date(2026, 8, dia), dt.time(hora, minuto))
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


# ── o alerta composto (preço E RVOL) ────────────────────────────────────────
#
# Critério de aceite da tarefa do alerta de volume: o RVOL ajustado ao horário.
# Os números são os da especificação, e o ponto de escrevê-los AQUI é que o
# alerta passa a ler este mesmo `rvol_da_sessao` em vez de recalcular a fração
# do próprio lado -- a conta já esteve duplicada em tools.py e
# get_technicals.py e quebrou nas duas ao mesmo tempo (o incidente do topo
# deste arquivo). Uma fonte, e os números da tela e do alerta batem por
# construção.


def test_meio_dia_com_a_aritmetica_da_especificacao():
    # 12h00 ET: 150 dos 390 minutos, fração 0,385.
    # Volume 9M contra média de 20M -> 9 / (20 x 0,385) = 1,17x.
    #
    # 30 barras de 5min a partir de 9h30 terminam em 11h55 + 5min = 12h00.
    rvol, fracao = rvol_da_sessao(_frame(9, 30, 30, 9_000_000), 20_000_000)
    assert fracao == pytest.approx(150 / MINUTOS_DO_PREGAO, abs=1e-9)
    assert fracao == pytest.approx(0.385, abs=0.001)
    assert rvol == pytest.approx(1.17, abs=0.01)


def test_as_9h40_o_rvol_sai_mas_nao_e_conclusivo():
    # A especificação pede um PISO de 0,1 na fração "para evitar ruído na
    # abertura". Esse piso existiu aqui (_RVOL_FRACAO_MINIMA) e foi substituído
    # de propósito, porque ele não resolve: às 9h40 a fração real é 0,026 e o
    # piso a levaria a 0,1, deixando o esperado em 2M -- um spike de abertura
    # passa de 1,2x com folga e o alerta dispararia em ruído do mesmo jeito.
    #
    # O que segura o alerta é o SINAL: get_technicals marca
    # rvolSignal="indefinido_abertura" abaixo de ~30min, e a condição de RVOL
    # não é satisfeita enquanto isso (ver lib/alert-conditions.ts). A fração
    # aqui continua sendo a real, sem piso -- número inventado no denominador
    # seria pior que número honesto marcado como não conclusivo.
    m = medida_do_rvol(_frame(9, 30, 2, 2_000_000), 20_000_000)
    assert m.fracao == pytest.approx(10 / MINUTOS_DO_PREGAO, abs=1e-9)
    assert m.rvol is not None         # o número sai...
    assert m.situacao == "indefinido_abertura"   # ...e é o sinal que o barra


def test_as_9h40_um_alerta_de_preco_mais_rvol_nao_dispara():
    """O critério de aceite que substitui o do piso de 0,1.

    Enunciado: às 9h40 ET o RVOL é indefinido, e um alerta de preço + RVOL não
    dispara MESMO com o preço acima do alvo. A metade em TypeScript disto está
    em lib/__tests__/alert-conditions.test.ts; aqui fica a metade que produz o
    sinal, para as duas não poderem discordar sobre o que "9h40" significa.

    Volume de spike na abertura, de propósito: 2M em dez minutos contra média
    de 20M é o caso em que o piso de 0,1 deixaria passar (esperado 2M -> rvol
    1,0... e com 2,5M viraria 1,25x, acima do corte de 1,2x do alerta da AVGO).
    """
    m = medida_do_rvol(_frame(9, 30, 2, 2_500_000), 20_000_000)
    assert m.situacao == "indefinido_abertura"

    # A conta com o piso da especificação, para registro do que foi evitado.
    com_piso = 2_500_000 / (20_000_000 * max(0.1, m.fracao))
    assert com_piso > 1.2, "o piso deixaria o alerta de RVOL>1,2x disparar aqui"


# ── pregão curto: 210 minutos, não 390 ──────────────────────────────────────

def test_pregao_curto_sai_de_regra_e_nao_de_tabela():
    # Dia depois do Thanksgiving (4a quinta de novembro + 1).
    assert pregao_curto(dt.date(2026, 11, 27))
    assert pregao_curto(dt.date(2025, 11, 28))
    # Véspera de Natal em dia de semana, com o 25 também em dia de semana.
    assert pregao_curto(dt.date(2026, 12, 24))   # quinta, Natal na sexta
    # 3 de julho de 2025: quinta, com o feriado na sexta.
    assert pregao_curto(dt.date(2025, 7, 3))

    # Pregão inteiro.
    assert not pregao_curto(dt.date(2026, 8, 26))
    assert not pregao_curto(dt.date(2026, 11, 26))  # o Thanksgiving em si é fechado
    # 4 de julho de 2026 cai num sábado: o dia 3 (sexta) é o feriado OBSERVADO,
    # bolsa fechada o dia inteiro -- não é meio pregão.
    assert not pregao_curto(dt.date(2026, 7, 3))
    # Natal de 2027 cai num sábado: o dia 24 (sexta) é o observado.
    assert not pregao_curto(dt.date(2027, 12, 24))


def test_o_rvol_do_fim_de_um_pregao_curto_e_1x_e_nao_1_86x():
    """O defeito que o denominador fixo produzia.

    Às 13h de um pregão curto a sessão ACABOU: a fração tem de valer 1,0 e o
    rvol tem de convergir para `volume_do_dia / mediana20`. Com 390 no
    denominador a fração saía 210/390 = 0,538 e o rvol vinha 1,86x acima do
    real -- um dia de volume perfeitamente normal lido como "alto", em todos os
    tickers ao mesmo tempo, num dia por ano.
    """
    # 42 barras de 5min de 9h30 até 12h55 + 5min = 13h00, véspera de Natal.
    frame = _frame(9, 30, 42, 20_000_000, data=dt.date(2026, 12, 24))
    m = medida_do_rvol(frame, 20_000_000)
    assert m.pregao_curto
    assert m.minutos == 210
    assert m.fracao == 1.0
    assert m.rvol == pytest.approx(1.0, abs=0.01)
    assert m.situacao == "normal"

    # A conta antiga, para registro: 1,86x e rótulo "alto".
    inflado = 20_000_000 / (20_000_000 * (210 / MINUTOS_DO_PREGAO))
    assert inflado == pytest.approx(1.86, abs=0.01)
    assert situacao_do_rvol(inflado, 210) == "alto"


def test_num_pregao_curto_o_pos_mercado_nao_entra_como_pregao():
    """O caso NVDA reencenado pelo calendário.

    Num pregão curto o pós-mercado começa às 13h. Um filtro fixo `< 16:00`
    deixaria três horas de volume de pós-mercado entrarem no numerador como se
    fossem pregão -- e a véspera de Natal negocia pouco, então o peso relativo
    do pós-mercado é maior, não menor.
    """
    vespera = dt.date(2026, 12, 24)
    pregao = _frame(9, 30, 42, 20_000_000, data=vespera)          # 9h30-13h00
    pos = _frame(13, 0, 12, 60_000_000, data=vespera)             # 13h00-14h00
    m = medida_do_rvol(pd.concat([pregao, pos]), 20_000_000)
    assert m.fim == dt.time(13, 0)
    assert m.rvol == pytest.approx(1.0, abs=0.01), "o pós-mercado entrou no rvol"

    # No mesmo horário, num pregão INTEIRO, 13h00 é meio de sessão e as barras
    # das 13h em diante são pregão de verdade.
    comum = dt.date(2026, 12, 22)
    assert not pregao_curto(comum)
    m2 = medida_do_rvol(pd.concat([_frame(9, 30, 42, 20_000_000, data=comum),
                                   _frame(13, 0, 12, 60_000_000, data=comum)]),
                        20_000_000)
    assert m2.fim == dt.time(14, 0)
    assert m2.rvol == pytest.approx(80_000_000 / (20_000_000 * (270 / 390)), abs=0.01)


# ── situacao_do_rvol: a única fonte do sinal ────────────────────────────────

def test_sem_rvol_a_situacao_e_indisponivel_e_nunca_baixo():
    """Ausência de dado não é volume fraco.

    "baixo" para um ticker sem barra nenhuma é a afirmação errada mais fácil de
    fazer aqui, e é a que o painel fez: `rvol = 0` renderizado como volume
    fraco em vez de "—".
    """
    assert situacao_do_rvol(None, 300) == "indisponivel"
    assert situacao_do_rvol(None, 0) == "indisponivel"
    assert medida_do_rvol(None, MEDIANA20).situacao == "indisponivel"


def test_pre_mercado_nao_produz_rvol():
    """Fora do pregão regular o RVOL é indefinido, não zero e não 'baixo'.

    Às 7h da manhã existe volume (pré-mercado), mas não existe fração de pregão
    decorrida para dividir por ela. `barras_da_sessao` remove as barras e o que
    sobra é "não sei".
    """
    m = medida_do_rvol(_frame(7, 0, 20, 50_000_000), MEDIANA20)
    assert m.rvol is None
    assert m.situacao == "indisponivel"
    assert m.fracao == 0.0


def test_os_cortes_do_sinal():
    assert situacao_do_rvol(1.5, 300) == "alto"
    assert situacao_do_rvol(1.49, 300) == "normal"
    assert situacao_do_rvol(0.7, 300) == "normal"
    assert situacao_do_rvol(0.69, 300) == "baixo"
    # Abaixo de 30 minutos nenhum valor é conclusivo, nem um rvol baixíssimo.
    assert situacao_do_rvol(0.1, 29) == "indefinido_abertura"
    assert situacao_do_rvol(0.1, 30) == "baixo"
