"""
Testes de parametros_volatilidade.py e parametros_macro.py -- as camadas
que transformam os dados do radar em parâmetros operacionais (classes de
vol, stops, sizing, vol de carteira com covariância, modo FOMC, sinal
overnight).

Sem rede e sem banco: tudo sai do snapshot embutido no radar + funções
puras. Os testes fixam `ref` explicitamente onde a data importa, pra não
virarem flaky quando o calendário passar do snapshot.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_parametros_volatilidade.py -v
"""
from datetime import date

import pytest

# Import de PACOTE (conftest.py já põe src/ no sys.path). Não colocar
# src/agent/ no path aqui: existe um agent.py DENTRO de agent/, então com o
# diretório no path o nome `agent` passa a resolver pro módulo em vez do
# pacote, e `from agent.market_alerts import ...` quebra.
from agent import parametros_macro as pm
from agent import parametros_volatilidade as pv

# Data anterior a qualquer earnings/FOMC do snapshot -- "dia normal".
REF_CALMA = date(2026, 8, 3)


# ── classes e conversões ───────────────────────────────────────────────────

def test_classe_vol_cobre_as_quatro_faixas():
    # Valores em vol semanal %, com exemplos MEDIDOS (não a coleta manual,
    # que subestimava): SNDK ~16.8, AMD ~10.7, NVDA ~5.5.
    assert pv.classe_vol(16.8) == "extrema"
    assert pv.classe_vol(10.7) == "alta"
    assert pv.classe_vol(5.5) == "media"
    assert pv.classe_vol(2.0) == "baixa"


def test_classe_vol_nos_limites_exatos():
    """Corte é inclusivo na borda de baixo -- fixar isso evita que uma
    recalibração futura mude o comportamento de borda sem querer."""
    assert pv.classe_vol(12.0) == "extrema"
    assert pv.classe_vol(11.99) == "alta"
    assert pv.classe_vol(8.0) == "alta"
    assert pv.classe_vol(7.99) == "media"
    assert pv.classe_vol(5.0) == "media"
    assert pv.classe_vol(4.99) == "baixa"


def test_conversoes_de_vol_usam_raiz_do_tempo():
    assert pv.vol_diaria(10.0) == pytest.approx(10.0 / (5 ** 0.5))
    assert pv.vol_anualizada(10.0) == pytest.approx(10.0 * (52 ** 0.5))


# ── parametros por ticker ─────────────────────────────────────────────────

def test_parametros_de_ticker_conhecido():
    """Sem overlay de vol medida, `parametros` usa o valor embutido. Asserta
    a RELAÇÃO (stop = vol × multiplicador da classe) em vez de fixar o nome
    da classe: a classe depende dos cortes, que são recalibrados quando a
    distribuição medida muda -- fixá-la aqui quebraria o teste a cada
    recalibração sem apontar defeito nenhum."""
    p = pv.parametros("MU", REF_CALMA)
    assert p["modo"] == "normal"
    assert p["vol_semanal_pct"] == pytest.approx(7.89)
    assert p["classe"] == pv.classe_vol(p["vol_operacional_pct"])
    assert p["stop_sugerido_pct"] == pytest.approx(
        p["vol_operacional_pct"] * pv.MULT_STOP[p["classe"]], abs=0.01)


def test_parametros_de_ticker_fora_do_radar_e_none():
    assert pv.parametros("XXXX", REF_CALMA) is None


def test_classe_extrema_avisa_pra_reduzir_posicao():
    p = pv.parametros("SNDK", REF_CALMA)
    assert p["classe"] == "extrema"
    assert "reduzir posição" in p["nota_stop"]


def test_modo_earnings_usa_implicita_quando_maior(monkeypatch):
    """No snapshot atual NENHUM ticker tem vol_sem (TEMA_IA) e
    move_impl_sem (REACAO_EARNINGS) ao mesmo tempo -- os com implícita são
    do screening de varejo/China, e o tema IA é de chips. O ramo existe pra
    quando as duas coletas se cruzarem, então é exercitado por injeção."""
    monkeypatch.setitem(pv.REACAO_EARNINGS, "NVDA", {"move_impl_sem": 12.0, "evr": 5.0})
    p = pv.parametros("NVDA", date(2026, 8, 24))  # earnings 26/08
    assert p["vol_operacional_pct"] == pytest.approx(12.0)
    assert "implícita 12.0%" in p["modo"]
    # a implícita muda a CLASSE, e com ela o multiplicador do stop
    assert p["classe"] == "extrema"


