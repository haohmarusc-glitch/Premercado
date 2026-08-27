"""Os quatro erros do veredito das 19:07 de 26/08/2026 que escaparam.

O validador daquela geração pegou dois (RSI de WOLF, sinal de SKHY) — porque
esses dados estavam no snapshot. Estes quatro escaparam, cada um por um
buraco distinto, e cada teste abaixo carrega a frase real da tela.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.veredito_validator import (  # noqa: E402
    CONFIANCA_MAXIMA_NA_VESPERA,
    EARNINGS_PROXIMO_DIAS,
    lint_veredito,
    validar_bloco_estruturado,
)


def _snap(**extras):
    base = {
        "as_of": "2026-08-26",
        "quotes": {}, "technicals": {}, "earnings": {},
    }
    base.update(extras)
    return base


def _codigos(rep):
    return {i.code for i in rep.issues}


# ── 1. o lado elíptico (BABA) ───────────────────────────────────────────────

_PARAGRAFO_BABA = (
    "BABA está perto de suporte crítico: preço 119.83, suporte identificado "
    "em $126 (dados do plano). O plano de saída ordena \"vender se quebrar "
    "suporte $126\" — ainda acima, mas em risco.")

_SNAP_BABA = dict(quotes={"BABA": {"price": 119.83, "previous_close": 119.44,
                                   "change_percent": 0.33,
                                   "as_of": "2026-08-26"}})


def test_o_lado_eliptico_do_baba_cai():
    """'— ainda acima, mas em risco' sem preço na cláusula: o preço está no
    parágrafo, e 119,83 é MENOR que 126."""
    rep = lint_veredito(_PARAGRAFO_BABA, _snap(**_SNAP_BABA))
    assert "NIVEL_LADO_INVERTIDO" in _codigos(rep)


def test_o_lado_certo_eliptico_passa():
    rep = lint_veredito(_PARAGRAFO_BABA.replace("ainda acima", "ainda abaixo"),
                        _snap(**_SNAP_BABA))
    assert "NIVEL_LADO_INVERTIDO" not in _codigos(rep)


def test_referente_alheio_nao_e_o_nivel_do_plano():
    """'continua acima da MM50' nomeia o referente — não é o suporte."""
    rep = lint_veredito(
        "BABA: preço 119.83, suporte $110. O papel continua acima da MM50.",
        _snap(**_SNAP_BABA))
    assert "NIVEL_LADO_INVERTIDO" not in _codigos(rep)


def test_dois_precos_no_paragrafo_calam_o_eliptico():
    """Com dois preços no parágrafo não dá para saber a qual a elipse se
    refere — silêncio é o correto."""
    rep = lint_veredito(
        "BABA: preço 119.83 ontem, preço 127.10 agora, suporte $126 — "
        "ainda acima.", _snap(**_SNAP_BABA))
    assert "NIVEL_LADO_INVERTIDO" not in _codigos(rep)


# ── 2. earnings negado com o balanço na porta (NVDA) ────────────────────────

def test_earnings_longe_com_balanco_hoje_e_erro():
    rep = lint_veredito(
        "NVDA lidera o setor. Earnings estão longe (próximo em nov/dez, fora "
        "do horizonte deste veredito).",
        _snap(earnings={"NVDA": "2026-08-26"}))
    assert "EARNINGS_NEGADO_IMINENTE" in _codigos(rep)


def test_earnings_longe_quando_esta_longe_passa():
    rep = lint_veredito(
        "INTC: earnings estão longe (22/out).",
        _snap(earnings={"INTC": "2026-10-22"}))
    assert "EARNINGS_NEGADO_IMINENTE" not in _codigos(rep)


def test_falar_do_balanco_iminente_sem_nega_lo_passa():
    rep = lint_veredito(
        "NVDA reporta hoje após o fechamento; aguardar a reação.",
        _snap(earnings={"NVDA": "2026-08-26"}))
    assert "EARNINGS_NEGADO_IMINENTE" not in _codigos(rep)


# ── 3. variação do dia vestida de reação média (NVDA) ───────────────────────

_SNAP_REACAO = dict(
    quotes={"NVDA": {"price": 209.66, "previous_close": 213.05,
                     "change_percent": -1.59, "as_of": "2026-08-26"}},
    reacao_earnings={"NVDA": {"dias_ate_earnings": 0,
                              "reacao_abs_media_pct": 3.35,
                              "reacao_media_pct": -2.27,
                              "n_eventos": 7}})


def test_variacao_do_dia_como_reacao_media_e_erro():
    """A frase real: '-1,59% nos 21 pregões pós-earnings' — -1,59% é o
    pregão DO DIA."""
    rep = lint_veredito(
        "Historicamente, NVDA experimenta reação média de -1.59% nos 21 "
        "pregões pós-earnings (7 eventos).", _snap(**_SNAP_REACAO))
    assert "REACAO_E_VARIACAO_DO_DIA" in _codigos(rep)


def test_a_reacao_media_de_verdade_passa():
    rep = lint_veredito(
        "NVDA tem reação média de -2.27% pós-earnings (7 eventos).",
        _snap(**_SNAP_REACAO))
    assert "REACAO_E_VARIACAO_DO_DIA" not in _codigos(rep)


def test_sem_reacao_no_snapshot_a_coincidencia_nao_acusa():
    """Gate no dado: sem `reacao_earnings` não há fato para conferir, e um
    número que por acaso coincide com o dia não vira acusação."""
    snap = _snap(quotes=_SNAP_REACAO["quotes"])
    rep = lint_veredito(
        "NVDA experimenta reação média de -1.59% pós-earnings.", snap)
    assert "REACAO_E_VARIACAO_DO_DIA" not in _codigos(rep)


# ── 4. dois preços para o mesmo papel (ARM) ─────────────────────────────────

_SNAP_ARM = dict(quotes={"ARM": {"price": 251.06, "previous_close": 241.56,
                                 "change_percent": 3.93,
                                 "as_of": "2026-08-26"}})


def test_dois_precos_no_mesmo_paragrafo_avisam():
    """A frase real: EMA 261.2 vs preço 250.71 ... SMA50 294.28 vs preço
    251.06 — dois preços para o mesmo papel."""
    rep = lint_veredito(
        "ARM: EMA 261.20 vs preço 250.71, SMA50 294.28 vs preço 251.06.",
        _snap(**_SNAP_ARM))
    assert "PRECO_INCONSISTENTE" in _codigos(rep)


def test_um_preco_so_nao_avisa():
    rep = lint_veredito("ARM: preço 251.06, subiu 3.93% no dia.",
                        _snap(**_SNAP_ARM))
    assert "PRECO_INCONSISTENTE" not in _codigos(rep)


def test_vwap_perto_do_preco_nao_e_segundo_preco():
    """VWAP a 245,54 fica fora da janela de 1% — e mesmo um nível DENTRO da
    janela é excluído pelo contexto."""
    rep = lint_veredito(
        "ARM: preço 251.06, VWAP 249.90, alvo 250.00.", _snap(**_SNAP_ARM))
    assert "PRECO_INCONSISTENTE" not in _codigos(rep)


# ── 5. o gate no bloco ──────────────────────────────────────────────────────

def _bloco(acao, conf=0.6, codes=("EARNINGS_PROXIMO",)):
    return {"tickers": [{"ticker": "NVDA", "action": acao,
                         "confidence": conf, "reason_codes": list(codes)}]}

_SNAP_VESPERA = {"as_of": "2026-08-26", "quotes": {"NVDA": {}},
                 "technicals": {}, "earnings": {"NVDA": "2026-08-26"}}


@pytest.mark.parametrize("acao", ["COMPRAR", "AUMENTAR"])
def test_entrada_na_vespera_cai_mesmo_declarada(acao):
    """Declarar EARNINGS_PROXIMO tornava a compra consciente; consciente não
    é sustentada — a técnica não sabe o número que sai em horas."""
    rep = validar_bloco_estruturado(_bloco(acao), _SNAP_VESPERA)
    assert "BLOCO_DIRECIONAL_NA_VESPERA" in _codigos(rep)


def test_vender_na_vespera_nao_cai():
    """Tirar risco não espera balanço — o stop do plano continua valendo."""
    rep = validar_bloco_estruturado(
        _bloco("VENDER", conf=0.95, codes=("PLANO_DE_SAIDA",)), _SNAP_VESPERA)
    assert "BLOCO_DIRECIONAL_NA_VESPERA" not in _codigos(rep)
    assert "BLOCO_CONFIANCA_NA_VESPERA" not in _codigos(rep)


def test_manter_com_confianca_alta_na_vespera_avisa():
    rep = validar_bloco_estruturado(_bloco("MANTER", conf=0.9), _SNAP_VESPERA)
    assert "BLOCO_CONFIANCA_NA_VESPERA" in _codigos(rep)


def test_manter_com_confianca_honesta_passa():
    """O MRVL do dia saiu 45% — abaixo do teto, como deve."""
    rep = validar_bloco_estruturado(_bloco("MANTER", conf=0.45), _SNAP_VESPERA)
    assert "BLOCO_CONFIANCA_NA_VESPERA" not in _codigos(rep)


def test_fora_da_vespera_nada_disso_roda():
    snap = {**_SNAP_VESPERA, "earnings": {"NVDA": "2026-11-19"}}
    rep = validar_bloco_estruturado(_bloco("COMPRAR", conf=0.9), snap)
    assert "BLOCO_DIRECIONAL_NA_VESPERA" not in _codigos(rep)
    assert "BLOCO_CONFIANCA_NA_VESPERA" not in _codigos(rep)


def test_o_teto_esta_nomeado():
    assert 0.5 <= CONFIANCA_MAXIMA_NA_VESPERA < 1.0
    assert EARNINGS_PROXIMO_DIAS == 2


# ═══ 27/08/2026 — o stop atribuído ao plano tem que existir no plano ════════
#
# Veredito das 23:22: "INTC (stop-loss $100 acionado)" e "ARM (stop-loss $275
# acionado)" — os itens do plano dizem só "Vender imediatamente — stop-loss
# acionado", SEM número. O detalhe perverso: a ação estava CERTA (VENDER, do
# plano) e os números eram plausíveis — $275 para um papel a $251 parece um
# stop de verdade. Número inventado que não muda a ação é o mais difícil de
# notar, e é o que o leitor cita amanhã como se fosse fato.

_ITEM_SEM_NUMERO = [{"acao": "Vender imediatamente — stop-loss acionado",
                     "data_alvo": None, "fase": None, "nivel": None}]
_ITEM_ADI = [{"acao": "Vender 50% em $390 (resistência Bollinger upper) com "
                      "stop em $370",
              "data_alvo": None, "fase": None, "nivel": 390.0}]


def _snap_stop(plano, tickers=("INTC", "ARM", "ADI")):
    return _snap(quotes={tk: {} for tk in tickers}, plano_de_saida=plano)


def test_stop_inventado_para_item_sem_numero_e_erro():
    """As duas frases do incidente."""
    for texto, tk in [
        ("INTC (Pendente desde 20/ago, stop-loss $100 acionado): aguarda "
         "execução imediata.", "INTC"),
        ("ARM (Pendente desde 20/ago, stop-loss $275 acionado): aguarda "
         "execução imediata.", "ARM"),
    ]:
        rep = lint_veredito(texto, _snap_stop({tk: _ITEM_SEM_NUMERO}))
        assert "STOP_SEM_FONTE" in _codigos(rep), texto


def test_a_mensagem_diz_que_o_item_nao_traz_numero():
    rep = lint_veredito("INTC: stop-loss $100 acionado.",
                        _snap_stop({"INTC": _ITEM_SEM_NUMERO}))
    msg = next(i.message for i in rep.issues if i.code == "STOP_SEM_FONTE")
    assert "não trazem número" in msg


def test_stop_que_esta_no_item_passa():
    """O $370 do ADI é o SEGUNDO número do texto do item — todos contam,
    não só o primeiro (que é o `nivel`)."""
    rep = lint_veredito(
        "ADI: perto do stop $370; plano manda vender 50% em $390.",
        _snap_stop({"ADI": _ITEM_ADI}))
    assert "STOP_SEM_FONTE" not in _codigos(rep)


def test_stop_errado_com_itens_numerados_lista_os_certos():
    rep = lint_veredito("ADI: stop $360 acionado.",
                        _snap_stop({"ADI": _ITEM_ADI}))
    msg = next(i.message for i in rep.issues if i.code == "STOP_SEM_FONTE")
    assert "$370" in msg and "$390" in msg


def test_stop_sem_numero_no_texto_nao_e_conferido():
    """"stop-loss acionado" sem valor é exatamente o que o item diz."""
    rep = lint_veredito(
        "INTC: vender imediatamente conforme stop-loss acionado do plano.",
        _snap_stop({"INTC": _ITEM_SEM_NUMERO}))
    assert "STOP_SEM_FONTE" not in _codigos(rep)


def test_sem_plano_no_snapshot_ha_silencio():
    """Sem contra o que conferir, silêncio honesto — o 'stop emocional em
    $25' do WOLF (que não tem item) fica para o leitor julgar."""
    rep = lint_veredito("ARM com stop $275 acionado.", _snap_stop({}))
    assert "STOP_SEM_FONTE" not in _codigos(rep)


def test_leitura_falhada_do_plano_tambem_cala():
    rep = lint_veredito("INTC stop $100.",
                        _snap_stop({"_leitura_falhou": True}))
    assert "STOP_SEM_FONTE" not in _codigos(rep)


def test_alvo_de_analista_nao_e_stop():
    """"alvo $305,79 (consenso)" tem número fora do plano — mas não é stop,
    e o escopo é deliberadamente só stop para não acusar o consenso."""
    rep = lint_veredito(
        "NVDA: consenso Strong Buy, alvo $305.79 (upside 45.9%).",
        _snap(quotes={"NVDA": {}},
              plano_de_saida={"NVDA": _ITEM_SEM_NUMERO}))
    assert "STOP_SEM_FONTE" not in _codigos(rep)
