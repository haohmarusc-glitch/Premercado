"""
A tabela de faixas do Fear & Greed, em UM lugar só.

Ate 26/08/2026 ela existia duas vezes -- `tools.py::_classify` (que alimenta o
agente e o Veredito) e `get_macro.py::classify` (que alimenta o painel). As
duas concordavam, entao o defeito era latente; a MM50 mostrou no mesmo dia o
que acontece quando duas copias da mesma conta divergem sem ninguem notar.

## A fronteira

O incidente que trouxe este modulo nao foi divergencia de tabela -- foi a
FRONTEIRA. O Veredito de 26/08 saiu com

    prosa:   "Fear & Greed 54,9 (neutro em leitura de 13:28:50)"
    painel:  "Fear & Greed 55.2 · ganância"

Os dois rotulos estao CERTOS: 54,9 <= 55 e neutro, 55,2 > 55 e ganancia. O
que aconteceu foi 0,3 ponto de deriva intradia atravessar a fronteira dos 55
e trocar a palavra.

E' o mesmo custo assimetrico do resto do repo: quem le "ganancia" opera
diferente de quem le "neutro", e a distancia entre os dois aqui e' ruido.
Por isso `faixa()` devolve tambem a distancia ate a borda mais proxima -- o
rotulo continua sendo o rotulo, mas a tela pode dizer quando ele esta por um
fio.
"""

# (teto da faixa, rotulo). O teto e' INCLUSIVO: score <= teto entra na faixa.
FAIXAS = (
    (25.0, "medo extremo"),
    (45.0, "medo"),
    (55.0, "neutro"),
    (75.0, "ganância"),
    (100.0, "ganância extrema"),
)

# A leitura em UMA linha, por faixa. Terceira copia da mesma escada, achada
# ao trazer as duas primeiras para ca' -- e a unica das tres que tinha defeito:
#
#     "Panico ..." if score and score <= 25 else ... else "Euforia ..."
#
# `score and` e' FALSO em 0.0, entao a cadeia inteira despencava e o score 0 --
# o panico maximo que o indice sabe expressar -- saia como "Euforia, risco
# maximo de reversao". O sentido exatamente invertido, no extremo em que o
# rotulo mais importa.
#
# Ninguem viu porque 0 nunca apareceu em producao. Continua sendo o valor mais
# provavel de aparecer num crash, que e' quando se le esta tela.
INTERPRETACOES = {
    "medo extremo": "Pânico — potencial oportunidade contrária",
    "medo": "Medo predominante — cautela",
    "neutro": "Sentimento neutro",
    "ganância": "Mercado ganancioso — risco de reversão",
    "ganância extrema": "Euforia — risco máximo de reversão",
    "desconhecido": "Sem leitura do índice",
}

# Abaixo de quantos pontos da borda o rotulo e' considerado "por um fio".
#
# 1,0 porque a deriva observada entre duas leituras do mesmo dia foi de 0,3 a
# 0,7 ponto. Um limiar menor nao cobriria o caso real; um bem maior marcaria
# metade das leituras e a marca perderia sentido.
MARGEM_DA_FRONTEIRA = 1.0


def classificar(score) -> str:
    """O rotulo em pt-BR, ou "desconhecido" quando nao ha leitura."""
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "desconhecido"
    if score != score:                      # NaN
        return "desconhecido"
    for teto, rotulo in FAIXAS:
        if score <= teto:
            return rotulo
    return FAIXAS[-1][1]


def distancia_da_fronteira(score):
    """Pontos ate a borda de faixa mais proxima, ou None sem leitura.

    Serve para a tela dizer "55,2 -- a 0,2 da faixa neutro" em vez de afirmar
    "ganancia" com a mesma firmeza que afirmaria em 68."""
    if classificar(score) == "desconhecido":
        return None
    bordas = [teto for teto, _ in FAIXAS[:-1]]
    return round(min(abs(score - b) for b in bordas), 2)


def interpretar(score) -> str:
    """A leitura em uma linha. Derivada do ROTULO, nunca de uma segunda
    escada de limiares -- era a existencia dessa segunda escada que deixava o
    score 0 sair como euforia."""
    return INTERPRETACOES[classificar(score)]


def faixa(score) -> dict:
    """{rotulo, distanciaDaFronteira, naFronteira} -- o que as duas fontes
    publicam. Uma so' funcao para as duas nao voltarem a divergir."""
    rotulo = classificar(score)
    dist = distancia_da_fronteira(score)
    return {
        "rotulo": rotulo,
        "distanciaDaFronteira": dist,
        "naFronteira": dist is not None and dist <= MARGEM_DA_FRONTEIRA,
    }
