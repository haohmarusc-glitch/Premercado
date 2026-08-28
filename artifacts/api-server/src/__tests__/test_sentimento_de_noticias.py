"""O sentimento é DESTE papel, e ressalva não é endosso.

Incidente real (ARM, 26/08/2026). A tela de Análise Rápida mostrou
"Notícias (positivo)" para ARM com estas quatro manchetes em destaque:

    AMD Stock Upgraded To Strong Buy. Here's Why.
    AMD Stock Gets a 'Strong Buy' Upgrade: Why It Could Outperform Nvidia
    ARM's 93X Earnings Multiple Overshadows Its Growth Potential
    Arm Holdings (ARM) Delivers Strong Growth, but Has the Valuation Run Too Far?

Duas são da AMD. As outras duas são cautelosas, não positivas. O rótulo
"positivo" contradisse a técnica de baixa, virou "divergência técnico ×
notícias", e a divergência virou o sinal AGUARDAR — uma matéria sobre outra
empresa mudou a recomendação da tela.

Os dois defeitos são independentes, e é por isso que os dois estão aqui:
corrigir só a relevância deixaria pos=2, neg=0, score 1.00 e o rótulo
"positivo" intacto.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.get_trend import _NEGATIVE_RE, _POSITIVE_RE, _RESSALVA  # noqa: E402
from agent.news_sources import fala_do_papel  # noqa: E402

AMD_1 = "AMD Stock Upgraded To Strong Buy. Here's Why."
AMD_2 = "AMD Stock Gets a 'Strong Buy' Upgrade: Why It Could Outperform Nvidia"
ARM_1 = "ARM's 93X Earnings Multiple Overshadows Its Growth Potential"
ARM_2 = ("Arm Holdings (ARM) Delivers Strong Growth, but Has the Valuation "
         "Run Too Far?")


def _classificar(titulo: str, ticker: str) -> str:
    """O mesmo caminho de news_sentiment, sem a rede do yfinance."""
    if not fala_do_papel(titulo, ticker):
        return "descartada"
    if _RESSALVA.search(titulo):
        return "misto"
    p = len(set(_POSITIVE_RE.findall(titulo.lower())))
    n = len(set(_NEGATIVE_RE.findall(titulo.lower())))
    return "positivo" if p > n else "negativo" if n > p else "misto"


# ── relevância ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("titulo", [AMD_1, AMD_2])
def test_manchete_de_outra_empresa_sai_do_sentimento(titulo):
    assert not fala_do_papel(titulo, "ARM")


@pytest.mark.parametrize("titulo", [ARM_1, ARM_2])
def test_manchete_do_papel_fica(titulo):
    assert fala_do_papel(titulo, "ARM")


def test_nome_da_empresa_conta_mesmo_sem_o_simbolo():
    """O Yahoo escreve "Nvidia beats" muito mais que "NVDA beats" — casar só
    pelo símbolo jogaria fora a notícia certa."""
    assert fala_do_papel("Nvidia beats on earnings", "NVDA")
    assert fala_do_papel("Super Micro jumps 12% after guidance", "SMCI")


@pytest.mark.parametrize("titulo", [
    "The alarm over chip supply grows",
    "Server farm demand keeps climbing",
    "A charm offensive from the CEO",
    "Warm weather hits the utilities",
])
def test_substring_nao_conta_como_o_papel(titulo):
    """Sem \\b, "ARM" casa dentro de alarm/farm/charm/warm. É a mesma
    armadilha de substring que já mordeu o classificador ("against" contendo
    "gains")."""
    assert not fala_do_papel(titulo, "ARM")


def test_palavra_generica_do_nome_nao_identifica_ninguem():
    """"Holdings" faria qualquer holding virar notícia da ARM."""
    assert not fala_do_papel("Berkshire Holdings raises stake", "ARM")


def test_token_curto_do_nome_nao_entra():
    """"Warrior Met Coal" tem "Met", que casa com qualquer "Met With"."""
    assert not fala_do_papel("Analysts Met With Management", "HCC")
    assert fala_do_papel("Warrior Met Coal tops estimates", "HCC")


# ── ressalva ────────────────────────────────────────────────────────────────

def test_verbo_que_inverte_a_frase_conta_como_negativo():
    """"Overshadows" invertia a manchete e não pontuava em lista nenhuma."""
    assert _NEGATIVE_RE.search("overshadows its growth potential")


@pytest.mark.parametrize("titulo", [
    "Delivers Strong Growth, but Has the Valuation Run Too Far?",
    "Strong quarter, however margins narrowed",
    "Record revenue despite weak guidance",
    "Upgrade raises questions about the multiple",
    "Is the rally over?",
])
def test_manchete_com_ressalva_nao_e_endosso(titulo):
    assert _RESSALVA.search(titulo)


@pytest.mark.parametrize("titulo", [
    "Nvidia beats on earnings and raises guidance",
    "Arm Holdings upgraded to buy rating",
])
def test_manchete_afirmativa_continua_positiva(titulo):
    """A ressalva não pode virar mordaça: manchete que afirma continua
    contando."""
    assert not _RESSALVA.search(titulo)


# ── o caso inteiro, como saiu na tela ───────────────────────────────────────

def test_as_quatro_manchetes_de_arm_deixam_de_dar_positivo():
    tons = [_classificar(t, "ARM") for t in (AMD_1, AMD_2, ARM_1, ARM_2)]
    assert tons == ["descartada", "descartada", "misto", "misto"]
    positivas = tons.count("positivo")
    negativas = tons.count("negativo")
    assert positivas == 0 and negativas == 0, "sem divergência técnico × notícias"


def test_so_a_relevancia_nao_bastaria():
    """A prova de que os dois defeitos são independentes: com o filtro de
    relevância e o classificador antigo, sobrariam 2 positivas e o rótulo
    continuaria "positivo"."""
    antigas = []
    for titulo in (ARM_1, ARM_2):
        p = len(set(_POSITIVE_RE.findall(titulo.lower())))
        # o classificador ANTIGO: sem _RESSALVA e sem "overshadows"
        n = len([w for w in set(_NEGATIVE_RE.findall(titulo.lower()))
                 if "overshadow" not in w])
        antigas.append("positivo" if p > n else "outro")
    assert antigas == ["positivo", "positivo"]


# ═══ NVDA, 26/08/2026 — o rótulo de UMA manchete ═══════════════════════════
#
# O painel dizia "Notícias (positivo)" e a tela concluía "alta forte
# CONFIRMADA por fluxo de notícias positivo" — em cima de UMA manchete. O
# texto era honesto ("tom positivo em 1 de 8 manchetes analisadas") e mesmo
# assim o rótulo saiu com confiança máxima, porque o score divide por
# (positivas + negativas), não pelas ANALISADAS.
#
# E a correção de relevância desta mesma PR AUMENTA a chance disso: descartada
# e ambígua saem do denominador. Uma defesa que abre o próximo buraco precisa
# fechar os dois.

from agent.get_trend import MINIMO_PARA_ROTULAR  # noqa: E402


def _rotulo(pos: int, neg: int) -> str:
    """O mesmo caminho de news_sentiment, sem a rede do yfinance."""
    total = pos + neg
    score = round((pos - neg) / total, 2) if total else 0.0
    if total < MINIMO_PARA_ROTULAR:
        return "neutro"
    return "positivo" if score > 0.25 else "negativo" if score < -0.25 else "misto"


def test_uma_manchete_nao_rotula_o_fluxo():
    """O caso NVDA: 1 positiva, 0 negativas, score 1,00 — e ainda assim não
    dá para dizer que o fluxo é positivo."""
    assert _rotulo(1, 0) == "neutro"


def test_duas_concordando_ainda_nao_bastam():
    """Com duas o score já dá 1,00 e uma virar do outro lado zera tudo."""
    assert _rotulo(2, 0) == "neutro"


def test_no_piso_o_rotulo_volta_a_afirmar():
    assert _rotulo(3, 0) == "positivo"
    assert _rotulo(0, 3) == "negativo"


def test_o_piso_nao_engole_amostra_grande():
    """O falso negativo aqui seria caro: uma tela que nunca afirma nada é tão
    inútil quanto uma que afirma qualquer coisa."""
    assert _rotulo(6, 1) == "positivo"
    assert _rotulo(1, 6) == "negativo"
    assert _rotulo(4, 3) == "misto"


def test_sem_manchete_classificada_e_neutro():
    assert _rotulo(0, 0) == "neutro"


def test_o_piso_esta_nomeado_e_nao_enterrado():
    """É julgamento, não teorema — tem que dar para achar e discutir."""
    assert isinstance(MINIMO_PARA_ROTULAR, int) and MINIMO_PARA_ROTULAR >= 2


# ═══ MRVL, 27/08/2026 — "positivo" com três manchetes de queda na tela ═════
#
# A Análise Rápida publicou "Notícias (positivo)" e "alta forte CONFIRMADA por
# fluxo de notícias positivo" com estas quatro manchetes em destaque:
#
#     Earnings live updates: Marvell stock falls despite strong earnings...
#     Jackson Hole Symposium kicks off, Marvell & dollar stores report earnings
#     Marvell Stock Sinks as Big Expectations Outweigh Solid Earnings
#     Marvell Shares Slide After Hours
#
# Três falam explicitamente de queda. O rótulo saiu positivo porque "slide"
# não estava em lista nenhuma: a manchete empatou 0-0 e virou MISTO, fora do
# denominador. Sobrou 2 positivas (que nem aparecem nos destaques, porque
# `destaques = scored[:4]` é ordem do feed) contra 1 negativa -> score 0,33.
#
# A própria prosa da IA se contradizia duas vezes no mesmo relatório:
# "confirmada por um fluxo de notícias positivo" e, dois parágrafos abaixo,
# "Manchetes recentes foram mistas a negativas".

MRVL_FALLS = ("Earnings live updates: Marvell stock falls despite strong "
              "earnings and guidance")
MRVL_JACKSON = ("Jackson Hole Symposium kicks off, Marvell & dollar stores "
                "report earnings: What to Watch")
MRVL_SINKS = "Marvell Stock Sinks as Big Expectations Outweigh Solid Earnings"
MRVL_SLIDE = "Marvell Shares Slide After Hours"


def test_slide_e_queda_e_nao_empate():
    """O termo que faltava. Sem ele a manchete empatava 0-0 e saía MISTO --
    o mesmo 'apagar notícia em silêncio' que o comentário do casamento por
    palavra inteira já descrevia."""
    assert _NEGATIVE_RE.search(MRVL_SLIDE.lower())
    assert _classificar(MRVL_SLIDE, "MRVL") == "negativo"


@pytest.mark.parametrize("titulo", [
    "Marvell Shares Slide After Hours",
    "MRVL Stock Slips After-Hours on Margin Outlook",
    "Marvell slipped 6% in extended trading",
])
def test_a_familia_de_queda_esta_completa(titulo):
    """slide/slip são o MESMO movimento de preço que fall/drop/sink/slump,
    que já estavam na lista. Fechar a lacuna de uma família representada não
    é o crescimento de lista contra o qual o código adverte."""
    assert _NEGATIVE_RE.search(titulo.lower())


def test_as_quatro_manchetes_de_mrvl_nao_dao_positivo():
    tons = [_classificar(t, "MRVL")
            for t in (MRVL_FALLS, MRVL_JACKSON, MRVL_SINKS, MRVL_SLIDE)]
    # "falls despite strong" tem ressalva; a do Jackson Hole não pontua nada.
    assert tons == ["misto", "misto", "negativo", "negativo"]
    assert tons.count("positivo") == 0


def test_o_rotulo_do_dia_deixa_de_ser_positivo():
    """O placar real da tela: as 2 positivas que o feed trouxe (e que nem
    apareceram nos destaques) contra as negativas das manchetes exibidas.
    Antes: 2x1 -> score 0,33 -> "positivo". Depois: 2x2 -> 0,0 -> "misto"."""
    tons = [_classificar(t, "MRVL")
            for t in (MRVL_FALLS, MRVL_JACKSON, MRVL_SINKS, MRVL_SLIDE)]
    negativas = tons.count("negativo")
    assert _rotulo(2, negativas) == "misto"
    # e é o "misto" que faz a confluência dizer "sem confirmação nem
    # divergência" em vez de "CONFIRMADA por fluxo de notícias positivo".
    assert _rotulo(2, 1) == "positivo", "o placar antigo, para contraste"


def test_a_lista_nova_nao_inverte_manchete_de_alta():
    """O risco que o comentário do código nomeia: cada termo novo é uma
    chance de virar o rótulo do lado errado. Manchete afirmativa de alta
    continua positiva."""
    for titulo in ("Marvell beats and raises guidance",
                   "Marvell surges on record data center growth",
                   "Marvell upgraded to buy rating"):
        assert _classificar(titulo, "MRVL") == "positivo", titulo


# ═══ AOSL, 28/08/2026 — "Reports Q4 Loss, Beats Revenue Estimates" ═════════
#
# Apontado por auditoria externa (verificado contra o código antes de agir,
# como sempre): a manchete saiu 1x0 POSITIVA porque "beats" pontuava e
# "loss" -- o substantivo do MESMO resultado que "miss/misses" já cobre --
# não pontuava nada em lista nenhuma. Mesmo padrão do slide/slip: família já
# representada (não bater a expectativa), só faltava o substantivo.

AOSL_LOSS_BEATS = "Alpha and Omega Semiconductor (AOSL) Reports Q4 Loss, Beats Revenue Estimates"


def test_loss_e_o_substantivo_de_miss():
    """Sem o termo, a manchete empatava 0-0 antes de "beats" pontuar --
    "loss" e "miss" descrevem o mesmo resultado (não bater expectativa)."""
    assert _NEGATIVE_RE.search(AOSL_LOSS_BEATS.lower())


def test_loss_e_beats_juntos_dao_misto_nao_positivo():
    assert _classificar(AOSL_LOSS_BEATS, "AOSL") == "misto"


def test_a_lista_nova_de_loss_nao_inverte_manchete_de_alta():
    """Mesma guarda de sempre: termo novo não pode virar o rótulo do lado
    errado numa manchete que só afirma alta."""
    for titulo in ("Alpha and Omega Semiconductor (AOSL) beats and raises guidance",
                   "Alpha and Omega Semiconductor (AOSL) surges on record margins"):
        assert _classificar(titulo, "AOSL") == "positivo", titulo


# ═══ AOSL, 28/08/2026 — "sem notícias favoráveis" ao lado de "1+/0-/2~" ═══
#
# Apontado por duas auditorias externas independentes e confirmado contra o
# código: `news_dir` vira 0 sempre que a amostra é menor que
# MINIMO_PARA_ROTULAR, mesmo com `positivas` > 0 -- é o mesmo motivo do
# `label` ficar "neutro" com 1 manchete positiva sozinha. "Sinal: venda --
# técnico de baixa forte sem notícias favoráveis" ao lado de um placar
# mostrando "1+/0-/2~" lê como o sistema negando um dado que ele mesmo
# mostrou. `_amostra_insuficiente_nota` acrescenta a ressalva só quando ela
# é necessária: contagem > 0 E amostra abaixo do piso.

from agent.get_trend import _amostra_insuficiente_nota  # noqa: E402


def test_nota_aparece_com_favoravel_isolado_e_amostra_pequena():
    news = {"positivas": 1, "negativas": 0, "classificadas": 1}
    nota = _amostra_insuficiente_nota(news, "positivas", "favorável")
    assert "1 notícia" in nota
    assert "favorável" in nota


def test_nota_se_cala_sem_contagem():
    news = {"positivas": 0, "negativas": 0, "classificadas": 0}
    assert _amostra_insuficiente_nota(news, "positivas", "favorável") == ""


def test_nota_se_cala_com_amostra_suficiente():
    """Com amostra >= MINIMO_PARA_ROTULAR o rótulo já afirma direção de
    verdade -- "sem notícias X" deixa de ser uma simplificação enganosa."""
    news = {"positivas": 3, "negativas": 0, "classificadas": 3}
    assert _amostra_insuficiente_nota(news, "positivas", "favorável") == ""


def test_nota_conta_o_campo_pedido_nao_o_outro():
    """Pedir a nota do lado CONTRÁRIO ao sinal (ex.: negativas na venda) não
    pode disparar por causa das positivas."""
    news = {"positivas": 1, "negativas": 0, "classificadas": 1}
    assert _amostra_insuficiente_nota(news, "negativas", "contrária") == ""
