"""
O bloco estruturado do Veredito (etapa 4 do motor auditável, 20/08/2026):
o LLM declara a decisão por ticker em JSON e o texto vira a explicação.

O que muda de verdade: o regex de intenção deixa de ser a FONTE (prosa
financeira é semanticamente traiçoeira -- "apesar da deterioração, a
assimetria favorece uma entrada...") e vira contraprova. As regras
determinísticas passam a rodar sobre estrutura: razão contradita pelo dado,
compra às vésperas de earnings sem veto declarado, concentração por
correlação sobre a declaração de compra, e a coerência JSON x texto.

Cada teste aqui é um jeito de o modelo trapacear que o validador tem que
pegar -- ou um caso legítimo que ele NÃO pode acusar (a isenção por risco
declarado, o vocabulário aberto em WARN).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_veredito_estruturado.py -v
"""
import json
import pathlib

from agent.veredito_validator import (
    checar_bloco_vs_texto,
    extrair_bloco_estruturado,
    validar_bloco_estruturado,
    validar_veredito_completo,
)


def _snapshot(**sobrescreve):
    base = {
        "as_of": "2026-08-19",
        "quotes": {
            "NVDA": {"price": 220.0, "previous_close": 219.0, "as_of": "2026-08-19"},
            "MU": {"price": 900.0, "previous_close": 890.0, "as_of": "2026-08-19"},
            "SNDK": {"price": 1500.0, "previous_close": 1490.0, "as_of": "2026-08-19"},
        },
        "technicals": {
            "NVDA": {"rsi": 50.0, "rsi_date": "2026-08-19"},
            "MU": {"rsi": 72.0, "rsi_date": "2026-08-19"},
            "SNDK": {"rsi": 68.0, "rsi_date": "2026-08-19"},
        },
        "earnings": {"NVDA": "2026-08-20"},
    }
    base.update(sobrescreve)
    return base


def _bloco(*itens):
    return {"tickers": list(itens)}


def _item(tk, action="MANTER", confidence=0.6, codes=("TENDENCIA_ALTA",)):
    return {"ticker": tk, "action": action, "confidence": confidence,
            "reason_codes": list(codes)}


def _texto_com(bloco, prosa="Veredito neutro. NVDA segue no radar, MU e SNDK idem."):
    return prosa + "\n\n```json\n" + json.dumps(bloco) + "\n```\n"


def _codigos(rep):
    return {i.code for i in rep.issues}


def _erros(rep):
    return {i.code for i in rep.issues if i.severity == "ERROR"}


# ── extração ─────────────────────────────────────────────────────────────────

def test_sem_bloco_devolve_none_sem_erro():
    assert extrair_bloco_estruturado("só prosa, nenhum código") == (None, None)

def test_bloco_valido_e_extraido():
    bloco, erro = extrair_bloco_estruturado(_texto_com(_bloco(_item("NVDA"))))
    assert erro is None
    assert bloco["tickers"][0]["ticker"] == "NVDA"

def test_json_quebrado_vira_erro_nomeado_nao_silencio():
    texto = 'x\n```json\n{"tickers": [oops]}\n```'
    bloco, erro = extrair_bloco_estruturado(texto)
    assert bloco is None and "invalido" in erro

def test_ultimo_bloco_com_tickers_vence():
    """O texto pode citar outros JSONs legitimamente; a decisão é a última."""
    texto = ('```json\n{"exemplo": 1}\n```\n'
             + _texto_com(_bloco(_item("NVDA", action="AGUARDAR"))))
    bloco, _ = extrair_bloco_estruturado(texto)
    assert bloco["tickers"][0]["action"] == "AGUARDAR"


# ── schema e completude ──────────────────────────────────────────────────────

def test_bloco_completo_e_coerente_passa_limpo():
    snap = _snapshot()
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", action="MANTER", codes=["RSI_SOBRECOMPRADO"]),
                   _item("SNDK", action="MANTER", codes=["TENDENCIA_ALTA"]))
    rep = validar_veredito_completo(_texto_com(bloco), snap)
    assert _erros(rep) == set()

def test_bloco_ausente_e_erro():
    rep = validar_veredito_completo("prosa sem bloco nenhum", _snapshot())
    assert "BLOCO_AUSENTE" in _erros(rep)