def test_implicita_menor_que_a_historica_nao_manda(monkeypatch):
    """Implícita só domina quando é MAIOR -- se o mercado precifica menos
    movimento que o histórico normal do papel, vale a precaução (x1.5)."""
    monkeypatch.setitem(pv.REACAO_EARNINGS, "SNDK", {"move_impl_sem": 2.0})
    monkeypatch.setitem(pv.EARNINGS, "SNDK", {"data": "2026-08-26", "setor": "semis"})
    p = pv.parametros("SNDK", date(2026, 8, 24))
    assert p["vol_operacional_pct"] == pytest.approx(18.72 * 1.5, abs=0.01)
    assert "precaução" in p["modo"]


def test_modo_earnings_sem_implicita_infla_por_precaucao():
    # NVDA reporta 26/08 e não tem move implícito no snapshot -> x1.5.
    p = pv.parametros("NVDA", date(2026, 8, 24))
    assert "earnings" in p["modo"]
    assert p["vol_operacional_pct"] == pytest.approx(1.50 * 1.5, abs=0.01)


def test_fora_da_janela_de_earnings_fica_normal():
    assert pv.parametros("NVDA", REF_CALMA)["modo"] == "normal"


# ── stop e sizing ─────────────────────────────────────────────────────────

def test_stop_sugerido_converte_para_preco():
    s = pv.stop_sugerido("MU", 100.0, REF_CALMA)
    assert s["stop_preco"] == pytest.approx(100 * (1 - s["stop_pct"] / 100), abs=0.01)


def test_tamanho_maximo_respeita_o_orcamento_de_risco():
    r = pv.tamanho_maximo("SNDK", 10_000, 1.0, REF_CALMA)
    # arriscar 1% de 10k = $100; com stop de X%, posição = 100 / (X/100)
    assert r["risco_valor"] == pytest.approx(100.0)
    assert r["posicao_maxima"] == pytest.approx(100 / (r["stop_pct"] / 100), abs=1)
    # vol extrema -> posição pequena em relação ao capital
    assert r["pct_do_capital"] < 10


def test_tamanho_maximo_nunca_passa_do_capital():
    # Vol baixa + risco alto poderia sugerir posição > capital; tem teto.
    r = pv.tamanho_maximo("NVDA", 1_000, 50.0, REF_CALMA)
    assert r["posicao_maxima"] <= 1_000
    assert r["pct_do_capital"] <= 100


# ── carteira com covariância (a correção do sizing ingênuo) ───────────────

CARTEIRA = {"MU": 1, "SMCI": 1, "ARM": 1, "MRVL": 1, "AVGO": 1}


def test_vol_de_carteira_fica_abaixo_da_media_ponderada():
    """Diversificação só existe se a vol do conjunto for MENOR que a média
    das vols individuais -- é a diferença entre somar risco e compor risco."""
    r = pv.vol_carteira(CARTEIRA)
    assert r["vol_carteira_semanal_pct"] < r["vol_sem_diversificacao_pct"]
    assert r["beneficio_diversificacao_pct"] > 0


def test_carteira_de_um_ticker_so_nao_tem_beneficio():
    r = pv.vol_carteira({"MU": 1})
    assert r["vol_carteira_semanal_pct"] == pytest.approx(r["vol_sem_diversificacao_pct"], abs=0.01)
    assert r["beneficio_diversificacao_pct"] == pytest.approx(0.0, abs=0.1)


def test_correlacao_maior_reduz_o_beneficio():
    normal = pv.vol_carteira(CARTEIRA, 1.0)
    stress = pv.vol_carteira(CARTEIRA, pv.STRESS_FATOR)
    assert stress["vol_carteira_semanal_pct"] > normal["vol_carteira_semanal_pct"]
    assert stress["beneficio_diversificacao_pct"] < normal["beneficio_diversificacao_pct"]


def test_ticker_sem_vol_e_ignorado_e_reportado():
    r = pv.vol_carteira({"MU": 1, "XXXX": 1})
    assert "XXXX" in r["ignorados_sem_vol"]
    assert "XXXX" not in r["pesos_normalizados"]
    # peso do que sobrou é renormalizado pra 100%
    assert sum(r["pesos_normalizados"].values()) == pytest.approx(1.0, abs=0.01)


def test_contribuicao_de_risco_soma_cem_por_cento():
    contribs = pv.contribuicao_risco(CARTEIRA)
    assert sum(c["contribuicao_risco_pct"] for c in contribs) == pytest.approx(100.0, abs=0.5)


