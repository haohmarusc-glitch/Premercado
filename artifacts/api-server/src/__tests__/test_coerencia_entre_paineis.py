"""
Três correções nascidas do mesmo export da Análise Rápida (NBIS, 17/08/2026
10:37 BRT) -- o primeiro tirado com o MERCADO ABERTO. Todas são da família do
§1 do playbook: o mesmo fato derivado em vários lugares, sem fonte de verdade.

1. QUATRO preços num retrato só: $270,28 (Técnica), $269,87 (Níveis), $269,98
   (reação a earnings) e $277,68 (Tendência). Os três primeiros diferem por
   timing de fetch; o quarto era o fechamento da sexta anterior. A análise com
   IA abriu citando os $277,68 -- "a poucos dólares da máxima" -- e três
   parágrafos abaixo disse que o papel caía 2,66% a $270,28.

2. O cache de 30min do get_trend não morria na abertura do pregão, então
   servia dado pré-mercado com o pregão em curso. É a origem do $277,68.

3. RVOL 5,81 "alto" aos sete minutos de pregão: o cálculo assume volume
   uniforme ao longo do dia, mas a distribuição real é em U. A IA leu o
   artefato como "realização de lucro".

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_coerencia_entre_paineis.py -v
"""
import datetime
import json
import pathlib
import sys

import pytest

_AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from agent import tools  # noqa: E402
from agent.analise_rapida_ia import (  # noqa: E402
    _compactar, _defasagem_entre_paineis, _preco_canonico)


# ── 1. Preço canônico ───────────────────────────────────────────────────────

def _payload(**kw):
    """Retrato com os quatro painéis; cada kwarg sobrescreve um preço."""
    return {
        "ticker": "NBIS",
        "trend": {"price": kw.get("trend")} if "trend" in kw else None,
        "technicals": {"price": kw.get("tecnica")} if "tecnica" in kw else None,
        "snapshot": {"price": kw.get("niveis")} if "niveis" in kw else None,
        "reaction": {"summary": {"current_price": kw.get("reacao")}} if "reacao" in kw else None,
    }


def test_snapshot_e_a_fonte_canonica_por_ser_a_unica_ao_vivo():
    out = _preco_canonico(_payload(niveis=269.87, tecnica=270.28, reacao=269.98, trend=277.68))
    assert out["valor"] == pytest.approx(269.87)
    assert out["fonte"] == "niveis"


def test_cai_para_a_tecnica_quando_nao_ha_snapshot():
    out = _preco_canonico(_payload(tecnica=270.28, trend=277.68))
    assert out["fonte"] == "tecnica"
    assert out["valor"] == pytest.approx(270.28)


def test_tendencia_e_a_ultima_opcao_por_poder_vir_de_cache():
    out = _preco_canonico(_payload(trend=277.68))
    assert out["fonte"] == "tendencia"


def test_divergencia_grande_e_exposta_com_o_preco_de_cada_painel():
    """O caso real: a Tendência 2,9% acima das outras três."""
    out = _preco_canonico(_payload(niveis=269.87, tecnica=270.28, reacao=269.98, trend=277.68))
    assert out["divergenciaPct"] == pytest.approx(2.89, abs=0.02)
    assert out["porPainel"] == {
        "niveis": 269.87, "tecnica": 270.28, "reacaoEarnings": 269.98, "tendencia": 277.68,
    }


def test_divergencia_de_timing_de_fetch_nao_vira_alarme():
    """$270,28 / $269,87 / $269,98 sozinhos são 0,15% -- ruído de quatro
    requisições em instantes diferentes, não defasagem. Marcar isso como
    divergência treinaria o leitor a ignorar o aviso."""
    out = _preco_canonico(_payload(niveis=269.87, tecnica=270.28, reacao=269.98))
    assert "divergenciaPct" not in out
    assert "porPainel" not in out


def test_sem_nenhum_preco_devolve_none():
    assert _preco_canonico(_payload()) is None
    assert _preco_canonico(_payload(niveis=None, tecnica=0)) is None


def test_preco_canonico_entra_no_payload_do_prompt():
    texto, _omitidos = _compactar(_payload(niveis=269.87, trend=277.68))
    payload = json.loads(texto)
    assert payload["precoAtual"]["valor"] == pytest.approx(269.87)
    assert payload["precoAtual"]["fonte"] == "niveis"
    # Sem isto o modelo não tem como saber que a Tendência está defasada.
    assert payload["precoAtual"]["divergenciaPct"] > 1.0