def test_ticker_da_carteira_sem_entrada_e_erro():
    """Omitir o ticker difícil é como o modelo 'resolve' a parte difícil."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU"))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_TICKER_FALTANDO" in _erros(rep)

def test_schema_ruim_e_apontado_campo_a_campo():
    bloco = _bloco(
        _item("NVDA", action="HOLD"),                # vocabulário errado
        _item("MU", confidence=1.7),                 # fora de [0,1]
        {"ticker": "SNDK", "action": "MANTER", "confidence": 0.5,
         "reason_codes": []},                        # sem razão declarada
    )
    erros = _erros(validar_bloco_estruturado(bloco, _snapshot()))
    assert {"BLOCO_ACTION_INVALIDA", "BLOCO_CONFIDENCE_INVALIDA",
            "BLOCO_SEM_REASON_CODES"} <= erros

def test_ticker_fora_da_carteira_e_duplicado_sao_erros():
    bloco = _bloco(_item("XYZ"), _item("MU"), _item("MU"))
    erros = _erros(validar_bloco_estruturado(bloco, _snapshot()))
    assert {"BLOCO_TICKER_DESCONHECIDO", "BLOCO_TICKER_DUPLICADO"} <= erros

def test_runup_esticado_e_vocabulario_conhecido():
    """Promovido do primeiro veredito real: o modelo usou VALUATION_ESTICADO
    para run-up de preço -- o rótulo certo agora existe e não gera WARN."""
    bloco = _bloco(_item("MU", codes=["RUNUP_ESTICADO"]),
                   _item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_REASON_DESCONHECIDO" not in _codigos(rep)


def test_reason_code_novo_e_warn_nao_error():
    """O vocabulário evolui; código novo razoável não pode custar um retry."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO", "GAP_ABERTO"]),
                   _item("MU"), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_REASON_DESCONHECIDO" in _codigos(rep)
    assert "BLOCO_REASON_DESCONHECIDO" not in _erros(rep)


# ── coerência com o dado ─────────────────────────────────────────────────────

def test_razao_contradita_pelo_dado_e_erro():
    """RSI_SOBREVENDIDO com RSI 50: o leitor confia no rótulo -- razão
    contradita pelo dado é pior que razão ausente."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["RSI_SOBREVENDIDO"]),
                   _item("MU"), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_REASON_CONTRADITO" in _erros(rep)

def test_comprar_na_vespera_de_earnings_sem_veto_declarado_e_erro():
    bloco = _bloco(_item("NVDA", action="COMPRAR", codes=["TENDENCIA_ALTA"]),
                   _item("MU"), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())  # NVDA reporta amanhã
    assert "BLOCO_COMPRA_SEM_VETO_DECLARADO" in _erros(rep)

def test_comprar_declarando_earnings_proximo_e_decisao_consciente():
    bloco = _bloco(_item("NVDA", action="COMPRAR",
                         codes=["TENDENCIA_ALTA", "EARNINGS_PROXIMO"]),
                   _item("MU"), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_COMPRA_SEM_VETO_DECLARADO" not in _erros(rep)

def test_concentracao_agora_roda_sobre_a_declaracao():
    """MU+SNDK (corr 0.82) COMPRAR nos dois sem RISCO_CORRELACAO: o mesmo
    trade duas vezes, pego do JSON -- sem depender de regex acertar a prosa."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", action="COMPRAR", codes=["TENDENCIA_ALTA"]),
                   _item("SNDK", action="COMPRAR", codes=["TENDENCIA_ALTA"]))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_CONCENTRACAO" in _erros(rep)

def test_risco_declarado_isenta_a_concentracao():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", action="COMPRAR", codes=["TENDENCIA_ALTA", "RISCO_CORRELACAO"]),
                   _item("SNDK", action="COMPRAR", codes=["TENDENCIA_ALTA", "RISCO_CORRELACAO"]))
    rep = validar_bloco_estruturado(bloco, _snapshot())
    assert "BLOCO_CONCENTRACAO" not in _erros(rep)


# ── coerência JSON x texto ───────────────────────────────────────────────────

def test_texto_compra_e_bloco_vende_e_divergencia():
    bloco = _bloco(_item("NVDA", action="VENDER", codes=["TENDENCIA_BAIXA"]))
    texto = "A leitura sugere comprar NVDA na abertura de amanhã."
    rep = checar_bloco_vs_texto(bloco, texto, ["NVDA"])
    assert "JSON_TEXTO_DIVERGEM" in _erros(rep)

