"""O retry de correção não pode vazar a fala do modelo pro relatório.

Visto em produção 03/08: o lint_report pegou uma violação de rubrica no rótulo
do SKHY, o retry corrigiu certo, e o relatório diário chegou ao usuário
começando por

    "Compreendido. Segue o relatório corrigido apenas no rótulo e justificativa
     de SKHY, mantendo o restante da análise e formato original."

O prompt já pedia "reescreva o relatório completo" e o modelo obedeceu -- só
pôs um acompanhamento na frente. Prompt pede, código garante.
"""
import pytest

from agent import agent as agent_module
from agent.provider import NormalizedResponse, TextBlock, ToolUseBlock


_ORIGINAL = """Análise Pré-Mercado: 03 de Agosto de 2026

Contexto Geral do Mercado:
O sentimento do mercado encontra-se Neutro.

SKHY 🔴
Justificativa: rótulo errado aqui.
"""

_PREAMBULO = (
    "Compreendido. Segue o relatório corrigido apenas no rótulo e "
    "justificativa de SKHY, mantendo o restante da análise e formato original."
)


def _resp(texto: str) -> NormalizedResponse:
    return NormalizedResponse(content=[TextBlock(text=texto)], stop_reason="end_turn")


def test_corta_o_preambulo_ancorando_na_abertura_do_original():
    corrigido = _ORIGINAL.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(_resp(_PREAMBULO + "\n\n" + corrigido), _ORIGINAL)
    assert saida is not None
    assert not saida.startswith("Compreendido")
    assert saida.startswith("Análise Pré-Mercado: 03 de Agosto de 2026")
    # E o conteúdo corrigido continua inteiro.
    assert "🟡" in saida
    assert "Contexto Geral do Mercado:" in saida


def test_texto_sem_preambulo_passa_intacto():
    corrigido = _ORIGINAL.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(_resp(corrigido), _ORIGINAL)
    assert saida == corrigido


def test_titulo_reescrito_devolve_intacto_em_vez_de_chutar():
    """Sem âncora não dá pra saber onde o relatório começa. Errar pra menos aqui
    é só manter o comportamento antigo; errar pra mais decapitaria o relatório."""
    outro = "Relatório Pré-Mercado (revisado) — 03/08\n\nContexto: tudo certo agora."
    saida = agent_module._texto_da_correcao(_resp(_PREAMBULO + "\n\n" + outro), _ORIGINAL)
    assert saida == _PREAMBULO + "\n\n" + outro


def test_nao_confunde_mencao_dentro_do_preambulo_com_a_abertura():
    """O preâmbulo cita o relatório; se a âncora fosse curta, casaria com ele e o
    corte sairia no lugar errado."""
    preambulo_citando = (
        "Compreendido. Análise Pré-Mercado ajustada conforme pedido, veja abaixo."
    )
    corrigido = _ORIGINAL.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(
        _resp(preambulo_citando + "\n\n" + corrigido), _ORIGINAL
    )
    assert saida.startswith("Análise Pré-Mercado: 03 de Agosto de 2026")
    assert "conforme pedido" not in saida


def test_sem_bloco_de_texto_devolve_none():
    """Aí o chamador fica com o original em vez de trocar por vazio."""
    resp = NormalizedResponse(
        content=[ToolUseBlock(id="t1", name="get_stock_data", input={})],
        stop_reason="tool_use",
    )
    assert agent_module._texto_da_correcao(resp, _ORIGINAL) is None


def test_bloco_so_com_espaco_nao_conta_como_texto():
    assert agent_module._texto_da_correcao(_resp("   \n\n  "), _ORIGINAL) is None


def test_original_sem_linha_com_corpo_nao_tenta_cortar():
    """Sem nada longo o bastante pra ancorar, não há decisão segura a tomar."""
    saida = agent_module._texto_da_correcao(_resp(_PREAMBULO + "\n\nx"), "a\nb\nc\n")
    assert saida == _PREAMBULO + "\n\nx"


@pytest.mark.parametrize("ancora", ["Análise Pré-Mercado: 03 de Agosto de 2026"])
def test_primeira_linha_util_pula_linhas_curtas(ancora):
    texto = "\n\n#\n---\n" + ancora + "\nresto\n"
    assert agent_module._primeira_linha_util(texto) == ancora


def test_primeira_linha_util_sem_candidata():
    assert agent_module._primeira_linha_util("a\n\nbb\n") == ""
