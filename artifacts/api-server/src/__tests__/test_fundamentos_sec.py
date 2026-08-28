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