def test_compra_no_bloco_sem_o_ticker_no_texto_e_decisao_sem_explicacao():
    bloco = _bloco(_item("MU", action="COMPRAR", codes=["TENDENCIA_ALTA"]))
    rep = checar_bloco_vs_texto(bloco, "Texto que fala só de outras coisas.", ["MU"])
    assert "COMPRA_SEM_EXPLICACAO" in _erros(rep)

def test_negacao_no_texto_nao_vira_falsa_divergencia():
    """'não é hora de comprar NVDA' + bloco AGUARDAR: mesma história."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]))
    texto = "Não é hora de comprar NVDA; o balanço sai amanhã."
    rep = checar_bloco_vs_texto(bloco, texto, ["NVDA"])
    assert _erros(rep) == set()


# ── o prompt pede o que o validador cobra ────────────────────────────────────

def test_prompt_do_veredito_especifica_o_bloco():
    """Amarra por leitura de fonte: cobrar no validador um formato que o
    prompt não pede seria armar o retry para sempre disparar."""
    fonte = (pathlib.Path(__file__).resolve().parent.parent / "agent" / "llm_runtime.py"
             ).read_text(encoding="utf-8")
    trecho = fonte.split("def build_veredito_prompt", 1)[1].split("\ndef ", 1)[0]
    assert "```json" in trecho
    assert '"reason_codes"' in trecho
    for acao in ("COMPRAR", "AGUARDAR", "VENDER"):
        assert acao in trecho


# ── capex: a tese de data center como razão CHECÁVEL (25/08/2026) ────────────

def _snap_capex(direcao="acelerando"):
    return _snapshot(capex_hyperscalers={
        "disponivel": True, "trimestre": "2026Q2", "totalUsdBi": 152.4,
        "variacaoQoQPct": 16.0 if direcao == "acelerando" else -12.0,
        "variacaoYoYPct": 80.0, "direcao": direcao,
        "disponivelEm": "2026-08-14", "empresas": ["AMZN", "GOOGL", "META", "MSFT", "ORCL"],
    })


def test_capex_acelerando_com_o_dado_acelerando_passa():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAPEX_ACELERANDO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_capex("acelerando"))
    assert "BLOCO_CAPEX_CONTRADITO" not in _erros(rep)
    assert "BLOCO_REASON_DESCONHECIDO" not in _codigos(rep)


def test_capex_acelerando_com_o_dado_desacelerando_e_erro():
    """Sem esta checagem, CAPEX_ACELERANDO viraria o rótulo bonito que
    justifica qualquer compra -- a tese usada como enfeite."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAPEX_ACELERANDO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_capex("desacelerando"))
    assert "BLOCO_CAPEX_CONTRADITO" in _erros(rep)


