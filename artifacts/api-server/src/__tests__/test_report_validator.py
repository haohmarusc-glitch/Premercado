"""
Testes de report_validator.py — enforcement dos gates de rótulo do relatório
diário.

Contexto: a rubrica de rótulo (agent.py, seção "RÓTULO POR ATIVO") define quatro
condições que proíbem 🟢. Prompt é pedido, não garantia -- este módulo verifica
o texto gerado e dispara um retry de correção quando o modelo rotula verde um
ativo com gate ativo. Os dois casos que motivaram tudo estão cobertos aqui como
teste de regressão: ARM (queda + IV de evento) e SKHY (queda) no relatório de
02/08.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_report_validator.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json

import pytest

from agent.report_validator import (
    collect_tool_result,
    correction_prompt,
    lint_report,
    new_snapshot,
)


# ------------------------------------------------------------- coleta ---


def test_coleta_quote_technicals_options_e_earnings():
    snap = new_snapshot()
    collect_tool_result(snap, "get_stock_data", {}, json.dumps(
        {"ticker": "NVDA", "change_pct": 2.9, "as_of": "2026-08-01"}))
    collect_tool_result(snap, "get_technical_indicators", {}, json.dumps(
        {"ticker": "NVDA", "rsi_date": "2026-08-01", "atr_pct": 3.1}))
    collect_tool_result(snap, "get_options_data", {}, json.dumps(
        {"ticker": "NVDA", "atm_iv_pct": 44.0}))
    collect_tool_result(snap, "get_earnings_calendar", {}, json.dumps(
        [{"ticker": "NVDA", "days_until_earnings": 24}]))

    assert snap["quotes"]["NVDA"]["change_pct"] == 2.9
    assert snap["technicals"]["NVDA"]["atr_pct"] == 3.1
    assert snap["options"]["NVDA"]["atm_iv_pct"] == 44.0
    assert snap["earnings"]["NVDA"] == 24


def test_coleta_ignora_resultado_de_erro():
    """Ferramenta que falhou não contribui -- e gate sem dado não vira violação."""
    snap = new_snapshot()
    collect_tool_result(snap, "get_stock_data", {}, json.dumps(
        {"ticker": "XYZ", "error": "Dados insuficientes"}))
    assert snap["quotes"] == {}


def test_coleta_nao_quebra_com_resultado_nao_json():
    snap = new_snapshot()
    collect_tool_result(snap, "get_stock_data", {}, "isto não é json")
    assert snap["quotes"] == {}


# -------------------------------------------------------------- gates ---


def _relatorio(ticker: str, rotulo: str) -> str:
    return f"""# Relatório pré-mercado

## {ticker}

{rotulo} — leitura do dia.

Texto de análise do ativo.

## Outro ativo qualquer

