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