# ── 2. Cache do get_trend x abertura do pregão ──────────────────────────────

def _ts(dia: str, hora: str) -> float:
    return datetime.datetime.fromisoformat(f"{dia}T{hora}").replace(
        tzinfo=datetime.timezone.utc
    ).timestamp()


def test_cache_gravado_antes_da_abertura_nao_vale_com_pregao_em_curso():
    """O caso NBIS: entrada gravada 12:50 UTC (pré-mercado), consulta 13:37
    UTC (sete minutos de pregão). Só 47min de idade -- dentro do TTL de 30min
    não estaria, mas a regra tem que valer independentemente dele."""
    import get_trend

    gravado = _ts("2026-08-17", "12:50:00")
    agora = _ts("2026-08-17", "13:37:00")
    assert get_trend._cruzou_abertura(gravado, agora) is True


def test_cache_gravado_depois_da_abertura_continua_valendo():
    import get_trend

    assert get_trend._cruzou_abertura(
        _ts("2026-08-17", "14:00:00"), _ts("2026-08-17", "14:20:00")
    ) is False


def test_cache_inteiramente_pre_abertura_continua_valendo():
    """Duas consultas no pré-mercado: nada mudou entre elas, o cache serve."""
    import get_trend

    assert get_trend._cruzou_abertura(
        _ts("2026-08-17", "11:00:00"), _ts("2026-08-17", "12:00:00")
    ) is False


def test_cache_do_dia_anterior_morre_na_abertura_de_hoje():
    import get_trend

    assert get_trend._cruzou_abertura(
        _ts("2026-08-14", "20:00:00"), _ts("2026-08-17", "14:00:00")
    ) is True


# ── 3. RVOL na abertura ─────────────────────────────────────────────────────

def test_rvol_nos_primeiros_30min_nao_vira_alto():
    """7min de pregão = ~2 barras de 5min. Era aqui que saía "alto" com 5,81."""
    assert tools._rvol_signal(5.81, fraction_elapsed=2 / 78) == "indefinido_abertura"
    assert tools._rvol_signal(0.2, fraction_elapsed=2 / 78) == "indefinido_abertura"


def test_rvol_volta_a_classificar_depois_da_primeira_meia_hora():
    depois = 10 / 78
    assert tools._rvol_signal(5.81, depois) == "alto"
    assert tools._rvol_signal(1.0, depois) == "normal"
    assert tools._rvol_signal(0.5, depois) == "baixo"


def test_limiares_originais_preservados_no_pregao_cheio():
    cheio = 1.0
    assert tools._rvol_signal(1.5, cheio) == "alto"
    assert tools._rvol_signal(1.49, cheio) == "normal"
    assert tools._rvol_signal(0.7, cheio) == "normal"
    assert tools._rvol_signal(0.69, cheio) == "baixo"


def test_as_duas_copias_de_rvol_signal_concordam():
    """get_technicals.py não pode ser importado (sequestra o stdout no
    import, ver a docstring do módulo), então a cópia é comparada no texto --
    mesma convenção de test_get_technicals_fallback.py."""
    fonte = (_AGENT_DIR / "get_technicals.py").read_text(encoding="utf-8")
    assert "_RVOL_FRACAO_MINIMA = 6 / 78" in fonte
    assert 'return "indefinido_abertura"' in fonte
    assert 'return "alto" if rvol >= 1.5 else "baixo" if rvol < 0.7 else "normal"' in fonte
    # E que ela é de fato usada, não só definida.
    assert "rvol_signal = _rvol_signal(rvol, fraction_elapsed)" in fonte


# ═══ 29/08/2026 — MRVL: painéis do mesmo retrato, sessões diferentes ═══════
#
# A Técnica trazia US$ 241,45 com "-1,49% no dia" -- número idêntico à linha
# `2026-08-27` da tabela de earnings, ou seja, a barra do dia do ANÚNCIO. Os
# Níveis traziam US$ 216,62. As bandas R1/R2/S1/S2, que são projeções do
# preço, saíram ancoradas em 241,45 (241,45 × 1,1378 = 274,72 = o R1 exibido)
# sob um cabeçalho que exibia 216,62.
#
# O `divergenciaPct` PEGOU: 11,46%, e a prosa abriu o segundo parágrafo
# nomeando os dois preços. Mas ele é um PROXY -- só dispara quando o preço se
# moveu. Se o balanço tivesse sido morno, o mesmo defeito passaria mudo.
#
# A causa não é cache do servidor (histórico 10 min, trend 30 min): é a tela
# mandando `{trend, technicals, snapshot, reaction}` do que tem em memória,
# cada painel de um hook próprio, nenhum dizendo de quando era.