Nada a ver.
"""


def test_verde_com_variacao_negativa_e_erro():
    """SKHY 02/08: 🟢 com -3,5% no dia."""
    snap = new_snapshot()
    snap["quotes"]["SKHY"] = {"change_pct": -3.5, "as_of": "2026-08-01"}
    rep = lint_report(_relatorio("SKHY", "🟢"), snap)
    assert rep.has_errors
    assert "variação do dia" in rep.summary()


def test_verde_com_iv_de_evento_e_erro():
    """ARM 02/08: 🟢 com IV extrema pro próprio ativo.

    atr_pct 2.0% -> vol anualizada ~31,7%; o gate exige IV >= 2x isso (~63,5%).
    """
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["ARM"] = {"rsi_date": "2026-08-01", "atr_pct": 2.0}
    snap["options"]["ARM"] = {"atm_iv_pct": 90.0}
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    assert rep.has_errors
    assert "IV ATM" in rep.summary()


def test_iv_alta_mas_normal_pro_ativo_nao_e_gate():
    """96% é IV alta em termos absolutos, mas não é evento num ativo de ATR% 4
    (vol anualizada ~63%; o gate só dispara a partir de ~127%). É a razão de o
    corte ser por ativo em vez de número fixo."""
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["technicals"]["SMCI"] = {"rsi_date": "2026-08-01", "atr_pct": 4.0}
    snap["options"]["SMCI"] = {"atm_iv_pct": 96.0}
    rep = lint_report(_relatorio("SMCI", "🟢"), snap)
    assert not rep.has_errors


def test_verde_com_earnings_proximo_e_erro():
    snap = new_snapshot()
    snap["quotes"]["HCC"] = {"change_pct": 0.8, "as_of": "2026-08-01"}
    snap["earnings"]["HCC"] = 3
    rep = lint_report(_relatorio("HCC", "🟢"), snap)
    assert rep.has_errors
    assert "earnings em 3 dias" in rep.summary()


def test_verde_com_tecnico_defasado_e_erro():
    """MRVL 02/08: MM200 de pregão anterior sustentando o veredito."""
    snap = new_snapshot()
    snap["quotes"]["MRVL"] = {"change_pct": 2.3, "as_of": "2026-08-01"}
    snap["technicals"]["MRVL"] = {"rsi_date": "2026-07-30", "atr_pct": 3.0}
    rep = lint_report(_relatorio("MRVL", "🟢"), snap)
    assert rep.has_errors
    assert "anterior ao pregão" in rep.summary()


def test_dois_gates_pedem_vermelho():
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.8, "as_of": "2026-08-01"}
    snap["earnings"]["ARM"] = 2
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    assert rep.has_errors
    assert "deveria ser 🔴" in rep.summary()


def test_um_gate_pede_amarelo():
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.8, "as_of": "2026-08-01"}
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    assert "deveria ser 🟡" in rep.summary()


@pytest.mark.parametrize("rotulo", ["🟡", "🔴"])
def test_amarelo_e_vermelho_nunca_violam(rotulo):
    """Os gates só proíbem 🟢 -- rótulo mais conservador é sempre aceitável."""
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.8, "as_of": "2026-08-01"}
    snap["earnings"]["ARM"] = 1
    rep = lint_report(_relatorio("ARM", rotulo), snap)
    assert not rep.has_errors


def test_verde_sem_gate_passa():
    snap = new_snapshot()
    snap["quotes"]["GOOGL"] = {"change_pct": 6.7, "as_of": "2026-08-01"}
    snap["technicals"]["GOOGL"] = {"rsi_date": "2026-08-01", "atr_pct": 2.0}
    snap["earnings"]["GOOGL"] = 40
    rep = lint_report(_relatorio("GOOGL", "🟢"), snap)
    assert not rep.has_errors


def test_ativo_sem_secao_no_texto_e_ignorado():
    """Grupo B aparece só como preço numa tabela, sem seção nem rótulo."""
    snap = new_snapshot()
    snap["quotes"]["INTC"] = {"change_pct": -2.0, "as_of": "2026-08-01"}
    rep = lint_report("# Relatório\n\n| INTC | -2,0% |\n", snap)
    assert not rep.has_errors


def test_ticker_nao_casa_dentro_de_outra_palavra():
    """'MU' não pode casar dentro de 'MULTI' -- senão o gate seria avaliado
    contra a seção errada."""
    snap = new_snapshot()
    snap["quotes"]["MU"] = {"change_pct": -5.9, "as_of": "2026-08-01"}
    texto = "# Relatório\n\n## MULTI Setores\n\n🟢 — visão geral positiva.\n"
    rep = lint_report(texto, snap)
    assert not rep.has_errors


def test_secao_para_no_proximo_cabecalho_de_mesmo_nivel():
    """O rótulo de um ativo não pode vazar pro ativo seguinte."""
    snap = new_snapshot()
    snap["quotes"]["AAA"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["quotes"]["BBB"] = {"change_pct": -4.0, "as_of": "2026-08-01"}
    texto = (
        "# Relatório\n\n## AAA\n\n🟢 — tudo certo.\n\n"
        "## BBB\n\n🟡 — cautela.\n"
    )
    rep = lint_report(texto, snap)
    # AAA é verde legítimo; BBB é amarelo. Nenhuma violação.
    assert not rep.has_errors


def test_gate_nao_dispara_sem_dado():
    """Ausência de dado nunca vira violação."""
    snap = new_snapshot()
    snap["quotes"]["NVDA"] = {"change_pct": None, "as_of": None}
    snap["technicals"]["NVDA"] = {"rsi_date": None, "atr_pct": None}
    rep = lint_report(_relatorio("NVDA", "🟢"), snap)
    assert not rep.has_errors


def test_prompt_de_correcao_lista_os_tickers():
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.8, "as_of": "2026-08-01"}
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    prompt = correction_prompt(rep)
    assert "ARM" in prompt
    assert "não altere o resto da análise" in prompt
