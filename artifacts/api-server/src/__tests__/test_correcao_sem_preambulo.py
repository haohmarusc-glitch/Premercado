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

from agent import llm_runtime as agent_module
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


# ── Avisos do sistema sobrevivem à reescrita ─────────────────────────────────
#
# Produção 04/08: o deadline forçou o turno final sem ferramentas, a run acabou
# sem gravar observação nenhuma (elas viraram texto DENTRO do relatório), o loop
# anexou o aviso -- e o validador de rótulo disparou por causa do HCC. O modelo
# reescreveu o relatório sem o aviso, porque o aviso nunca foi texto dele.
#
# O usuário recebeu um relatório de aparência perfeita, sem nenhuma indicação de
# que a memória do dia não tinha sido salva.

_COM_AVISO = _ORIGINAL + "\n\n[Aviso: 3 ativo(s) exigido(s) ficaram sem observação nesta execução: AVGO, MRVL, SKHY. (0 observações foram salvas no total.)]"


def test_aviso_apagado_pela_reescrita_e_reanexado():
    corrigido = _ORIGINAL.replace("🔴", "🟡")  # o modelo devolve SEM o aviso
    saida = agent_module._texto_da_correcao(_resp(corrigido), _COM_AVISO)
    assert "🟡" in saida
    assert "ficaram sem observação" in saida
    assert "AVGO, MRVL, SKHY" in saida


def test_aviso_nao_e_duplicado_quando_o_modelo_o_mantem():
    corrigido = _COM_AVISO.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(_resp(corrigido), _COM_AVISO)
    assert saida.count("ficaram sem observação") == 1


def test_varios_avisos_sao_todos_preservados():
    original = _ORIGINAL + (
        "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"
        "\n\n[Aviso: 1 ativo(s) exigido(s) ficaram sem observação nesta execução: HCC.]"
    )
    saida = agent_module._texto_da_correcao(_resp(_ORIGINAL.replace("🔴", "🟡")), original)
    assert "limite de turnos atingido" in saida
    assert "ficaram sem observação" in saida


def test_relatorio_sem_aviso_nenhum_nao_ganha_nada():
    corrigido = _ORIGINAL.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(_resp(corrigido), _ORIGINAL)
    assert "[Aviso:" not in saida


def test_preambulo_e_aviso_sao_tratados_na_mesma_passada():
    """O caso real de produção tinha os dois: o modelo põe conversa na frente e
    perde o aviso no fim."""
    corrigido = _PREAMBULO + "\n\n" + _ORIGINAL.replace("🔴", "🟡")
    saida = agent_module._texto_da_correcao(_resp(corrigido), _COM_AVISO)
    assert not saida.startswith("Compreendido")
    assert "ficaram sem observação" in saida