def _retrato(tendencia=None, tecnica=None, niveis=None, reacao=None):
    return {
        "ticker": "MRVL", "benchmark": "SMH",
        "trend": {"price": 241.45, "dadosAte": tendencia} if tendencia else None,
        "technicals": {"price": 241.45, "dadosAte": tecnica} if tecnica else None,
        "snapshot": {"price": 216.62, "dadosAte": niveis, "aoVivo": True} if niveis else None,
        "reaction": {"summary": {"current_price": 241.45, "dados_ate": reacao}} if reacao else None,
    }


def test_paineis_de_sessoes_diferentes_viram_apontamento():
    """O retrato do MRVL: três painéis na barra de 27/08, os níveis ao vivo."""
    d = _defasagem_entre_paineis(
        _retrato("2026-08-27", "2026-08-27", "2026-08-29", "2026-08-27"))
    assert d is not None
    assert d["maisVelha"] == "2026-08-27"
    assert d["maisNova"] == "2026-08-29"
    assert d["diasDeAtraso"] == 2
    assert d["porPainel"]["tecnica"] == "2026-08-27"
    assert d["porPainel"]["niveis"] == "2026-08-29"


def test_o_aviso_diz_o_que_NAO_escrever():
    """Marcador que só constata a diferença vira decoração. Este nomeia as
    conclusões que ficam inválidas -- VWAP, distância de médias, balanço
    precificado -- porque são exatamente as que a tela do MRVL publicou."""
    d = _defasagem_entre_paineis(
        _retrato("2026-08-27", "2026-08-27", "2026-08-29", "2026-08-27"))
    aviso = d["_aviso"]
    assert "vwap" in aviso.lower()
    assert "precificado" in aviso
    assert "nomeando as duas datas" in aviso


def test_mesmo_retrato_nao_vira_apontamento():
    assert _defasagem_entre_paineis(
        _retrato("2026-08-29", "2026-08-29", "2026-08-29", "2026-08-29")) is None


def test_pega_mesmo_com_o_preco_parado():
    """A razão de existir, ao lado do `divergenciaPct`: aquele é um proxy e só
    dispara quando o preço se moveu. Aqui os quatro painéis marcam o MESMO
    preço -- e a defasagem continua sendo apontada."""
    d = _retrato("2026-08-27", "2026-08-27", "2026-08-29", "2026-08-27")
    for bloco in ("trend", "technicals", "snapshot"):
        d[bloco]["price"] = 216.62
    d["reaction"]["summary"]["current_price"] = 216.62
    assert _preco_canonico(d).get("divergenciaPct") is None, "preço parado"
    assert _defasagem_entre_paineis(d) is not None, "e mesmo assim aponta"


def test_um_painel_so_nao_tem_com_que_comparar():
    assert _defasagem_entre_paineis(_retrato(tecnica="2026-08-27")) is None


def test_painel_sem_dadosAte_nao_derruba_a_checagem():
    """Painel velho, de antes do campo existir, não pode virar exceção nem
    silenciar os que declaram."""
    d = _retrato("2026-08-27", None, "2026-08-29", None)
    assert _defasagem_entre_paineis(d)["diasDeAtraso"] == 2


def test_data_malformada_vira_silencio_e_nao_traceback():
    assert _defasagem_entre_paineis(
        _retrato("ontem", "2026-08-27", "2026-08-29", None)) is None


def test_a_defasagem_entra_no_payload_do_prompt():
    """Antes dos painéis, de propósito: se eles não são do mesmo retrato, isso
    muda o que TODOS os outros campos significam."""
    texto, _omitidos = _compactar(
        _retrato("2026-08-27", "2026-08-27", "2026-08-29", "2026-08-27"))
    payload = json.loads(texto)
    assert payload["defasagemEntrePaineis"]["diasDeAtraso"] == 2
    chaves = list(payload)
    assert chaves.index("defasagemEntrePaineis") < chaves.index("tendencia")