def test_posicao_mais_volatil_contribui_mais_que_o_peso():
    """O ponto da métrica: peso em capital != peso em risco."""
    contribs = {c["ticker"]: c for c in pv.contribuicao_risco(CARTEIRA)}
    smci, avgo = contribs["SMCI"], contribs["AVGO"]
    assert smci["contribuicao_risco_pct"] > smci["peso_pct"]
    assert avgo["contribuicao_risco_pct"] < avgo["peso_pct"]


def test_stress_reporta_encolhimento_da_diversificacao():
    st = pv.stress_carteira(CARTEIRA)
    assert st["aumento_pct"] > 0
    assert st["beneficio_diversificacao_stress_pct"] < st["beneficio_diversificacao_normal_pct"]


def test_pares_concentrados_acha_o_mesmo_trade():
    pares = pv.pares_concentrados(["MU", "SNDK", "CEG"])
    assert [p["par"] for p in pares] == [("MU", "SNDK")]


# ── camada macro: FOMC ────────────────────────────────────────────────────

def test_dot_plot_derivado_do_mes():
    assert pm.tem_dot_plot("2026-09-16") is True    # setembro
    assert pm.tem_dot_plot("2026-12-09") is True    # dezembro
    assert pm.tem_dot_plot("2026-10-28") is False   # outubro
    assert pm.tem_dot_plot("data-ruim") is False


def test_calendario_fomc_vem_do_market_alerts():
    """Não pode haver um segundo calendário: o do módulo tem que ser
    exatamente o que market_alerts.MACRO_EVENTS já mantém."""
    from agent.market_alerts import MACRO_EVENTS
    assert pm._fomc_datas() == list(MACRO_EVENTS["FOMC"])


def test_em_janela_macro_pega_reuniao_proxima():
    ev = pm.em_janela_macro(date(2026, 9, 14))  # FOMC 16/09, 2 dias depois
    assert ev["data"] == "2026-09-16" and ev["dias"] == 2 and ev["sep"] is True


def test_em_janela_macro_ignora_reuniao_distante():
    assert pm.em_janela_macro(REF_CALMA) is None


def test_fomc_infla_vol_e_stop():
    normal = pm.parametros_completos("MU", REF_CALMA)
    fomc = pm.parametros_completos("MU", date(2026, 9, 15))
    assert "FOMC" in fomc["modo"] and "FOMC" not in normal["modo"]
    assert fomc["vol_operacional_pct"] > normal["vol_operacional_pct"]
    assert fomc["stop_sugerido_pct"] > normal["stop_sugerido_pct"]


def test_beta_alto_sofre_multiplicador_extra_no_dot_plot():
    """SNDK tem beta 3.79 (>= 2.5) -- em dot plot leva o adicional."""
    p = pm.parametros_completos("SNDK", date(2026, 9, 15))
    assert p["mult_macro"] == pytest.approx(pm.MULT_FOMC_SEP * pm.MULT_EXTRA_BETA_ALTO, abs=0.001)
    assert "beta alto" in p["modo"]


def test_beta_baixo_nao_leva_o_adicional():
    p = pm.parametros_completos("NVDA", date(2026, 9, 15))  # beta baixo
    assert p["mult_macro"] == pytest.approx(pm.MULT_FOMC_SEP, abs=0.001)


def test_fora_da_janela_macro_nao_altera_nada():
    assert pm.parametros_completos("MU", REF_CALMA) == pv.parametros("MU", REF_CALMA)


def test_reuniao_sem_dot_plot_usa_multiplicador_menor():
    p = pm.parametros_completos("SNDK", date(2026, 10, 27))  # FOMC 28/10, sem SEP
    assert p["mult_macro"] == pytest.approx(pm.MULT_FOMC, abs=0.001)
    assert "dot plot" not in p["modo"]


# ── camada macro: sinal overnight ─────────────────────────────────────────

def test_overnight_estima_impacto_por_correlacao():
    # EWY-MU = 0.81 no snapshot -> -3% * 0.81 = -2.43%
    s = pm.sinal_overnight({"EWY": -3.0}, ["MU"])
    assert s[0]["impacto_esperado_pct"] == pytest.approx(-2.43, abs=0.01)
    assert s[0]["alerta"] == "FORTE"


