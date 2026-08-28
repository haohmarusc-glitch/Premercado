"""Múltiplos TTM da SEC: uma armadilha por teste.

O que torna estas armadilhas caras é que TODAS produzem número plausível.
Nenhuma levanta exceção; cada uma entrega um múltiplo com cara de certo. Por
isso cada caso aqui checa o VALOR, não só que a função não quebrou -- e vários
deles afirmam também qual seria o número ERRADO, para o teste falhar se a
defesa for removida.

Fixture em vez de rede de propósito: a aritmética é o que erra, e um teste que
depende da SEC estar no ar não roda na CI. A conferência contra demonstração
real é a ETAPA 1 (modo sombra, `python3 -m agent.fundamentos_sec MRVL ...`),
que roda onde há rede -- este arquivo é a rede de segurança da lógica.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.fundamentos_sec import (  # noqa: E402
    SemDado,
    _acoes_em_circulacao,
    _crescimento_ttm,
    _instantaneo,
    _mais_recente,
    _trimestres_de,
    _ttm,
    emissor_estrangeiro,
    multiplos,
)


def _f(start, end, val, *, filed="2026-01-01", accn="a-1", form="10-Q"):
    return {"start": start, "end": end, "val": val,
            "filed": filed, "accn": accn, "form": form, "fp": "Q1"}


def _inst(end, val, *, filed="2026-01-01", accn="a-1", form="10-Q"):
    return {"end": end, "val": val, "filed": filed, "accn": accn, "form": form}


def _quatro_trimestres(vals, ano=2025):
    """Quatro trimestres ISOLADOS (como o emissor bem-comportado publica)."""
    janelas = [("01-01", "03-31"), ("04-01", "06-30"),
               ("07-01", "09-30"), ("10-01", "12-31")]
    return [_f(f"{ano}-{a}", f"{ano}-{b}", v) for (a, b), v in zip(janelas, vals)]


# ── armadilha 1: acumulado do ano somado como trimestre ─────────────────────


def test_acumulado_vira_trimestre_por_diferenca():
    """O caso que infla a receita: 10-Q publicando YTD, não o trimestre.

    Se os quatro fossem somados como estão, o TTM daria 100+300+600+1000=2000
    -- mais que o DOBRO da receita real do ano (1000), porque o Q1 entraria
    quatro vezes, o Q2 três, e assim por diante.
    """
    ytd = [
        _f("2025-01-01", "2025-03-31", 100),   # Q1
        _f("2025-01-01", "2025-06-30", 300),   # semestre
        _f("2025-01-01", "2025-09-30", 600),   # 9 meses
        _f("2025-01-01", "2025-12-31", 1000),  # ano
    ]
    tris = _trimestres_de(ytd)
    assert [t["val"] for t in tris] == [100, 200, 300, 400]

    total, prov = _ttm(ytd)
    assert total == 1000, "TTM tem que ser a receita do ano, não a soma dos YTD"
    assert total != 2000, "somar YTD contaria o Q1 quatro vezes"
    # Os derivados ficam marcados: o número não foi publicado assim.
    assert [t["derivado_por_diferenca"] for t in prov["trimestres"]] == [
        False, True, True, True]


def test_trimestre_publicado_ganha_do_derivado():
    """Quando o emissor publica os dois, vale o publicado -- menos aritmética
    nossa entre o arquivamento e o número na tela."""
    mistura = [
        _f("2025-01-01", "2025-03-31", 100),
        _f("2025-01-01", "2025-06-30", 300),   # YTD -> derivaria 200
        _f("2025-04-01", "2025-06-30", 210),   # publicado como trimestre
    ]
    tris = _trimestres_de(mistura)
    assert [t["val"] for t in tris] == [100, 210]


def test_ano_inteiro_nao_e_trimestre():
    """O 10-K sozinho não vira um 'trimestre' gigante na janela."""
    assert _trimestres_de([_f("2025-01-01", "2025-12-31", 1000)]) == []


# ── armadilha 2: reapresentação ─────────────────────────────────────────────


def test_reapresentacao_vence_pelo_filed():
    antigo = _f("2025-01-01", "2025-03-31", 100, filed="2025-04-30", accn="a-1")
    novo = _f("2025-01-01", "2025-03-31", 90, filed="2025-11-02", accn="a-9")
    assert _mais_recente([antigo, novo])["val"] == 90
    # E na ordem inversa da lista: não pode depender de quem veio primeiro.
    assert _mais_recente([novo, antigo])["val"] == 90


def test_empate_no_filed_desempata_pelo_accn():
    a = _f("2025-01-01", "2025-03-31", 100, filed="2025-04-30", accn="a-1")
    b = _f("2025-01-01", "2025-03-31", 95, filed="2025-04-30", accn="a-2")
    assert _mais_recente([a, b])["val"] == 95


def test_ttm_usa_o_numero_reapresentado():
    tris = _quatro_trimestres([100, 100, 100, 100])
    tris.append(_f("2025-01-01", "2025-03-31", 40,
                   filed="2026-02-01", accn="z-9"))  # Q1 republicado
    total, _ = _ttm(tris)
    assert total == 340, "o Q1 republicado (40) tem que substituir o original (100)"


# ── TTM exige quatro trimestres contíguos ───────────────────────────────────


def test_menos_de_quatro_trimestres_nao_e_ttm():
    with pytest.raises(SemDado, match="TTM precisa de 4"):
        _ttm(_quatro_trimestres([100, 100, 100])[:3])


def test_buraco_no_meio_reprova():
    """Nove meses somados com nome de doze seriam um TTM menor e plausível."""
    tris = [
        _f("2025-01-01", "2025-03-31", 100),
        # Q2 ausente
        _f("2025-07-01", "2025-09-30", 100),
        _f("2025-10-01", "2025-12-31", 100),
        _f("2026-01-01", "2026-03-31", 100),
    ]
    with pytest.raises(SemDado, match="não são contíguos"):
        _ttm(tris)


# ── armadilha 4: estoque não se soma ────────────────────────────────────────


def test_instantaneo_pega_o_balanco_mais_recente():
    fatos = [_inst("2025-06-30", 500), _inst("2025-12-31", 700)]
    val, prov = _instantaneo(fatos)
    assert val == 700 and prov["data"] == "2025-12-31"


def test_instantaneo_ate_uma_data_para_a_media_do_roe():
    fatos = [_inst("2024-12-31", 400), _inst("2025-12-31", 700)]
    import datetime as dt
    val, _ = _instantaneo(fatos, dt.date(2025, 1, 15))
    assert val == 400


def test_fato_com_periodo_nao_conta_como_instantaneo():
    with pytest.raises(SemDado, match="nenhum fato instantâneo"):
        _instantaneo([_f("2025-01-01", "2025-03-31", 100)])


# ── armadilha 7: foreign private issuer ─────────────────────────────────────


def test_ifrs_sem_us_gaap_nao_e_suportado():
    dados = {"facts": {"ifrs-full": {"Revenue": {"units": {"USD": []}}}}}
    motivo = emissor_estrangeiro(dados)
    assert motivo and "IFRS" in motivo
    assert multiplos(dados, 10.0)["suportado"] is False


def test_formulario_20f_nao_e_suportado():
    dados = {"facts": {
        "us-gaap": {"Revenues": {"units": {"USD": []}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _inst("2025-12-31", 1000, form="20-F")]}}},
    }}
    motivo = emissor_estrangeiro(dados)
    assert motivo and "20-F" in motivo


def test_emissor_domestico_passa():
    dados = {"facts": {
        "us-gaap": {"Revenues": {"units": {"USD": []}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _inst("2025-12-31", 1000, form="10-K")]}}},
    }}
    assert emissor_estrangeiro(dados) is None


# ── armadilha 5: ações em circulação, não média diluída ─────────────────────


def test_acoes_vem_da_capa_do_arquivamento():
    dados = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {
        "units": {"shares": [_inst("2025-12-31", 860_000_000)]}}}}}
    val, prov = _acoes_em_circulacao(dados)
    assert val == 860_000_000
    assert prov["tag"] == "dei:EntityCommonStockSharesOutstanding"


def test_sem_acoes_nao_ha_capitalizacao():
    with pytest.raises(SemDado, match="EntityCommonStockSharesOutstanding"):
        _acoes_em_circulacao({"facts": {"dei": {}}})


# ── as métricas, num emissor completo ───────────────────────────────────────


def _emissor(**extra):
    """Emissor doméstico com números redondos, para a conta ser conferível
    de cabeça."""
    facts = {
        "Revenues": {"units": {"USD": _quatro_trimestres([250, 250, 250, 250])}},
        "NetIncomeLoss": {"units": {"USD": _quatro_trimestres([25, 25, 25, 25])}},
        "OperatingIncomeLoss": {"units": {"USD": _quatro_trimestres([30, 30, 30, 30])}},
        "DepreciationDepletionAndAmortization": {
            "units": {"USD": _quatro_trimestres([5, 5, 5, 5])}},
        "NetCashProvidedByUsedInOperatingActivities": {
            "units": {"USD": _quatro_trimestres([40, 40, 40, 40])}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "units": {"USD": _quatro_trimestres([10, 10, 10, 10])}},
        "StockholdersEquity": {"units": {"USD": [
            _inst("2024-12-31", 400), _inst("2025-12-31", 600)]}},
        "CashAndCashEquivalentsAtCarryingValue": {
            "units": {"USD": [_inst("2025-12-31", 50)]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [_inst("2025-12-31", 150)]}},
    }
    facts.update(extra)
    return {"facts": {
        "us-gaap": facts,
        "dei": {"EntityCommonStockSharesOutstanding": {
            "units": {"shares": [_inst("2025-12-31", 100, form="10-K")]}}},
    }}


def test_as_oito_metricas_saem_com_a_conta_certa():
    # preço 20 x 100 ações = capitalização 2000.
    r = multiplos(_emissor(), preco=20.0)
    assert r["suportado"] is True
    assert r["market_cap"]["valor"] == 2000
    m = r["metricas"]

    assert m["pl"]["valor"] == 20.0            # 2000 / 100 de lucro TTM
    assert m["pvp"]["valor"] == pytest.approx(3.3333, abs=1e-3)  # 2000 / 600
    # ROE com patrimônio MÉDIO (600+400)/2 = 500 -> 100/500 = 0,20.
    assert m["roe"]["valor"] == 0.2
    assert m["roe"]["valor"] != pytest.approx(100 / 600, abs=1e-4), (
        "usar só o patrimônio atual misturaria fluxo com estoque")
    # EV = 2000 + 150 - 50 = 2100; EBITDA = 120 + 20 = 140.
    assert m["ev_ebitda"]["valor"] == pytest.approx(15.0, abs=1e-6)
    assert m["divida_liquida_ebitda"]["valor"] == pytest.approx(100 / 140, abs=1e-4)
    # FCF = 160 - 40 = 120; / 2000 = 6%.
    assert m["fcf_yield"]["valor"] == pytest.approx(0.06, abs=1e-6)
    assert m["margem_liquida"]["valor"] == pytest.approx(0.1, abs=1e-6)


def test_pvp_usa_o_balanco_e_nao_uma_soma_de_trimestres():
    """Patrimônio somado ao longo do ano daria 600+400=1000 e um P/VP de 2,0."""
    r = multiplos(_emissor(), preco=20.0)
    assert r["metricas"]["pvp"]["valor"] == pytest.approx(2000 / 600, abs=1e-3)
    assert r["metricas"]["pvp"]["proveniencia"]["nota"] == "estoque, não TTM"


# ── armadilha 6: EBITDA sem D&A confiável ───────────────────────────────────


def test_sem_d_e_a_o_ev_ebitda_fica_indisponivel_com_motivo():
    dados = _emissor()
    del dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None
    assert "D&A" in m["ev_ebitda"]["indisponivel"]
    # E não caiu para "EBITDA = operacional", que seria plausível e errado.
    assert "EBITDA sem D&A" in m["ev_ebitda"]["indisponivel"]


def test_uma_metrica_indisponivel_nao_derruba_as_outras():
    dados = _emissor()
    del dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None
    assert m["pl"]["valor"] == 20.0, "P/L não depende de D&A"


def test_sem_preco_nao_ha_multiplo_de_preco_mas_ha_margem():
    m = multiplos(_emissor(), preco=None)["metricas"]
    assert m["pl"]["valor"] is None
    assert m["margem_liquida"]["valor"] == pytest.approx(0.1, abs=1e-6)


# ── crescimento precisa de oito trimestres ──────────────────────────────────


def test_crescimento_compara_ttm_contra_ttm():
    fatos = _quatro_trimestres([100, 100, 100, 100], ano=2024) + \
        _quatro_trimestres([125, 125, 125, 125], ano=2025)
    dados = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": fatos}}}}}
    valor, prov = _crescimento_ttm(dados)
    assert valor == pytest.approx(0.25, abs=1e-6)
    assert prov["receita_TTM"] == 500 and prov["receita_TTM_anterior"] == 400


def test_crescimento_sem_oito_trimestres_e_indisponivel():
    r = multiplos(_emissor(), preco=20.0)  # só 4 trimestres
    assert r["metricas"]["crescimento_receita"]["valor"] is None
    assert "8" in r["metricas"]["crescimento_receita"]["indisponivel"]


# ── proveniência: número que não dá para conferir é indistinguível de errado ─


def test_cada_metrica_traz_periodo_formulario_accession_e_tags():
    m = multiplos(_emissor(), preco=20.0)["metricas"]
    prov = m["pl"]["proveniencia"]
    assert prov["formula"] == "market_cap / lucro_liquido_TTM"
    assert "NetIncomeLoss" in prov["tags"]
    tri = prov["de"]["trimestres"][0]
    assert {"fim", "form", "accn", "filed", "derivado_por_diferenca"} <= set(tri)


def test_metrica_indisponivel_sempre_diz_por_que():
    dados = _emissor()
    del dados["facts"]["us-gaap"]["NetIncomeLoss"]
    m = multiplos(dados, preco=20.0)["metricas"]
    for nome in ("pl", "roe", "margem_liquida"):
        assert m[nome]["valor"] is None
        assert m[nome]["indisponivel"], f"{nome} ficou sem motivo"


# ── tag alternativa: emissor troca de nome entre exercícios ─────────────────


def test_tag_alternativa_de_receita_e_aceita():
    fatos = _quatro_trimestres([250, 250, 250, 250])
    dados = _emissor()
    del dados["facts"]["us-gaap"]["Revenues"]
    dados["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"] = {
            "units": {"USD": fatos}}
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["margem_liquida"]["valor"] == pytest.approx(0.1, abs=1e-6)
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in \
        m["margem_liquida"]["proveniencia"]["tags"]


# ── o bug que o teste do YTD pegou, e o que ele abriu ───────────────────────
#
# A primeira versão classificava só por DURAÇÃO e descartava o fato do ano
# inteiro como "não é trimestre". Num emissor que só publica acumulado, era
# justamente ele que faltava para derivar o Q4 -- o TTM saía com três
# trimestres. O agrupamento passou a ser por INÍCIO: mesmo `start` repetido é
# a assinatura do acumulado, e é o único sinal que separa "12 meses porque é o
# exercício" de "12 meses porque é o quarto acumulado da série".


def test_serie_ytd_precisa_do_fato_do_ano_para_o_q4():
    """Sem o fato de 12 meses não há Q4, e o TTM não fecha."""
    sem_o_ano = [
        _f("2025-01-01", "2025-03-31", 100),
        _f("2025-01-01", "2025-06-30", 300),
        _f("2025-01-01", "2025-09-30", 600),
    ]
    assert [t["val"] for t in _trimestres_de(sem_o_ano)] == [100, 200, 300]
    with pytest.raises(SemDado, match="TTM precisa de 4"):
        _ttm(sem_o_ano)

    com_o_ano = sem_o_ano + [_f("2025-01-01", "2025-12-31", 1000)]
    assert _ttm(com_o_ano)[0] == 1000


def test_serie_com_buraco_nao_vira_trimestre_de_nove_meses():
    """Q1 e depois direto o ano: a diferença são 9 meses, não um trimestre."""
    tris = _trimestres_de([
        _f("2025-01-01", "2025-03-31", 100),
        _f("2025-01-01", "2025-12-31", 1000),
    ])
    assert [t["val"] for t in tris] == [100], (
        "900 em 9 meses não pode ser emitido como se fosse um trimestre")


def test_primeiro_periodo_de_seis_meses_serve_de_base_sem_ser_emitido():
    """Emissor que estreia com um semestre: o semestre não é trimestre, mas o
    acumulado seguinte menos ele é."""
    tris = _trimestres_de([
        _f("2025-01-01", "2025-06-30", 300),
        _f("2025-01-01", "2025-09-30", 600),
    ])
    assert [t["val"] for t in tris] == [300], "600-300=300 é o trimestre"
    assert tris[0]["start"] == "2025-06-30"


# ── ano fiscal irregular (NVDA/MRVL fecham em janeiro; 52/53 semanas) ───────


def test_ano_fiscal_irregular_de_52_semanas():
    """Trimestre de 13 semanas dá 91 dias e o exercício não bate com o
    calendário -- a janela precisa aceitar isso sem esticar para semestre."""
    fatos = [
        _f("2025-01-27", "2025-04-27", 100),
        _f("2025-04-28", "2025-07-27", 110),
        _f("2025-07-28", "2025-10-26", 120),
        _f("2025-10-27", "2026-01-25", 130),
    ]
    total, prov = _ttm(fatos)
    assert total == 460
    assert prov["periodo"] == "2025-01-27..2026-01-25"


def test_ano_fiscal_irregular_em_serie_acumulada():
    """O mesmo emissor de janeiro, mas publicando acumulado."""
    fatos = [
        _f("2025-01-27", "2025-04-27", 100),
        _f("2025-01-27", "2025-07-27", 210),
        _f("2025-01-27", "2025-10-26", 330),
        _f("2025-01-27", "2026-01-25", 460),
    ]
    assert [t["val"] for t in _trimestres_de(fatos)] == [100, 110, 120, 130]
    assert _ttm(fatos)[0] == 460


# ═══ O que o MODO SOMBRA achou e a fixture não acharia ══════════════════════
#
# Primeira execução contra dado real (NVDA, 28/08/2026): margem líquida de
# 1766%. Lucro TTM de US$ 192,9 bi sobre receita TTM de US$ 10,9 bi -- o lucro
# dos trimestres atuais, a receita do exercício encerrado em JANEIRO DE 2020.
#
# Duas falhas independentes, e a segunda sobreviveria à correção da primeira:
#
#   1. `_fatos` devolvia o PRIMEIRO tag com qualquer dado. Na NVDA isso era
#      RevenueFromContractWithCustomerExcludingAssessedTax, abandonado depois
#      do FY2020 -- preferência estava ganhando de recência.
#   2. Nada conferia se dois TTM combinados cobriam a MESMA janela. Mesmo com
#      o tag certo, um conceito com histórico mais curto faria a razão
#      comparar eras.


def _tag_parada_e_tag_atual():
    """A situação da NVDA: um tag descontinuado com histórico velho e um tag
    corrente com o histórico de verdade."""
    return {"facts": {"us-gaap": {
        # Descontinuado em 2020 -- primeiro na ordem de preferência.
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": _quatro_trimestres([10, 10, 10, 10], ano=2019)}},
        # Corrente, mas depois na ordem de preferência.
        "Revenues": {"units": {"USD": _quatro_trimestres([500, 500, 500, 500])}},
    }}}


def test_tag_descontinuado_perde_para_o_atual():
    from agent.fundamentos_sec import _fatos
    fatos, tag = _fatos(_tag_parada_e_tag_atual(), "receita")
    assert tag == "Revenues", (
        "recência tem que vencer preferência -- foi assim que a receita da "
        "NVDA saiu com seis anos de atraso")
    assert _ttm(fatos)[0] == 2000


def test_preferencia_ainda_vale_quando_os_dois_estao_atuais():
    """A correção não pode virar 'ignore a ordem': com os dois tags no mesmo
    período, a preferência continua decidindo."""
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": _quatro_trimestres([500, 500, 500, 500])}},
        "Revenues": {"units": {"USD": _quatro_trimestres([400, 400, 400, 400])}},
    }}}
    _, tag = _fatos(dados, "receita")
    assert tag == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_a_margem_absurda_da_nvda_nao_sai_mais():
    """O caso inteiro, ponta a ponta: lucro atual, receita velha no tag
    descontinuado. Antes: 1766%. Agora: a receita certa é encontrada."""
    dados = _tag_parada_e_tag_atual()
    dados["facts"]["us-gaap"]["NetIncomeLoss"] = {
        "units": {"USD": _quatro_trimestres([50, 50, 50, 50])}}
    m = multiplos(dados, preco=None)["metricas"]
    # 200 de lucro sobre 2000 de receita = 10%, não 200/40 = 500%.
    assert m["margem_liquida"]["valor"] == pytest.approx(0.1, abs=1e-6)


def test_ttm_de_janelas_diferentes_nao_vira_razao():
    """A segunda trava, sozinha: mesmo sem tag descontinuado, dois conceitos
    com históricos de eras distintas não podem ser divididos."""
    dados = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": _quatro_trimestres([100] * 4, ano=2019)}},
        "NetIncomeLoss": {"units": {"USD": _quatro_trimestres([50] * 4, ano=2025)}},
    }}}
    m = multiplos(dados, preco=None)["metricas"]
    assert m["margem_liquida"]["valor"] is None
    assert "janelas diferentes" in m["margem_liquida"]["indisponivel"]


def test_ebitda_nao_soma_operacional_e_d_e_a_de_eras_diferentes():
    dados = _emissor()
    dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"] = {
        "units": {"USD": _quatro_trimestres([5] * 4, ano=2019)}}
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None
    assert "janelas diferentes" in m["ev_ebitda"]["indisponivel"]


def test_fcf_nao_subtrai_capex_de_outra_era():
    dados = _emissor()
    dados["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"] = {
        "units": {"USD": _quatro_trimestres([10] * 4, ano=2019)}}
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["fcf_yield"]["valor"] is None
    assert "janelas diferentes" in m["fcf_yield"]["indisponivel"]


def test_janela_igual_continua_passando():
    """A trava não pode reprovar o caso bom -- inclusive com um dia de folga
    entre rótulos do mesmo trimestre fiscal."""
    from agent.fundamentos_sec import _janela_incompativel
    assert _janela_incompativel(
        {"periodo": "2025-01-01..2025-12-31"},
        {"periodo": "2025-01-02..2025-12-31"}) is None
    assert _janela_incompativel({"periodo": "2025-01-01..2025-12-31"}) is None
    assert _janela_incompativel() is None


# ═══ Segunda rodada do modo sombra: a procuração virou fonte ════════════════
#
# MRVL e NVDA, 28/08/2026. Com a receita já corrigida, a proveniência do P/L
# mostrou um trimestre vindo de `DEF 14A` -- a procuração de assembleia, não
# demonstração financeira:
#
#   MRVL  "fim": "2026-01-31", "form": "DEF 14A", "accn": "0001104659-26-060253"
#   NVDA  "fim": "2026-01-25", "form": "DEF 14A", "accn": "0001045810-26-000036"
#
# Entrou porque `_mais_recente()` desempata por `filed`, e a procuração é
# arquivada DEPOIS do 10-K do mesmo período. Desde a regra de "pay versus
# performance" da SEC ela traz NetIncomeLoss etiquetado, então o fato existe
# e vencia o 10-K por ser mais recente. Nos dois casos o número parecia
# plausível -- que é justamente por que precisa de trava.


def test_procuracao_nao_e_fonte_de_numero():
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _f("2025-10-01", "2025-12-31", 100, form="10-K",
           filed="2026-02-25", accn="10k-1"),
        # Mesmo período, arquivado DEPOIS, e por isso vencia antes.
        _f("2025-10-01", "2025-12-31", 999, form="DEF 14A",
           filed="2026-05-12", accn="proxy-1"),
    ]}}}}}
    fatos, _ = _fatos(dados, "lucro_liquido")
    assert [f["val"] for f in fatos] == [100]
    assert all(f["form"] == "10-K" for f in fatos)


@pytest.mark.parametrize("form", ["10-K", "10-Q", "10-K/A", "10-Q/A"])
def test_demonstracao_periodica_e_emenda_continuam_valendo(form):
    """A trava não pode derrubar a emenda: 10-K/A é a versão CORRIGIDA."""
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _f("2025-10-01", "2025-12-31", 100, form=form)]}}}}}
    assert _fatos(dados, "lucro_liquido")[0][0]["form"] == form


@pytest.mark.parametrize("form", ["DEF 14A", "S-1", "8-K", "424B5"])
def test_formulario_fora_da_lista_nao_entra(form):
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _f("2025-10-01", "2025-12-31", 100, form=form)]}}}}}
    with pytest.raises(SemDado):
        _fatos(dados, "lucro_liquido")


def test_a_emenda_ainda_ganha_do_original():
    """Filtrar formulário não pode desligar a regra de reapresentação."""
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _f("2025-01-01", "2025-03-31", 100, form="10-Q",
           filed="2025-04-30", accn="orig"),
        _f("2025-01-01", "2025-03-31", 88, form="10-Q/A",
           filed="2025-09-01", accn="emenda"),
    ]}}}}}
    fatos, _ = _fatos(dados, "lucro_liquido")
    from agent.fundamentos_sec import _mais_recente
    assert _mais_recente(fatos)["val"] == 88


def test_acoes_tambem_ignoram_a_procuracao():
    """A capa da procuração também traz contagem de ações."""
    dados = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {
        "units": {"shares": [
            _inst("2026-05-21", 874_800_000, form="10-Q",
                  filed="2026-05-28", accn="10q"),
            _inst("2026-05-30", 1, form="DEF 14A",
                  filed="2026-06-01", accn="proxy"),
        ]}}}}}
    val, prov = _acoes_em_circulacao(dados)
    assert val == 874_800_000 and prov["form"] == "10-Q"


def test_recencia_do_tag_olha_so_formulario_aceito():
    """Um tag cujo dado recente só existe em procuração não pode parecer
    'mais atual' que o tag com 10-Q de verdade."""
    from agent.fundamentos_sec import _fatos
    dados = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [_f("2026-01-01", "2026-03-31", 5,
                                 form="DEF 14A", filed="2026-06-01")]}},
        "Revenues": {"units": {"USD": _quatro_trimestres([100] * 4, ano=2025)}},
    }}}
    _, tag = _fatos(dados, "receita")
    assert tag == "Revenues"


# ── dívida: LongTermDebt já inclui a parcela circulante ─────────────────────


def _com_divida(tag_longa: str, valor_longa: float, curta: float = 40.0):
    dados = _emissor()
    del dados["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    dados["facts"]["us-gaap"][tag_longa] = {
        "units": {"USD": [_inst("2025-12-31", valor_longa)]}}
    dados["facts"]["us-gaap"]["LongTermDebtCurrent"] = {
        "units": {"USD": [_inst("2025-12-31", curta)]}}
    return dados


def test_long_term_debt_e_saldo_total_e_nao_soma_a_curta():
    """`LongTermDebt` já inclui a parcela circulante -- somar a curta contaria
    duas vezes. Dívida inflada não estoura nada: só encarece o EV em silêncio.
    """
    r = multiplos(_com_divida("LongTermDebt", 150.0, curta=40.0), preco=20.0)
    # EV = cap 2000 + dívida 150 - caixa 50 = 2100 (não 2140).
    assert r["metricas"]["ev_ebitda"]["proveniencia"]["ev"] == 2100


def test_noncurrent_mais_curta_continua_somando():
    """Com o tag que é SÓ a parte não circulante, a soma é a conta certa."""
    r = multiplos(_com_divida("LongTermDebtNoncurrent", 150.0, curta=40.0),
                  preco=20.0)
    assert r["metricas"]["ev_ebitda"]["proveniencia"]["ev"] == 2140


# ═══ Conferência contra o anual publicado ═══════════════════════════════════
#
# O único ponto onde a nossa aritmética pode ser checada contra um número que
# a empresa publicou pronto. Importa mais justamente onde o risco é maior: o
# fluxo de caixa do 10-Q é SEMPRE acumulado, então quase todo trimestre de CFO
# e capex sai de uma subtração nossa. Na AOSL (28/08/2026) foram 3 dos 4, e o
# CFO somou -16,3 mi -- caixa operacional negativo no ano é possível, mas é
# exatamente o tipo de número que ninguém distingue de erro de diferenciação.


def _serie_ytd_com_anual(q1, q2, q3, q4, anual, ano=2025):
    """YTD como o fluxo de caixa publica, mais o anual do 10-K."""
    return [
        _f(f"{ano}-01-01", f"{ano}-03-31", q1),
        _f(f"{ano}-01-01", f"{ano}-06-30", q1 + q2),
        _f(f"{ano}-01-01", f"{ano}-09-30", q1 + q2 + q3),
        _f(f"{ano}-01-01", f"{ano}-12-31", anual, form="10-K"),
    ]


def test_ttm_confere_com_o_anual_e_registra_a_conferencia():
    fatos = _serie_ytd_com_anual(10, 20, 30, 40, anual=100)
    total, prov = _ttm(fatos)
    assert total == 100
    assert prov["conferido_contra_anual"]["valor"] == 100
    assert prov["conferido_contra_anual"]["form"] == "10-K"


def test_soma_que_nao_bate_com_o_anual_vira_indisponivel():
    """Se a diferenciação errar em algum trimestre, a soma diverge do anual --
    e o certo é não publicar número, não escolher um dos dois."""
    fatos = [
        _f("2025-01-01", "2025-03-31", 10),
        _f("2025-04-01", "2025-06-30", 20),
        _f("2025-07-01", "2025-09-30", 30),
        _f("2025-10-01", "2025-12-31", 40),
        # Anual do 10-K diz 999, mas os trimestres somam 100.
        _f("2025-01-01", "2025-12-31", 999, form="10-K"),
    ]
    with pytest.raises(SemDado, match="não bate com o anual"):
        _ttm(fatos)


def test_diferenca_de_arredondamento_nao_reprova():
    """Trimestres publicados INDEPENDENTEMENTE do anual (numa série YTD o Q4
    sai do próprio anual e os dois batem por construção). Centavo de
    arredondamento entre as duas fontes não pode derrubar a métrica."""
    fatos = _quatro_trimestres([25, 25, 25, 25]) + [
        _f("2025-01-01", "2025-12-31", 100.05, form="10-K")]
    total, prov = _ttm(fatos)
    assert total == 100
    assert prov["conferido_contra_anual"]["valor"] == 100.05


def test_ttm_sem_anual_correspondente_nao_tem_o_que_conferir():
    """Só existe conferência quando a empresa publicou o anual daquele mesmo
    período. Sem ele, o silêncio é a resposta certa, não um erro."""
    fatos = _quatro_trimestres([10, 20, 30, 40], ano=2025) + \
        _quatro_trimestres([50, 60, 70, 80], ano=2026)
    total, prov = _ttm(fatos)
    assert total == 260, "os quatro trimestres de 2026"
    assert "conferido_contra_anual" not in prov


def test_o_anual_nao_e_confundido_com_um_dos_trimestres():
    """A janela do TTM começa e termina nos trimestres; o fato de 3 meses que
    coincide com uma ponta não pode ser lido como 'o anual'."""
    fatos = _quatro_trimestres([25, 25, 25, 25])
    total, prov = _ttm(fatos)
    assert total == 100
    assert "conferido_contra_anual" not in prov


# ── mensagem de indisponível que se diagnostica sozinha ─────────────────────


def test_indisponivel_nomeia_o_tag_usado_e_os_parecidos():
    """MRVL, 28/08/2026: o D&A saiu '0 trimestre(s) utilizável(is)' sem dizer
    qual tag foi usado nem o que mais havia -- descobrir exigia um script à
    parte contra a SEC."""
    dados = _emissor()
    # O tag conhecido existe, mas só com o exercício inteiro: nenhum trimestre.
    dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"] = {
        "units": {"USD": [_f("2025-01-01", "2025-12-31", 20)]}}
    # E há um parecido fora da lista, que é a pista que faltava.
    dados["facts"]["us-gaap"]["AmortizationOfIntangibleAssets"] = {
        "units": {"USD": _quatro_trimestres([5] * 4)}}
    motivo = multiplos(dados, preco=20.0)["metricas"]["ev_ebitda"]["indisponivel"]
    assert "DepreciationDepletionAndAmortization" in motivo
    assert "AmortizationOfIntangibleAssets" in motivo
    assert "fora da lista" in motivo


def test_sem_parecido_a_mensagem_nao_inventa_pista():
    dados = _emissor()
    del dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    motivo = multiplos(dados, preco=20.0)["metricas"]["ev_ebitda"]["indisponivel"]
    assert "fora da lista" not in motivo


def test_pista_ignora_tag_que_ja_esta_na_lista():
    from agent.fundamentos_sec import _tags_parecidos
    dados = {"facts": {"us-gaap": {
        "DepreciationDepletionAndAmortization": {},   # já é conhecido
        "AmortizationOfIntangibleAssets": {},         # pista de verdade
        "Revenues": {},                               # outra família
    }}}
    assert _tags_parecidos(dados, "dep_amort") == ["AmortizationOfIntangibleAssets"]


# ═══ Conceito publicado PARTIDO em mais de um tag (MRVL) ════════════════════
#
# Terceira rodada do modo sombra, 28/08/2026. O D&A da MRVL saía indisponível
# porque `DepreciationAndAmortization` existe mas não rende trimestre nenhum.
# A lista de pistas -- que esta mesma rodada estreou -- mostrou oito
# candidatos, e SEIS são armadilha: acumulado de balanço
# (Accumulated..., FiniteLived...AccumulatedAmortization) e cronograma FUTURO
# de amortização (...NextTwelveMonths, ...AfterYearFive, ...RemainderOf
# FiscalYear), além da amortização de custo de dívida, que é financeira.
#
# Sobram os dois que são despesa operacional do período, e é assim que a MRVL
# publica: separado, porque a amortização de intangível das aquisições é
# grande demais para ficar embutida.


def _mrvl_dep_amort():
    """O formato da MRVL: o tag combinado existe, mas só com o exercício
    inteiro (nenhum trimestre), e os dois componentes vêm trimestrais."""
    dados = _emissor()
    dados["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"] = {
        "units": {"USD": [_f("2025-01-01", "2025-12-31", 80)]}}
    dados["facts"]["us-gaap"]["Depreciation"] = {
        "units": {"USD": _quatro_trimestres([3] * 4)}}
    dados["facts"]["us-gaap"]["AmortizationOfIntangibleAssets"] = {
        "units": {"USD": _quatro_trimestres([2] * 4)}}
    return dados


def test_conceito_partido_e_reconstituido_pela_soma():
    r = multiplos(_mrvl_dep_amort(), preco=20.0)
    prov = r["metricas"]["ev_ebitda"]["proveniencia"]
    # D&A = 12 (Depreciation) + 8 (Amortização) = 20; EBITDA = 120 + 20 = 140.
    assert prov["ebitda_reconstruido_de"]["dep_amort_TTM"] == 20
    assert prov["tags"][1] == "Depreciation + AmortizationOfIntangibleAssets"


def test_o_composto_declara_que_e_reconstituicao():
    """Quem lê tem que saber que este número foi somado por nós."""
    r = multiplos(_mrvl_dep_amort(), preco=20.0)
    prov = r["metricas"]["ev_ebitda"]["proveniencia"]["dep_amort_de"]
    assert [c["tag"] for c in prov["composto_de"]] == [
        "Depreciation", "AmortizationOfIntangibleAssets"]
    assert "reconstituído" in prov["nota"]


def test_o_tag_unico_continua_ganhando_quando_funciona():
    """O composto é remendo do buraco, não substituto do caminho normal."""
    r = multiplos(_emissor(), preco=20.0)   # tem o tag combinado trimestral
    prov = r["metricas"]["ev_ebitda"]["proveniencia"]
    assert prov["tags"][1] == "DepreciationDepletionAndAmortization"
    assert "composto_de" not in prov["dep_amort_de"]


def test_componente_faltando_nao_vira_soma_parcial():
    """Meia soma seria um D&A menor e plausível -- pior que indisponível."""
    dados = _mrvl_dep_amort()
    del dados["facts"]["us-gaap"]["AmortizationOfIntangibleAssets"]
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None


def test_componentes_de_eras_diferentes_nao_somam():
    """A mesma trava de janela vale dentro do composto: somar Depreciation de
    2025 com Amortização de 2019 compararia eras por outra porta."""
    dados = _mrvl_dep_amort()
    dados["facts"]["us-gaap"]["AmortizationOfIntangibleAssets"] = {
        "units": {"USD": _quatro_trimestres([2] * 4, ano=2019)}}
    m = multiplos(dados, preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None


@pytest.mark.parametrize("armadilha", [
    "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
    "FiniteLivedIntangibleAssetsAccumulatedAmortization",
    "FiniteLivedIntangibleAssetsAmortizationExpenseNextTwelveMonths",
    "FiniteLivedIntangibleAssetsAmortizationExpenseAfterYearFive",
    "FiniteLivedIntangibleAssetsAmortizationExpenseRemainderOfFiscalYear",
    "AmortizationOfFinancingCostsAndDiscounts",
])
def test_os_seis_candidatos_de_armadilha_ficam_de_fora(armadilha):
    """Todos têm 'Depreciation' ou 'Amortization' no nome e apareceram na
    lista de pistas da MRVL. Nenhum é despesa operacional do período: os dois
    primeiros são acumulado de BALANÇO, os três seguintes são cronograma
    FUTURO de nota explicativa, e o último é financeiro."""
    from agent.fundamentos_sec import TAGS, TAGS_COMPOSTOS
    assert armadilha not in TAGS["dep_amort"]
    assert all(armadilha not in partes
               for partes in TAGS_COMPOSTOS["dep_amort"])


def test_composto_carrega_o_valor_de_cada_parcela():
    """A soma sem as parcelas não dá para conferir -- que é o defeito que a
    proveniência inteira existe para não ter."""
    r = multiplos(_mrvl_dep_amort(), preco=20.0)
    partes = r["metricas"]["ev_ebitda"]["proveniencia"]["dep_amort_de"]["composto_de"]
    assert [(c["tag"], c["valor"]) for c in partes] == [
        ("Depreciation", 12), ("AmortizationOfIntangibleAssets", 8)]
    assert sum(c["valor"] for c in partes) == 20


# ═══ "Só anual" é veredito, não lacuna de tag ═══════════════════════════════
#
# Quarta rodada do modo sombra, com o dado real da MRVL:
#
#   DepreciationAndAmortization       6 fatos, TODOS anuais  -> 0 trimestres
#   Depreciation                     15 fatos, TODOS anuais  -> 0 trimestres
#   AmortizationOfIntangibleAssets   65 fatos               -> 25 trimestres
#
# Falta METADE do D&A em base trimestral. O composto que a rodada anterior
# criou NÃO resolve este caso -- somar a depreciação anual (exercício até
# 31/01) com a amortização TTM (ago-ago) seria a armadilha de janelas.
#
# O conceito é irreconstituível em TTM ali, e a mensagem tem que dizer isso
# em vez de continuar listando tags: cada sugestão custa uma rodada de
# investigação a quem for ler.


def _mrvl_real():
    """O formato que o diagnóstico encontrou na MRVL."""
    dados = _emissor()
    for anual in ("DepreciationDepletionAndAmortization", "Depreciation"):
        dados["facts"]["us-gaap"][anual] = {"units": {"USD": [
            _f("2024-02-04", "2025-02-01", 177_000_000, form="10-K"),
            _f("2025-02-02", "2026-01-31", 221_700_000, form="10-K"),
        ]}}
    dados["facts"]["us-gaap"]["AmortizationOfIntangibleAssets"] = {
        "units": {"USD": _quatro_trimestres([225_000_000] * 4)}}
    return dados


def test_so_anual_reconhece_o_conceito_irreconstituivel():
    from agent.fundamentos_sec import so_anual
    anuais = [_f("2024-02-04", "2025-02-01", 177, form="10-K"),
              _f("2025-02-02", "2026-01-31", 221, form="10-K")]
    assert so_anual(anuais) is True


def test_so_anual_e_falso_quando_ha_trimestre():
    from agent.fundamentos_sec import so_anual
    assert so_anual(_quatro_trimestres([10] * 4)) is False


def test_so_anual_e_falso_quando_o_acumulado_rende_trimestre():
    """Série YTD tem fato de 12 meses, mas rende trimestre por diferença --
    não é o caso 'só anual'."""
    from agent.fundamentos_sec import so_anual
    assert so_anual([
        _f("2025-01-01", "2025-03-31", 100),
        _f("2025-01-01", "2025-06-30", 300),
        _f("2025-01-01", "2025-09-30", 600),
        _f("2025-01-01", "2025-12-31", 1000),
    ]) is False


def test_so_anual_e_falso_sem_fato_nenhum():
    from agent.fundamentos_sec import so_anual
    assert so_anual([]) is False


def test_a_mensagem_nomeia_os_tags_que_so_saem_anuais():
    """Tag ausente se resolve acrescentando um nome à lista; "só anual" não se
    resolve com nome nenhum. A mensagem tem que separar os dois casos."""
    motivo = multiplos(_mrvl_real(), preco=20.0)["metricas"]["ev_ebitda"]["indisponivel"]
    assert "apenas em base ANUAL" in motivo
    assert "Depreciation" in motivo
    assert "compararia janelas diferentes" in motivo


def test_a_pista_continua_junto_do_veredito_anual():
    """As duas informações juntas é que explicam a MRVL: a depreciação só sai
    no 10-K E a amortização tem trimestre. Suprimir a lista de pistas aqui
    escondia a metade que existe -- pego por um teste anterior."""
    motivo = multiplos(_mrvl_real(), preco=20.0)["metricas"]["ev_ebitda"]["indisponivel"]
    assert "fora da lista" in motivo
    assert "AmortizationOfIntangibleAssets" in motivo


def test_o_composto_nao_salva_quando_falta_a_metade_trimestral():
    """A hipótese da rodada anterior, refutada pelo dado real: com
    `Depreciation` só anual, somar não é opção."""
    m = multiplos(_mrvl_real(), preco=20.0)["metricas"]
    assert m["ev_ebitda"]["valor"] is None
    assert m["divida_liquida_ebitda"]["valor"] is None


def test_o_resto_do_emissor_continua_saindo():
    """D&A irreconstituível não pode contaminar as outras seis métricas."""
    m = multiplos(_mrvl_real(), preco=20.0)["metricas"]
    assert m["pl"]["valor"] == 20.0
    assert m["margem_liquida"]["valor"] == pytest.approx(0.1, abs=1e-6)
    assert m["roe"]["valor"] == 0.2
