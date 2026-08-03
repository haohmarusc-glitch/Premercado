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
    """🟢 com IV extrema pro próprio ativo. atr_pct 2.0% -> limiar 64%."""
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["ARM"] = {"rsi_date": "2026-08-01", "atr_pct": 2.0}
    snap["options"]["ARM"] = {"atm_iv_pct": 90.0}
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    assert rep.has_errors
    assert "IV ATM" in rep.summary()


def test_iv_alta_mas_normal_pro_ativo_nao_e_gate():
    """96% é IV alta em termos absolutos, mas não é evento num ativo de ATR% 4
    (limiar 128%). É a razão de o corte ser por ativo em vez de número fixo."""
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


def test_critico_mais_ativo_pede_vermelho():
    """earnings em 2 dias (crítico) + queda (ativo) = 🔴."""
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
def test_com_dois_gates_amarelo_e_vermelho_passam(rotulo):
    """Com 2 gates, 🔴 é o rótulo da rubrica e 🟡 é conservador demais mas
    aceitável -- o que os gates proíbem ali é 🟢."""
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.8, "as_of": "2026-08-01"}
    snap["earnings"]["ARM"] = 1
    rep = lint_report(_relatorio("ARM", rotulo), snap)
    assert not rep.has_errors


# ------------------------------------------------- rótulo inflado (🔴) ---
#
# O inverso do bug original: em vez de otimismo indevido, receio indevido.
# Visto em produção (02/08): ARM levou 🔴 alegando "dois gates ativos", sendo
# que o segundo era a IV -- e o próprio texto dizia que ela estava ABAIXO do
# limiar. O validador antigo só olhava 🟢, então isso passava calado.


def test_vermelho_com_um_gate_so_e_erro():
    """O caso ARM: um gate real (queda), 🔴 alegando dois."""
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": -0.77, "as_of": "2026-08-01"}
    snap["technicals"]["ARM"] = {"rsi_date": "2026-08-01", "atr_pct": 9.75}
    snap["options"]["ARM"] = {"atm_iv_pct": 103.6}  # bem abaixo de 32 x 9,75 = 312%
    rep = lint_report(_relatorio("ARM", "🔴"), snap)
    assert rep.has_errors
    assert "ROTULO_INFLADO" in rep.summary()
    assert "só sustentam 🟡" in rep.summary()


def test_vermelho_sem_gate_nenhum_e_erro():
    snap = new_snapshot()
    snap["quotes"]["GOOGL"] = {"change_pct": 6.7, "as_of": "2026-08-01"}
    snap["earnings"]["GOOGL"] = 40
    rep = lint_report(_relatorio("GOOGL", "🔴"), snap)
    assert rep.has_errors
    assert "nenhum" in rep.summary()


def test_vermelho_com_dois_gates_passa():
    """HCC 02/08: earnings em 3 dias + queda no dia = 🔴 legítimo."""
    snap = new_snapshot()
    snap["quotes"]["HCC"] = {"change_pct": -1.23, "as_of": "2026-08-01"}
    snap["earnings"]["HCC"] = 3
    rep = lint_report(_relatorio("HCC", "🔴"), snap)
    assert not rep.has_errors


def test_amarelo_com_um_gate_passa():
    """🟡 é o meio livre: um gate ativo é exatamente o caso dele."""
    snap = new_snapshot()
    snap["quotes"]["NVDA"] = {"change_pct": -0.5, "as_of": "2026-08-01"}
    rep = lint_report(_relatorio("NVDA", "🟡"), snap)
    assert not rep.has_errors


def test_amarelo_sem_gate_nenhum_passa():
    """Julgamento que nenhum gate cobre (volume fraco, manchete ambígua) é uso
    legítimo de 🟡 -- engessar isso tiraria a saída honesta para o receio."""
    snap = new_snapshot()
    snap["quotes"]["NVDA"] = {"change_pct": 2.9, "as_of": "2026-08-01"}
    snap["earnings"]["NVDA"] = 24
    rep = lint_report(_relatorio("NVDA", "🟡"), snap)
    assert not rep.has_errors


