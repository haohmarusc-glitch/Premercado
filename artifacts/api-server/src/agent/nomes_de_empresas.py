"""Como um papel aparece ESCRITO — mapa de nomes e relevância de manchete.

Módulo só de stdlib (re), sem import relativo. Nasceu de um incidente de
deploy (27/08/2026): a #410 fez o get_trend importar `fala_do_papel` de
news_sources, e news_sources puxa `config` e `cache` por import RELATIVO —
que só resolve dentro do pacote. O get_trend rodava por SPAWN
(sys.path[0] = src/agent, sem pacote), caiu no braço de imports planos, e o
/trend inteiro morreu com "attempted relative import with no known parent
package".

A restrição que ele foi criado para respeitar não existe mais: desde a
unificação do spawn, todo script roda como módulo do pacote e alcança
qualquer irmão, relativo inclusive. Este arquivo continua onde está porque
separar "como um papel aparece escrito" de "buscar notícia" é uma divisão
que se sustenta sozinha — mas ela agora é escolha de desenho, não
contorno de um contexto de import.

A lição que sobrevive é sobre a forma do incidente, não sobre imports:
quando existem dois jeitos de rodar a mesma coisa, o que funciona num
quebra no outro, e o teste do jeito errado passa verde.
test_scripts_de_spawn_importam.py fixa o contrato de hoje.
"""

import re

# Nome da empresa pra montar a query do Google News: buscar só "ALAB" traz
# lixo (é sigla de várias coisas), enquanto "Astera Labs" traz a notícia de
# verdade. Só os tickers sob cobertura precisam estar aqui -- qualquer outro
# cai no fallback '"TICKER" stock', que funciona bem pra ticker conhecido.
# Não vale resolver isso via yf.Ticker().info: é uma chamada de rede lenta a
# mais por ticker justamente no caminho que existe pra ser rápido.
_COMPANY_NAMES = {
    "NVDA": "Nvidia",
    "SMCI": "Super Micro Computer",
    "MU": "Micron Technology",
    "INTC": "Intel",
    "GOOGL": "Alphabet Google",
    "ARM": "Arm Holdings",
    "TSLA": "Tesla",
    "SNDK": "SanDisk",
    "WDC": "Western Digital",
    "ALAB": "Astera Labs",
    "CRDO": "Credo Technology",
    "ANET": "Arista Networks",
    "VRT": "Vertiv",
    "TSM": "TSMC Taiwan Semiconductor",
    "ASML": "ASML",
    "HCC": "Warrior Met Coal",
    "AMR": "Alpha Metallurgical Resources",
    "AVGO": "Broadcom",
    "MRVL": "Marvell Technology",
    "SKHY": "SK Hynix",
}


# ── Relevância: a manchete FALA do papel? ────────────────────────────────────
#
# Incidente real (ARM, 26/08/2026). O feed do Yahoo para ARM devolveu, entre
# as 8 manchetes que viraram sentimento:
#
#     "AMD Stock Upgraded To Strong Buy. Here's Why."
#     "AMD Stock Gets a 'Strong Buy' Upgrade: Why It Could Outperform Nvidia"
#
# Duas notícias da AMD contadas como sentimento da ARM. E não foi cosmético:
# o rótulo "positivo" contradisse a técnica de baixa, a contradição virou
# "divergência técnico × notícias", e a divergência virou o sinal AGUARDAR.
# Uma matéria sobre outra empresa mudou a recomendação da tela.
#
# A regra é o SUJEITO: a manchete precisa nomear o papel -- pelo símbolo ou
# pelo nome da empresa. Contar pelo símbolo sozinho não serve, porque o Yahoo
# escreve "Nvidia beats" muito mais do que "NVDA beats"; é para isso que o
# mapa acima já existia.
#
# Falso NEGATIVO aqui é barato e quase sempre correto: a manchete de setor
# ("Chip Stocks Rally") não nomeia ninguém, e sentimento de setor não é
# sentimento da empresa. Falso POSITIVO é o que custa -- foi ele que mudou o
# sinal.

# Palavra de nome de empresa que não identifica ninguém sozinha. Sem esta
# lista, "Holdings" faria qualquer holding virar notícia da ARM.
_GENERICO_NO_NOME = {
    "holdings", "holding", "technology", "technologies", "semiconductor",
    "semiconductors", "systems", "networks", "labs", "laboratories",
    "computer", "computers", "micro", "digital", "resources", "industries",
    "international", "group", "corp", "corporation", "incorporated",
    "limited", "company",
}

# Piso de tamanho para o token de nome, e a razão é concreta: "Warrior Met
# Coal" tem "Met", que casa com "Analysts Met With Management" em qualquer
# manchete. Quatro letras cortam esse tipo sem perder "Arm" (que já entra
# pelo símbolo) nem "Hynix", "Astera", "Credo", "Micron".
_MINIMO_DO_TOKEN_DE_NOME = 4


def _termos_do_papel(ticker: str) -> list[str]:
    """Como este papel pode aparecer escrito numa manchete."""
    termos = [ticker]
    nome = _COMPANY_NAMES.get(ticker.upper(), "")
    if nome:
        termos.append(nome)  # o nome inteiro, "Super Micro Computer"
        termos.extend(
            palavra for palavra in nome.split()
            if len(palavra) >= _MINIMO_DO_TOKEN_DE_NOME
            and palavra.lower() not in _GENERICO_NO_NOME
        )
    return termos


def fala_do_papel(titulo: str, ticker: str) -> bool:
    """A manchete nomeia ESTE papel?

    Palavra inteira, sempre: sem \b, "ARM" casa dentro de "alarm", "farm",
    "charm" e "warm" -- e a cobertura de semicondutor fala de "warm" o tempo
    todo. É a mesma armadilha de substring que já mordeu o classificador de
    sentimento ("against" contendo "gains").
    """
    if not titulo or not ticker:
        return False
    for termo in _termos_do_papel(ticker):
        if re.search(rf"\b{re.escape(termo)}\b", titulo, re.IGNORECASE):
            return True
    return False