def test_citar_capex_sem_o_dado_no_snapshot_e_erro():
    """Razão sem fato por trás: o overlay pode estar velho ou ausente, e o
    modelo não pode preencher a lacuna de memória."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAPEX_ACELERANDO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())  # sem capex
    assert "BLOCO_CAPEX_SEM_DADO" in _erros(rep)


def test_prompt_ensina_o_capex_como_contexto_nao_gatilho():
    fonte = (pathlib.Path(__file__).resolve().parent.parent / "agent" / "llm_runtime.py"
             ).read_text(encoding="utf-8")
    trecho = fonte.split("def build_veredito_prompt", 1)[1].split("\ndef ", 1)[0]
    assert "capex_hyperscalers" in trecho
    assert "CAPEX_ACELERANDO" in trecho
    # Sem depender de quebra de linha: o prompt tem que ensinar as duas
    # coisas -- que é contexto de tese e que NÃO dispara operação.
    assert "CONTEXTO de tese" in trecho
    assert "gatilho de operação" in trecho


# ── fôlego de caixa: solidez financeira como razão CHECÁVEL (25/08/2026) ─────
#
# "A empresa tem caixa" é das afirmações mais fáceis de fazer sem olhar o
# balanço, e por isso das que mais precisam de conferência. Mesma regra do
# capex: o rótulo só vale na direção que o número do snapshot sustenta.

def _snap_folego(**campos):
    base = {"disponivel": True, "trimestre": "2026Q2", "disponivelEm": "2026-08-14",
            "moeda": "USD", "piorando": False,
            "caixa": 700e6, "dividaLiquida": 2.3e9, "liquidezCorrente": 2.0,
            "fcfTrimestral": -170e6, "queimaMedia": 170e6,
            "trimestresDeQueima": 4, "folegoTrimestres": 4.1, "geraCaixa": False,
            "quebraDeSerie": False, "trimestresNaSerie": 5}
    base.update(campos)
    return _snapshot(folego_de_caixa={"MU": base})


def test_caixa_curto_com_folego_curto_passa():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", action="AGUARDAR", codes=["CAIXA_CURTO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_folego(folegoTrimestres=2.1))
    assert "BLOCO_CAIXA_CONTRADITO" not in _erros(rep)
    assert "BLOCO_REASON_DESCONHECIDO" not in _codigos(rep)


def test_caixa_curto_em_empresa_que_gera_caixa_e_erro():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", action="AGUARDAR", codes=["CAIXA_CURTO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(
        bloco, _snap_folego(geraCaixa=True, folegoTrimestres=None))
    assert "BLOCO_CAIXA_CONTRADITO" in _erros(rep)


def test_caixa_confortavel_com_folego_curto_e_erro():
    """A direção que mais interessa vetar: dizer que está tudo bem no caixa de
    quem tem menos de um ano de fôlego."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAIXA_CONFORTAVEL"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_folego(folegoTrimestres=1.5))
    assert "BLOCO_CAIXA_CONTRADITO" in _erros(rep)


def test_caixa_confortavel_em_quem_gera_caixa_passa():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAIXA_CONFORTAVEL"]), _item("SNDK"))
    rep = validar_bloco_estruturado(
        bloco, _snap_folego(geraCaixa=True, folegoTrimestres=None))
    assert "BLOCO_CAIXA_CONTRADITO" not in _erros(rep)


def test_citar_caixa_sem_o_balanco_no_snapshot_e_erro():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAIXA_CURTO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snapshot())  # sem fôlego
    assert "BLOCO_CAIXA_SEM_DADO" in _erros(rep)


def test_balanco_reestruturado_sem_quebra_marcada_e_erro():
    """O rótulo existe para o modelo declarar que a comparação a/a não vale --
    não para enfeitar um balanço que não teve evento nenhum."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["BALANCO_REESTRUTURADO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_folego(quebraDeSerie=False))
    assert "BLOCO_CAIXA_CONTRADITO" in _erros(rep)


def test_balanco_reestruturado_com_quebra_marcada_passa():
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["BALANCO_REESTRUTURADO"]), _item("SNDK"))
    rep = validar_bloco_estruturado(bloco, _snap_folego(quebraDeSerie=True))
    assert "BLOCO_CAIXA_CONTRADITO" not in _erros(rep)


def test_folego_de_outro_ticker_nao_vale_para_este():
    """O snapshot é por ticker: usar o balanço da NVDA para justificar a MU
    seria pior que não conferir nada."""
    bloco = _bloco(_item("NVDA", action="AGUARDAR", codes=["EARNINGS_PROXIMO"]),
                   _item("MU", codes=["CAIXA_CONFORTAVEL"]), _item("SNDK"))
    snap = _snapshot(folego_de_caixa={"NVDA": {"disponivel": True,
                                               "geraCaixa": True,
                                               "folegoTrimestres": None}})
    rep = validar_bloco_estruturado(bloco, snap)
    assert "BLOCO_CAIXA_SEM_DADO" in _erros(rep)


def test_prompt_ensina_o_folego_como_contexto_nao_gatilho():
    fonte = (pathlib.Path(__file__).resolve().parent.parent / "agent" / "llm_runtime.py"
             ).read_text(encoding="utf-8")
    trecho = fonte.split("def build_veredito_prompt", 1)[1].split("\ndef ", 1)[0]
    assert "folego_de_caixa" in trecho
    for codigo in ("CAIXA_CURTO", "CAIXA_CONFORTAVEL", "BALANCO_REESTRUTURADO"):
        assert codigo in trecho
    assert "CONTEXTO de risco de tese" in trecho
    assert "não gatilho" in trecho
