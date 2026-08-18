"""
NaN não pode sair no JSON — e não pode derrubar a resposta inteira.

Produção 18/08/2026, /api/technicals devolvendo 500 cinco vezes seguidas:

    {"items": [{"ticker": "NVDA", "price": NaN, ..., "rsi": 63.04, ...}]}

`json.dumps` do Python emite `NaN`/`Infinity` por extensão própria. Nenhum é
JSON válido, e o `JSON.parse` do Node os rejeita — então UM campo não-finito
tornou ilegível uma resposta em que todo o resto estava certo (o RSI 63,04, o
MACD, o VWAP). O painel Técnica parou por causa de um campo.

Duas camadas de conserto, e este arquivo cobre as duas:

  CAUSA   a última linha do histórico vinha com Close vazio (barra do dia
          corrente fora do pregão), então `close.iloc[-1]` era NaN e
          contaminava price, changePct, pctAboveSma* e priceVsVwapPct.
  CLASSE  tapar fonte por fonte é enxugar gelo — a próxima NaN aparece num
          campo que ninguém previu, com o mesmo estrago total. A fronteira da
          serialização é o único ponto onde a garantia vale para o payload
          inteiro, inclusive para campos que ainda não existem.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_json_seguro.py -v
"""
import json
import math
import pathlib

import pytest

from agent import json_seguro

_AGENT = pathlib.Path(__file__).resolve().parent.parent / "agent"


# ── o saneador ──────────────────────────────────────────────────────────────

def test_nan_vira_null():
    assert json_seguro.limpar_nao_finitos(float("nan")) is None


def test_infinitos_viram_null():
    """Divisão por zero em pandas dá inf, não NaN — e `Infinity` também não é
    JSON válido. Cobrir só NaN deixaria metade do problema de pé."""
    assert json_seguro.limpar_nao_finitos(float("inf")) is None
    assert json_seguro.limpar_nao_finitos(float("-inf")) is None


def test_numero_bom_passa_intacto():
    assert json_seguro.limpar_nao_finitos(226.27) == 226.27
    assert json_seguro.limpar_nao_finitos(0.0) == 0.0
    assert json_seguro.limpar_nao_finitos(-3.5) == -3.5


def test_zero_nao_e_confundido_com_ausencia():
    """`not 0.0` é True em Python — uma checagem por falsidade transformaria
    um zero legítimo (variação de 0,00% num dia parado) em null."""
    saida = json.loads(json_seguro.dumps({"changePct": 0.0}))
    assert saida["changePct"] == 0.0
    assert saida["changePct"] is not None


def test_limpa_dentro_de_dict_e_lista_aninhados():
    sujo = {"items": [{"price": float("nan"), "hist": [1.0, float("inf"), 2.0]}]}
    limpo = json_seguro.limpar_nao_finitos(sujo)
    assert limpo == {"items": [{"price": None, "hist": [1.0, None, 2.0]}]}


def test_tipos_que_nao_sao_numero_ficam_como_estao():
    dado = {"ticker": "NVDA", "ok": True, "n": None, "i": 42}
    assert json_seguro.limpar_nao_finitos(dado) == dado


def test_booleano_nao_vira_null():
    """bool é subclasse de int, não de float — mas a ordem das checagens
    importa se alguém trocar `isinstance(obj, float)` por algo mais largo."""
    saida = json.loads(json_seguro.dumps({"a": True, "b": False}))
    assert saida == {"a": True, "b": False}


# ── o resultado é JSON de verdade ───────────────────────────────────────────

def test_a_saida_e_parseavel_pelo_consumidor():
    """O teste que importa: o payload EXATO da produção volta parseável."""
    producao = {"items": [{
        "ticker": "NVDA", "price": float("nan"), "changePct": float("nan"),
        "rsi": 63.04, "macdHistogram": 1.6397, "sma20": None,
        "vwap": 226.27, "priceVsVwapPct": float("nan"),
    }]}
    item = json.loads(json_seguro.dumps(producao))["items"][0]

    assert item["price"] is None            # sem número, mas sem quebrar
    assert item["rsi"] == 63.04             # e o que era bom continua lá
    assert item["vwap"] == 226.27


def test_json_dumps_cru_produziria_json_invalido():
    """Fixa o motivo do helper existir. Se um dia o Python parar de emitir o
    token NaN, este teste falha e o helper pode ser reavaliado."""
    cru = json.dumps({"price": float("nan")})
    assert "NaN" in cru, "o Python parou de emitir o token NaN -- reavaliar o helper"

    # O json.loads do Python ACEITA o token por padrão; o JSON.parse do Node
    # não. parse_constant simula o consumidor estrito, que é quem consome de
    # verdade -- sem ele, este teste passaria mesmo com o bug de pé.
    def estrito(_token):
        raise ValueError("consumidor estrito rejeita")

    with pytest.raises(ValueError):
        json.loads(cru, parse_constant=estrito)


def test_nao_usa_allow_nan_false():
    """allow_nan=False faria o dumps LEVANTAR em vez de emitir NaN -- trocaria
    resposta ilegível por resposta NENHUMA. Limpar antes entrega os campos bons
    e um null onde não havia número."""
    saida = json_seguro.dumps({"a": float("nan"), "b": 1.0})   # não levanta
    assert json.loads(saida) == {"a": None, "b": 1.0}


# ── a causa, no get_technicals ──────────────────────────────────────────────
#
# O módulo sequestra o fd 1 no import (para proteger o stdout), então não dá
# para importá-lo aqui -- a verificação é sobre o texto do arquivo, mesmo
# padrão de test_technicals_rsi_rvol.py.

def _fonte(nome: str) -> str:
    return (_AGENT / nome).read_text(encoding="utf-8")


def test_get_technicals_descarta_linha_sem_close():
    """A barra do dia corrente vem com Close vazio fora do pregão e entra como
    ÚLTIMA linha. `close.iloc[-1]` virava NaN e contaminava metade do payload.
    O guarda de `len(hist) < 30` não pega: as linhas existem, só a última está
    vazia. Mesmo tratamento do get_chart.py:42."""
    fonte = _fonte("get_technicals.py")
    assert 'hist[hist["Close"].notna()]' in fonte


def test_get_technicals_serializa_pelo_helper():
    """Corrigir só a causa deixaria a próxima NaN (de outra origem) derrubando
    a resposta inteira de novo."""
    fonte = _fonte("get_technicals.py")
    assert "json_seguro.dumps" in fonte
    # e não pode ter sobrado um json.dumps no caminho de saída
    assert "json.dumps({\"items\"" not in fonte


def test_a_rota_registra_o_stdout_quando_nao_e_json():
    """Sem isso o erro era só "Parse error", e achar o NaN exigiu rodar o
    script à mão dentro do container. Mesmo remédio já aplicado em
    analysis.ts."""
    rota = (_AGENT.parent / "routes" / "technicals.ts").read_text(encoding="utf-8")
    assert "stdoutHead" in rota
    assert "stdoutTail" in rota