def test_overnight_media_entre_proxies():
    s = pm.sinal_overnight({"EWY": -3.0, "EWT": -2.0}, ["MU"])
    # média dos componentes com correlação medida (EWT pode não estar no
    # snapshot antigo; o cálculo divide pelo que de fato entrou)
    assert len(s) == 1 and s[0]["impacto_esperado_pct"] < 0


def test_overnight_ignora_proxy_desconhecido():
    assert pm.sinal_overnight({"XYZ": -5.0}, ["MU"]) == []


def test_overnight_ignora_posicao_sem_correlacao_medida():
    assert pm.sinal_overnight({"EWY": -3.0}, ["XXXX"]) == []


def test_overnight_classifica_intensidade():
    assert pm.sinal_overnight({"EWY": -0.2}, ["MU"])[0]["alerta"] == "leve"
    assert pm.sinal_overnight({"EWY": -1.0}, ["MU"])[0]["alerta"] == "moderado"


def test_overnight_ordena_por_impacto_absoluto():
    s = pm.sinal_overnight({"EWY": -3.0}, ["MU", "SMCI", "NVDA"])
    impactos = [abs(x["impacto_esperado_pct"]) for x in s]
    assert impactos == sorted(impactos, reverse=True)


# ── qual proxy lidera cada posição (derivado do dado, não de texto fixo) ───

def test_melhor_proxy_ordena_por_correlacao():
    ranking = pm.melhor_proxy("MU")
    assert ranking, "MU deveria ter ao menos EWY medido no snapshot"
    corrs = [c for _, c in ranking]
    assert corrs == sorted(corrs, reverse=True)
    assert all(p in pm.INDICADORES_GLOBAIS for p, _ in ranking)


def test_melhor_proxy_de_ticker_sem_correlacao_e_vazio():
    assert pm.melhor_proxy("XXXX") == []


def test_lideres_por_posicao_reporta_margem_sobre_o_segundo(monkeypatch):
    # EWT lidera MU por 0.10 sobre EWY neste cenário.
    fake = {("EWT", "MU"): 0.70, ("EWY", "MU"): 0.60}
    monkeypatch.setattr(pm, "correlacao",
                        lambda a, b: fake.get((a.upper(), b.upper())))
    lid = pm.lideres_por_posicao(["MU"])[0]
    assert lid["proxy"] == "EWT"
    assert lid["correlacao"] == pytest.approx(0.70)
    assert lid["margem_sobre_2o"] == pytest.approx(0.10, abs=0.001)
    assert lid["indice"] == "^TWII"


def test_lideres_por_posicao_ignora_quem_nao_tem_medida(monkeypatch):
    monkeypatch.setattr(pm, "correlacao", lambda a, b: None)
    assert pm.lideres_por_posicao(["MU", "NVDA"]) == []


def test_composicao_descreve_sem_ranquear():
    """A descrição não pode voltar a afirmar qual proxy é o 'melhor' -- esse
    julgamento depende da carteira e sai do dado (ver melhor_proxy)."""
    for info in pm.INDICADORES_GLOBAIS.values():
        assert "composicao" in info
        assert "melhor indicador" not in info["composicao"].lower()


# ── ausência de dado precisa parecer ausência ─────────────────────────────

def test_beta_ausente_nao_vira_beta_alto_nem_zero(monkeypatch):
    """Beta desconhecido não pode ser tratado como 0 (que significaria 'não
    acompanha o setor', leitura oposta de 'não sabemos'). Sem o dado, o
    papel só não leva o multiplicador extra de dot plot."""
    monkeypatch.setitem(pv.TEMA_IA, "FAKE",
                        {"vol_sem": 20.0, "beta": None, "est": False,
                         "grupo": "chips", "driver": "teste"})
    p = pm.parametros_completos("FAKE", date(2026, 9, 15))  # véspera de FOMC c/ dot plot
    assert p["mult_macro"] == pytest.approx(pm.MULT_FOMC_SEP, abs=0.001)
    assert "beta alto" not in p["modo"]


def test_beta_alto_conhecido_continua_levando_o_extra(monkeypatch):
    monkeypatch.setitem(pv.TEMA_IA, "FAKE2",
                        {"vol_sem": 20.0, "beta": 3.0, "est": False,
                         "grupo": "chips", "driver": "teste"})
    p = pm.parametros_completos("FAKE2", date(2026, 9, 15))
    assert p["mult_macro"] == pytest.approx(pm.MULT_FOMC_SEP * pm.MULT_EXTRA_BETA_ALTO, abs=0.001)
