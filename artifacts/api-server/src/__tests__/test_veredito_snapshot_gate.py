"""O snapshot do veredito entrega fatos fechados; a IA explica, não recalcula.

Auditoria de 26/08/2026 sobre o veredito das 19:07. Quatro erros além dos que
o validador pegou, todos da mesma raiz -- o texto decidia com números que não
estavam no snapshot, então nada era conferível:

  1. NVDA: "Earnings estão longe (nov/dez)" com o balanço saindo NO DIA.
  2. BABA: 119,83 contra suporte $126 descrito como "ainda acima".
  3. NVDA: variação DO DIA (-1,59%) rotulada de "reação média nos 21 pregões
     pós-earnings".
  4. WOLF: RSI 31,02 com o dado em 44,90 (este o validador pegou -- porque o
     RSI estava no snapshot; MACD/ATR/Bollinger não estavam).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.veredito_validator import (  # noqa: E402
    EARNINGS_PROXIMO_DIAS,
    validate_snapshot,
)


def _snap(**extras):
    base = {
        "as_of": "2026-08-26",
        "quotes": {"BABA": {"price": 119.83, "previous_close": 119.44,
                            "change_percent": 0.33, "as_of": "2026-08-26"}},
        "technicals": {"BABA": {"rsi": 47.08, "rsi_date": "2026-08-26",
                                "pct_above_sma50": -5.0, "atr_pct": 3.2,
                                "macd_hist": -1.3715,
                                "macd_direcao": "piorando"}},
        "earnings": {},
    }
    base.update(extras)
    return base


def _sinais(rep):
    return {s.code: s.message for s in rep.signals}


# ── gate de balanço iminente ────────────────────────────────────────────────

def test_balanco_hoje_vira_fato_com_a_regra_colada():
    rep = validate_snapshot(_snap(earnings={"BABA": "2026-08-26"}))
    msg = _sinais(rep).get("EVENTO_IMINENTE", "")
    assert "0 dia(s)" in msg
    assert "NÃO ocorreu" in msg
    assert "COMPRAR/AUMENTAR" in msg


def test_balanco_fora_da_janela_nao_gera_gate():
    rep = validate_snapshot(_snap(earnings={"BABA": "2026-11-24"}))
    assert "EVENTO_IMINENTE" not in _sinais(rep)


def test_a_janela_do_gate_e_a_do_veto_de_compra():
    dentro = validate_snapshot(_snap(
        earnings={"BABA": f"2026-08-{26 + EARNINGS_PROXIMO_DIAS:02d}"}))
    fora = validate_snapshot(_snap(
        earnings={"BABA": f"2026-08-{27 + EARNINGS_PROXIMO_DIAS:02d}"}))
    assert "EVENTO_IMINENTE" in _sinais(dentro)
    assert "EVENTO_IMINENTE" not in _sinais(fora)


# ── o lado do nível é conta, não leitura ────────────────────────────────────

def test_o_caso_baba_sai_com_o_lado_calculado():
    rep = validate_snapshot(_snap(plano_de_saida={"BABA": [{
        "acao": "Vender se quebrar suporte $126 (pullback risk)",
        "data_alvo": "2026-08-22", "fase": None, "nivel": 126.0}]}))
    msg = _sinais(rep).get("NIVEL_DO_PLANO", "")
    assert "ABAIXO" in msg and "$126.00" in msg
    assert "-4.90%" in msg
    # distância em ATR: 4,90 / 3,2 = 1,5
    assert "1.5 ATR" in msg


def test_preco_acima_do_nivel_diz_acima():
    rep = validate_snapshot(_snap(
        quotes={"BABA": {"price": 130.0, "previous_close": 129.0,
                         "change_percent": 0.78, "as_of": "2026-08-26"}},
        plano_de_saida={"BABA": [{"acao": "Vender se quebrar suporte $126",
                                  "data_alvo": None, "fase": None,
                                  "nivel": 126.0}]}))
    assert "ACIMA" in _sinais(rep).get("NIVEL_DO_PLANO", "")


def test_item_sem_nivel_numerico_fica_em_silencio():
    rep = validate_snapshot(_snap(plano_de_saida={"BABA": [{
        "acao": "Vender imediatamente -- stop-loss acionado",
        "data_alvo": None, "fase": None, "nivel": None}]}))
    assert "NIVEL_DO_PLANO" not in _sinais(rep)


def test_sem_atr_a_distancia_sai_so_em_pct():
    tec = {"BABA": {"rsi": 47.0, "rsi_date": "2026-08-26",
                    "pct_above_sma50": -5.0}}
    rep = validate_snapshot(_snap(
        technicals=tec,
        plano_de_saida={"BABA": [{"acao": "stop $126", "data_alvo": None,
                                  "fase": None, "nivel": 126.0}]}))
    msg = _sinais(rep).get("NIVEL_DO_PLANO", "")
    assert "-4.90%" in msg and "ATR" not in msg


# ── reação a earnings nomeada ───────────────────────────────────────────────

def test_reacao_fixada_separa_runup_de_reacao():
    rep = validate_snapshot(_snap(reacao_earnings={"BABA": {
        "dias_ate_earnings": 1, "threshold_pct": 7.23,
        "reacao_abs_media_pct": 3.35, "reacao_media_pct": -2.27,
        "gap_abs_medio_pct": 2.49, "n_eventos": 7,
        "runup_ate_agora_pct": 8.14, "estado": "neutro"}}))
    msg = _sinais(rep).get("REACAO_EARNINGS_FIXADA", "")
    assert "ATÉ AGORA" in msg and "+8.14%" in msg
    assert "|reação média| 3.35%" in msg and "±7.23%" in msg
    assert "variação do dia não é reação histórica" in msg


# ── MACD com direção ────────────────────────────────────────────────────────

def test_macd_sai_com_o_delta():
    rep = validate_snapshot(_snap())
    msg = _sinais(rep).get("MACD_FIXADO", "")
    assert "-1.3715" in msg and "piorando" in msg


def test_macd_sem_direcao_fica_em_silencio():
    tec = {"BABA": {"rsi": 47.0, "rsi_date": "2026-08-26",
                    "pct_above_sma50": -5.0, "macd_hist": -1.37}}
    rep = validate_snapshot(_snap(technicals=tec))
    assert "MACD_FIXADO" not in _sinais(rep)


# ── os fatos chegam ao prompt ───────────────────────────────────────────────

def test_os_sinais_entram_no_prompt_block():
    rep = validate_snapshot(_snap(
        earnings={"BABA": "2026-08-26"},
        plano_de_saida={"BABA": [{"acao": "suporte $126", "data_alvo": None,
                                  "fase": None, "nivel": 126.0}]}))
    bloco = rep.prompt_block()
    assert "nao recalcule" in bloco
    assert "EVENTO_IMINENTE" not in bloco  # o codigo nao vaza, a mensagem vai
    assert "balanço em 0 dia(s)" in bloco and "ABAIXO" in bloco


# ── o nível numérico é extraído do texto do item ────────────────────────────

def test_nivel_numerico_e_extraido_do_texto_do_item(monkeypatch):
    from agent import llm_runtime as gerador

    monkeypatch.setattr(gerador.t, "get_exit_plan_items", lambda: [
        {"ticker": "BABA", "status": "pending",
         "action": "Vender se quebrar suporte $126 (pullback risk)",
         "targetDate": "2026-08-22", "phaseLabel": None},
        {"ticker": "ADI", "status": "pending",
         "action": "Vender 50% em $390 (resistência) com stop em $370",
         "targetDate": "2026-08-27", "phaseLabel": None},
        {"ticker": "ARM", "status": "pending",
         "action": "Vender imediatamente — stop-loss acionado",
         "targetDate": "2026-08-20", "phaseLabel": None},
    ])
    plano = gerador._plano_de_saida_do_snapshot(["BABA", "ADI", "ARM"])
    assert plano["BABA"][0]["nivel"] == 126.0
    # dois números no texto: vale o PRIMEIRO, que é o gatilho da ação
    assert plano["ADI"][0]["nivel"] == 390.0
    # sem número, sem nível — e sem estourar
    assert plano["ARM"][0]["nivel"] is None


# ═══ P2 — força e direção da tendência, e o risco redundante ═══════════════

def test_estrutura_fixada_junta_inclinacao_estrutura_e_adx():
    tec = {"NVDA": {"rsi": 46.3, "rsi_date": "2026-08-26",
                    "pct_above_sma50": 0.92, "sma50_inclinacao": "caindo",
                    "sma20_inclinacao": "caindo", "estrutura": "alta",
                    "adx_14": 13.0, "plus_di": 18.0, "minus_di": 22.0,
                    "adx_direcao": "caindo"}}
    rep = validate_snapshot(_snap(technicals=tec))
    msg = _sinais(rep).get("ESTRUTURA_FIXADA", "")
    assert "MM50 caindo" in msg and "estrutura alta" in msg
    # ADX 13 = "muito fraca": o quadro que desmonta "alta forte" de posição
    assert "ADX 13 (muito fraca" in msg and "-DI>+DI" in msg


@pytest.mark.parametrize("adx,rotulo", [
    (13.0, "muito fraca"), (17.0, "fraca"), (22.0, "surgindo"),
    (31.0, "relevante"), (45.0, "muito forte"),
])
def test_a_escala_do_adx(adx, rotulo):
    tec = {"NVDA": {"rsi": 50.0, "rsi_date": "2026-08-26",
                    "pct_above_sma50": 1.0, "adx_14": adx}}
    rep = validate_snapshot(_snap(technicals=tec))
    assert rotulo in _sinais(rep).get("ESTRUTURA_FIXADA", "")


def test_sem_dado_novo_nao_ha_sinal_de_estrutura():
    tec = {"NVDA": {"rsi": 50.0, "rsi_date": "2026-08-26",
                    "pct_above_sma50": 1.0}}
    rep = validate_snapshot(_snap(technicals=tec))
    assert "ESTRUTURA_FIXADA" not in _sinais(rep)


def test_correlacao_alta_vira_sinal():
    rep = validate_snapshot(_snap(
        correlacoes_carteira=[{"a": "ARM", "b": "MRVL", "corr": 0.82}]))
    msg = _sinais(rep).get("CORRELACAO_ALTA", "")
    assert "ARM" in msg and "MRVL" in msg and "0.82" in msg
    assert "mesmo trade" in msg


def test_sem_par_acima_do_corte_nao_ha_sinal():
    """Hoje o par mais alto da carteira real é 0,56 — abaixo do corte, e
    silêncio é o certo: correlação moderada não é 'o mesmo trade'."""
    rep = validate_snapshot(_snap())
    assert "CORRELACAO_ALTA" not in _sinais(rep)


def test_correlacao_desconhecida_nao_vira_zero(monkeypatch):
    """O overlay não cobre o par → o par simplesmente não entra. Inventar 0
    seria pior que calar."""
    from agent import llm_runtime as gerador
    import agent.radar_ia_2026 as radar
    monkeypatch.setattr(radar, "correlacao", lambda a, b: None)
    assert gerador._correlacoes_da_carteira(["AAA", "BBB"]) == []
