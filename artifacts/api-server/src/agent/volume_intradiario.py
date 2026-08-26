"""RVOL medido pelo RELÓGIO da sessão regular, não pela contagem de barras.

Incidente real (NVDA, 26/08/2026). O painel Técnica mostrou:

    RVOL 8.89 — alto        Volume vs média 1.07x

As duas métricas têm definições diferentes (uma é ritmo intradiário, a outra é
média de 5 pregões sobre a mediana de 20), então não se contradizem por
construção. Mas 8,89 não se sustenta assim mesmo: com a sessão inteira no
frame, `fraction` vale 1,0 e o rvol vira `volume_do_dia / mediana20`. A NVDA
negociou 141M com mediana na casa das centenas de milhões — daria 0,78. Para
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

O arquivo existe agora. A conta mora aqui e os dois importam.
"""

from __future__ import annotations

import datetime as _dt

# Pregão regular americano, em horário da bolsa.
ABERTURA = _dt.time(9, 30)
FECHAMENTO = _dt.time(16, 0)
MINUTOS_DO_PREGAO = 390  # 6h30


def _fracao_ate(momento: _dt.time) -> float:
    """Quanto do pregão regular já passou até `momento`, entre 0 e 1."""
    minutos = ((momento.hour * 60 + momento.minute)
               - (ABERTURA.hour * 60 + ABERTURA.minute))
    return max(0.0, min(1.0, minutos / MINUTOS_DO_PREGAO))


def barras_da_sessao(intraday):
    """Só as barras dentro do pregão regular.

    Frame sem índice de horário volta inteiro: filtrar sem saber a hora seria
    jogar dado fora às cegas, que é pior que o problema que isto conserta.
    """
    if intraday is None or len(intraday) == 0:
        return intraday
    indice = getattr(intraday, "index", None)
    horas = getattr(indice, "time", None)
    if horas is None:
        return intraday
    dentro = [ABERTURA <= h < FECHAMENTO for h in horas]
    return intraday[dentro]


def rvol_da_sessao(intraday, base20, duracao_da_barra_min: int = 5):
    """(rvol, fracao_do_pregao) a partir do frame intradiário.

    `rvol` é None quando não há base ou não há barra utilizável -- e None não
    é zero: quem chama deve mostrar "—", nunca "volume baixo".
    """
    regulares = barras_da_sessao(intraday)
    if regulares is None or len(regulares) == 0:
        return None, 0.0

    indice = getattr(regulares, "index", None)
    horas = getattr(indice, "time", None)
    if horas is None or len(horas) == 0:
        return None, 0.0

    # A barra do frame de 5min é rotulada pelo INÍCIO do intervalo; o tempo
    # decorrido vai até o FIM dela.
    ultima = max(horas)
    fim = (_dt.datetime.combine(_dt.date.min, ultima)
           + _dt.timedelta(minutes=duracao_da_barra_min)).time()
    fracao = _fracao_ate(fim)
    if fracao <= 0:
        return None, 0.0

    volume = float(regulares["Volume"].sum())
    esperado = float(base20 or 0) * fracao
    if esperado <= 0:
        return None, fracao
    return round(volume / esperado, 2), fracao
