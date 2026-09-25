"""RVOL medido pelo RELÓGIO da sessão regular, não pela contagem de barras.

Incidente real (NVDA, 26/08/2026). O painel Técnica mostrou:

    RVOL 8.89 — alto        Volume vs média 1.07x

As duas métricas têm definições diferentes (uma é ritmo intradiário, a outra é
média de 5 pregões sobre a mediana de 20), então não se contradizem por
construção. Mas 8,89 não se sustenta assim mesmo: com a sessão inteira no
frame, `fraction` vale 1,0 e o rvol vira `volume_do_dia / mediana20`. A NVDA
negociou 141M com mediana na casa das centenas de milhões -- daria 0,78. Para
dar 8,89 a mediana teria de ser ~16M, duas ordens de grandeza abaixo do real.

A conta antiga:

    fraction_elapsed = min(1.0, len(intraday) / 78)

Ela deriva o tempo decorrido da CONTAGEM DE BARRAS. Se o frame trouxer barras
fora do pregão regular -- e num dia de balanço AMC o pós-mercado da NVDA move
centenas de milhões de ações --, o volume delas entra no numerador enquanto o
denominador as trata como tempo de pregão. Reproduzindo: 16 barras de
pós-mercado com 328M ações dão rvol 8,88.

O guarda `_RVOL_FRACAO_MINIMA` foi criado para o caso NBIS (rvol 5,81 aos sete
minutos de pregão) e não distingue "começo da sessão" de "sobraram só barras
de fora dela": as duas situações têm poucas barras.

Aqui a fração vem do relógio e as barras são filtradas para a sessão regular.
No fim do pregão a fração vale 1,0 por construção, e o rvol converge para
`volume_do_dia / mediana20` -- que é o número que dá para conferir a olho
contra o painel de volume.

## Uma fonte só

A conta estava DUPLICADA em tools.py e get_technicals.py, com o mesmo bug nas
duas. Três comentários no repo diziam que test_rvol_abertura.py amarrava as duas
cópias de `_rvol_signal`. O arquivo NÃO EXISTIA -- a duplicação era
documentada como segura por um teste que ninguém escreveu, e a conta (que
também era cópia, e essa nem teste alegava ter) quebrou nos dois arquivos ao
mesmo tempo sem ninguém ver.

O arquivo existe agora. A conta mora aqui, o SINAL (`situacao_do_rvol`) também
-- as duas cópias de `_rvol_signal` foram apagadas -- e os dois importam.

## Pregão curto: 210 minutos, não 390

O denominador não é constante. A NYSE fecha às 13h ET na véspera de Natal, no
dia depois do Thanksgiving e em 3 de julho, e nesses dias o pregão tem 210
minutos. Dividir por 390 num pregão de 210 infla o rvol: às 13h, com a sessão
JÁ ENCERRADA, a fração sairia 0,538 em vez de 1,0 e o rvol viria 1,86x acima
do real -- um dia inteiro de volume normal lido como "alto" em todos os
tickers ao mesmo tempo.

As datas saem de REGRA, não de tabela por ano. Tabela de feriado é a mesma
armadilha de `POPULAR_TICKERS` e da lista de ferramentas do prompt: uma lista
mantida à mão ao lado do código envelhece em silêncio, e aqui o sintoma seria
rvol inflado num dia do ano, o que ninguém liga a uma lista vencida.

O que a regra NÃO cobre: fechamento antecipado de ocasião (luto nacional,
falha técnica da bolsa), que é anunciado na hora e não tem regra. Nesses dias o
rvol sai inflado a partir do fechamento real, do mesmo jeito que saía em todo
pregão curto antes disto. É um dia por década contra três por ano.
"""

from __future__ import annotations

import datetime as _dt
from typing import NamedTuple

# Pregão regular americano, em horário da bolsa.
ABERTURA = _dt.time(9, 30)
FECHAMENTO = _dt.time(16, 0)
MINUTOS_DO_PREGAO = 390  # 6h30

# Pregão curto (véspera de Natal, pós-Thanksgiving, 3 de julho).
FECHAMENTO_CURTO = _dt.time(13, 0)
MINUTOS_DO_PREGAO_CURTO = 210  # 3h30