def test_limiar_de_iv_e_32x_atr_nao_16x():
    """A confusão real de 02/08: o modelo comparou IV contra atr_pct x 16 em
    NVDA, AVGO e ARM -- metade do limiar. IV entre 16x e 32x NÃO é gate."""
    snap = new_snapshot()
    snap["quotes"]["X"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["X"] = {"rsi_date": "2026-08-01", "atr_pct": 4.0}
    snap["options"]["X"] = {"atm_iv_pct": 100.0}  # 16x=64, 32x=128 -> no meio
    rep = lint_report(_relatorio("X", "🟢"), snap)
    assert not rep.has_errors, "IV entre 16x e 32x virou gate indevidamente"


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


# ----------------------------------------- gates novos e severidade ---
#
# G1 tier 6-14d, G5 extensão MM200, G9 manchete de risco e G4 short entram
# aqui. A severidade existe porque contagem simples colapsa quando os gates
# passam de meia dúzia: qualquer ativo em correção viraria 🔴 todo dia.


def test_earnings_entre_6_e_14_dias_e_ativo_nao_critico():
    """O caso SMCI de 02/08: 9 dias. Antes o modelo citava um gate "≤14d" que
    a rubrica não tinha; agora existe, mas como ATIVO -- sozinho dá 🟡."""
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["earnings"]["SMCI"] = 9
    assert not lint_report(_relatorio("SMCI", "🟡"), snap).has_errors
    rep = lint_report(_relatorio("SMCI", "🟢"), snap)
    assert rep.has_errors
    assert "deveria ser 🟡" in rep.summary()


def test_earnings_acima_de_14_dias_nao_e_gate():
    snap = new_snapshot()
    snap["quotes"]["NVDA"] = {"change_pct": 2.9, "as_of": "2026-08-01"}
    snap["earnings"]["NVDA"] = 24
    assert not lint_report(_relatorio("NVDA", "🟢"), snap).has_errors


def test_extensao_mm200_com_tecnico_fresco_e_gate():
    snap = new_snapshot()
    snap["quotes"]["ARM"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["ARM"] = {"rsi_date": "2026-08-01", "atr_pct": 3.0,
                                  "pct_above_sma200": 26.2}
    rep = lint_report(_relatorio("ARM", "🟢"), snap)
    assert rep.has_errors
    assert "acima da MM200" in rep.summary()


def test_extensao_mm200_defasada_nao_e_gate():
    """MRVL 02/08: "38,9% acima da MM200" era pico anterior. O relatório
    descartou com razão; gate sobre dado defasado é pior que gate nenhum."""
    snap = new_snapshot()
    snap["quotes"]["MRVL"] = {"change_pct": 2.3, "as_of": "2026-08-01"}
    snap["technicals"]["MRVL"] = {"rsi_date": "2026-07-30", "atr_pct": 3.0,
                                   "pct_above_sma200": 38.9}
    rep = lint_report(_relatorio("MRVL", "🟢"), snap)
    # o gate de frescor dispara, mas o de extensão NÃO pode aparecer
    assert "acima da MM200" not in rep.summary()
    assert "bloco técnico" in rep.summary()


def test_manchete_de_risco_binario_e_gate():
    """SMCI + investigação ITC/Netlist, que apareceu em 31/07 e 02/08."""
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["headlines"]["SMCI"] = [
        "Super Micro enfrenta investigação de patente da ITC sobre memória Netlist",
    ]
    rep = lint_report(_relatorio("SMCI", "🟢"), snap)
    assert rep.has_errors
    assert "risco binário" in rep.summary()


def test_manchete_neutra_nao_e_gate():
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["headlines"]["SMCI"] = ["Super Micro anuncia novo rack NVIDIA para IA"]
    assert not lint_report(_relatorio("SMCI", "🟢"), snap).has_errors


def test_short_alto_nao_rebaixa_cor():
    """G4 é "não perseguir", não gate de cor: a assimetria de squeeze corta
    pros dois lados, e rebaixar por ela assumiria uma direção."""
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["short"]["SMCI"] = {"short_pct_of_float": 19.6, "squeeze_risk": "moderado"}
    assert not lint_report(_relatorio("SMCI", "🟢"), snap).has_errors


def test_dois_ativos_sozinhos_nao_pedem_vermelho():
    """A contagem simples anterior transformaria isto em 🔴. SMCI 02/08 com
    earnings 9d + manchete ITC é 🟡 -- que é o que a leitura humana diz."""
    snap = new_snapshot()
    snap["quotes"]["SMCI"] = {"change_pct": 2.4, "as_of": "2026-08-01"}
    snap["earnings"]["SMCI"] = 9
    snap["headlines"]["SMCI"] = ["Investigação da ITC sobre patente"]
    assert not lint_report(_relatorio("SMCI", "🟡"), snap).has_errors
    rep = lint_report(_relatorio("SMCI", "🔴"), snap)
    assert rep.has_errors
    assert "ROTULO_INFLADO" in rep.summary()


def test_tres_ativos_pedem_vermelho():
    snap = new_snapshot()
    snap["quotes"]["X"] = {"change_pct": -1.0, "as_of": "2026-08-01"}
    snap["earnings"]["X"] = 9
    snap["headlines"]["X"] = ["Downgrade do banco"]
    assert not lint_report(_relatorio("X", "🔴"), snap).has_errors


def test_stale_sozinho_fica_amarelo_nao_vermelho():
    """NVDA 02/08: só técnico defasado. A tabela de referência diz 🟡."""
    snap = new_snapshot()
    snap["quotes"]["NVDA"] = {"change_pct": 2.9, "as_of": "2026-08-01"}
    snap["technicals"]["NVDA"] = {"rsi_date": "2026-07-31", "atr_pct": 3.0,
                                   "pct_above_sma200": 5.0}
    assert not lint_report(_relatorio("NVDA", "🟡"), snap).has_errors
    rep = lint_report(_relatorio("NVDA", "🔴"), snap)
    assert rep.has_errors
    assert "só sustentam 🟡" in rep.summary()


# --------------------------------------- IV suprimida na semana de earnings ---
#
# Na semana do resultado a IV está alta POR CAUSA do evento. Contar IV extrema
# E earnings ≤5d seria double-count: infla o 🔴 a partir de uma informação só,
# que é justamente o acúmulo que a severidade veio evitar.


def test_iv_nao_conta_com_earnings_em_ate_5_dias():
    snap = new_snapshot()
    snap["quotes"]["HCC"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["HCC"] = {"rsi_date": "2026-08-01", "atr_pct": 3.0}
    snap["options"]["HCC"] = {"atm_iv_pct": 200.0}  # muito acima de 32 x 3 = 96
    snap["earnings"]["HCC"] = 3
    rep = lint_report(_relatorio("HCC", "🟢"), snap)
    # earnings (crítico) aparece; IV não pode aparecer junto
    assert "earnings em 3 dias" in rep.summary()
    assert "IV ATM" not in rep.summary()
    # e sozinho o crítico só sustenta 🟡
    assert "deveria ser 🟡" in rep.summary()


def test_iv_volta_a_contar_fora_da_semana_de_earnings():
    snap = new_snapshot()
    snap["quotes"]["HCC"] = {"change_pct": 1.0, "as_of": "2026-08-01"}
    snap["technicals"]["HCC"] = {"rsi_date": "2026-08-01", "atr_pct": 3.0}
    snap["options"]["HCC"] = {"atm_iv_pct": 200.0}
    snap["earnings"]["HCC"] = 20
    rep = lint_report(_relatorio("HCC", "🟢"), snap)
    assert "IV ATM" in rep.summary()


def test_coleta_guarda_as_of_das_opcoes():
    """Sem as_of o gate de IV não sabe de quando é o número -- mesmo buraco
    que o get_stock_data tinha antes da #197."""
    snap = new_snapshot()
    collect_tool_result(snap, "get_options_data", {}, json.dumps(
        {"ticker": "NVDA", "atm_iv_pct": 44.0, "as_of": "2026-08-03"}))
    assert snap["options"]["NVDA"]["as_of"] == "2026-08-03"