# Abaixo disto o rvol não é conclusivo e não deve ser usado para decidir nada.
#
# Volume intradiário NÃO é uniforme: a distribuição é em U, com o leilão de
# abertura concentrando muito mais volume por minuto que o miolo do pregão. O
# rvol divide o volume de hoje por `base20 * fração_decorrida`, uma aproximação
# que assume uniformidade -- e nos primeiros minutos ela superestima
# grosseiramente.
#
# Visto em produção (NBIS, 17/08/2026 10:37 BRT, sete minutos de pregão): rvol
# 5,81 rotulado "alto", e a análise com IA leu como "volume muito acima do
# normal, típico de realização de lucro". Com ~2,6% da sessão decorrida e
# tipicamente 8-12% do volume diário já negociado, o número sai inflado em 3-4x
# só pela forma da curva -- a conclusão foi construída sobre um artefato.
#
# O número cru continua saindo (quem souber o que ele é pode usar); o que não
# pode é virar "alto"/"baixo", rótulo que o prompt e a tela tratam como
# convicção de mercado. Corrigir de verdade exigiria uma curva de volume
# intradiário calibrada, que este repo não tem -- e inventar uma sem dado seria
# trocar um viés conhecido por um desconhecido.
#
# Em MINUTOS, não em fração: `6/78` -- a forma antiga, herdada da contagem de
# barras -- vale ~0,077 e num pregão de 210 minutos isso são 16 minutos, não 30.
# Atrelar o guarda à fração o afrouxaria justamente nos dias curtos.
MINUTOS_MINIMOS_CONCLUSIVOS = 30

# `rvol` existe mas o pregão é novo demais para o número significar algo.
INDEFINIDO_ABERTURA = "indefinido_abertura"
# Não há rvol: sem base de volume, sem barra de pregão, ou fora do pregão.
INDISPONIVEL = "indisponivel"


def _quarta_quinta_de_novembro(ano: int) -> _dt.date:
    """Thanksgiving: a quarta quinta-feira de novembro."""
    primeiro = _dt.date(ano, 11, 1)
    # weekday(): segunda=0 ... quinta=3.
    primeira_quinta = 1 + (3 - primeiro.weekday()) % 7
    return _dt.date(ano, 11, primeira_quinta + 21)


def _dia_de_semana(data: _dt.date) -> bool:
    return data.weekday() < 5


def pregao_curto(data: _dt.date) -> bool:
    """Este dia fecha às 13h ET?

    As três regras, e por que cada condição de "dia de semana" está aí:

    - Dia depois do Thanksgiving. É sempre uma sexta-feira, então não precisa
      de guarda.
    - 24 de dezembro, com o 25 caindo em dia de semana. Se o Natal cai no
      sábado, o dia 24 (sexta) é o feriado OBSERVADO e a bolsa fecha o dia
      inteiro -- não é meio pregão. Se cai no domingo, o 24 é sábado.
    - 3 de julho, com o 4 caindo em dia de semana. Mesma lógica: 4 no sábado
      faz do dia 3 (sexta) o feriado observado, com a bolsa fechada.

    Dia fechado devolve False, e está certo: sem barra de pregão o rvol já sai
    `None` por falta de dado, sem precisar saber que era feriado.
    """
    if data.month == 11:
        return data == _quarta_quinta_de_novembro(data.year) + _dt.timedelta(days=1)
    if data.month == 12 and data.day == 24:
        return _dia_de_semana(data) and _dia_de_semana(_dt.date(data.year, 12, 25))
    if data.month == 7 and data.day == 3:
        return _dia_de_semana(data) and _dia_de_semana(_dt.date(data.year, 7, 4))
    return False


def fechamento_do_dia(data: _dt.date | None) -> _dt.time:
    """A hora de fechamento deste dia. Sem data, assume pregão inteiro."""
    if data is not None and pregao_curto(data):
        return FECHAMENTO_CURTO
    return FECHAMENTO


def minutos_do_pregao(data: _dt.date | None) -> int:
    """Quantos minutos o pregão deste dia tem. Sem data, assume 390."""
    if data is not None and pregao_curto(data):
        return MINUTOS_DO_PREGAO_CURTO
    return MINUTOS_DO_PREGAO


def minutos_decorridos_ate(momento: _dt.time, data: _dt.date | None = None) -> float:
    """Minutos de pregão até `momento`, limitados ao pregão deste dia."""
    minutos = ((momento.hour * 60 + momento.minute)
               - (ABERTURA.hour * 60 + ABERTURA.minute))
    return max(0.0, min(float(minutos_do_pregao(data)), float(minutos)))


def _fracao_ate(momento: _dt.time, data: _dt.date | None = None) -> float:
    """Quanto do pregão já passou até `momento`, entre 0 e 1."""
    return minutos_decorridos_ate(momento, data) / minutos_do_pregao(data)


def barras_da_sessao(intraday):
    """Só as barras dentro do pregão regular, com o fechamento DO DIA de cada uma.

    O fechamento vem por barra e não da constante: num pregão curto o
    pós-mercado começa às 13h, e um filtro `< 16:00` deixaria três horas de
    volume de pós-mercado entrarem como volume de pregão -- é o mesmo defeito
    do caso NVDA, só que ativado pelo calendário em vez pelo balanço.

    Frame sem índice de horário volta inteiro: filtrar sem saber a hora seria
    jogar dado fora às cegas, que é pior que o problema que isto conserta.
    """
    if intraday is None or len(intraday) == 0:
        return intraday
    indice = getattr(intraday, "index", None)
    horas = getattr(indice, "time", None)
    if horas is None:
        return intraday
    # `index.date` acompanha `index.time` num DatetimeIndex. Sem ele, o dia é
    # desconhecido e o fechamento cai no padrão de 16h.
    datas = getattr(indice, "date", None)
    if datas is None:
        datas = [None] * len(horas)
    dentro = [ABERTURA <= h < fechamento_do_dia(d) for h, d in zip(horas, datas)]
    return intraday[dentro]


class MedidaDeRvol(NamedTuple):
    """Tudo o que se sabe sobre o rvol neste instante.

    `situacao` é a ÚNICA fonte do sinal ("alto"/"normal"/"baixo"/
    "indefinido_abertura"/"indisponivel"); era função copiada em dois arquivos.

    `fim` é o horário de bolsa da última barra fechada -- quem consome fora do
    Python (o checker de alertas roda em Node) precisa dele para recusar um
    rvol velho: sem isso, "o valor existe" e "o valor é de agora" ficam
    indistinguíveis.
    """
    rvol: float | None
    fracao: float
    minutos: float
    situacao: str
    fim: _dt.time | None
    data: _dt.date | None
    pregao_curto: bool


def situacao_do_rvol(rvol: float | None, minutos: float) -> str:
    """O sinal do rvol, ou por que ele não vale.

    `minutos` é tempo de pregão decorrido. Sem rvol a resposta é
    "indisponivel" -- nunca "baixo": ausência de dado não é volume fraco, e foi
    exatamente essa confusão que fez o painel escrever "volume baixo" para um
    ticker sem barra nenhuma.
    """
    if rvol is None:
        return INDISPONIVEL
    if minutos < MINUTOS_MINIMOS_CONCLUSIVOS:
        return INDEFINIDO_ABERTURA
    return "alto" if rvol >= 1.5 else "baixo" if rvol < 0.7 else "normal"


def medida_do_rvol(intraday, base20, duracao_da_barra_min: int = 5) -> MedidaDeRvol:
    """O rvol do frame intradiário, com o contexto que diz se ele vale.

    `rvol` é None quando não há base ou não há barra utilizável -- e None não
    é zero: quem chama deve mostrar "—", nunca "volume baixo".
    """
    vazia = MedidaDeRvol(None, 0.0, 0.0, INDISPONIVEL, None, None, False)

    regulares = barras_da_sessao(intraday)
    if regulares is None or len(regulares) == 0:
        return vazia

    indice = getattr(regulares, "index", None)
    horas = getattr(indice, "time", None)
    if horas is None or len(horas) == 0:
        return vazia

    # A barra do frame de 5min é rotulada pelo INÍCIO do intervalo; o tempo
    # decorrido vai até o FIM dela.
    ultima = max(horas)
    fim = (_dt.datetime.combine(_dt.date.min, ultima)
           + _dt.timedelta(minutes=duracao_da_barra_min)).time()

    datas = getattr(indice, "date", None)
    data = max(datas) if datas is not None and len(datas) else None

    minutos = minutos_decorridos_ate(fim, data)
    fracao = minutos / minutos_do_pregao(data)
    curto = data is not None and pregao_curto(data)
    if fracao <= 0:
        return MedidaDeRvol(None, 0.0, 0.0, INDISPONIVEL, fim, data, curto)

    volume = float(regulares["Volume"].sum())
    esperado = float(base20 or 0) * fracao
    if esperado <= 0:
        return MedidaDeRvol(None, fracao, minutos, INDISPONIVEL, fim, data, curto)

    rvol = round(volume / esperado, 2)
    return MedidaDeRvol(rvol, fracao, minutos, situacao_do_rvol(rvol, minutos),
                        fim, data, curto)


def rvol_da_sessao(intraday, base20, duracao_da_barra_min: int = 5):
    """`(rvol, fracao)` -- a forma curta de `medida_do_rvol`."""
    m = medida_do_rvol(intraday, base20, duracao_da_barra_min)
    return m.rvol, m.fracao
