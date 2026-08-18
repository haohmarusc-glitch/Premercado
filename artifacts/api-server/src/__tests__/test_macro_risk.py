"""
Sinais macro de risco setorial — e a diferença entre "sem risco" e "sem dado".

Dois episódios reais viraram golden dataset aqui. Eles existem porque threshold
sem caso concreto atrás vira número que ninguém sabe revisar:

  28-29/07/2026  contágio setorial. Fed mantém juros com PCE a 3,7%, 30Y cruza
                 5,2%, SK Hynix -14,65% com circuit breaker no Kospi, e o setor
                 chega nisso depois de +71% em 9 semanas.
  18/08/2026     choque geopolítico. WTI a US$ 84 por tensão no Estreito de
                 Hormuz, 10Y a 4,72%, SEM contágio asiático e SEM setor
                 esticado. Padrão diferente, resposta diferente.

## O que este arquivo protege acima de tudo

A versão original do módulo devolvia score 0 tanto para "medi e está calmo"
quanto para "não consegui medir nada". Num módulo que dimensiona posição, isso
inverte o sentido da falha: a coleta quebrada vira permissão para posição
cheia.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_macro_risk.py -v
"""
import pytest

from agent import macro_risk as mr
from agent.macro_risk import MacroRiskModule, apply_macro_risk_modifier


# ── golden datasets ─────────────────────────────────────────────────────────

# SOX subindo 71% em 9 semanas (45 pregões), o ritmo de abr-mai/2026.
_SOX_ESTICADO = [100.0] + [100.0 * (1.71 ** (i / 45)) for i in range(1, 46)]
_SOX_PARADO = [100.0] * 46

GOLDEN_29_07 = dict(
    yield_30y_today=5.21, yield_30y_prev=5.14, near_fomc_window=True,
    sk_hynix_pct=-14.65, samsung_pct=-13.0, kospi_pct=-8.0,
    eps_surprise_pct=5.0, revenue_surprise_pct=3.0, premarket_reaction_pct=-9.0,
    manchetes=[
        {"title": "China chip equipment mass production report", "overall_sentiment_score": -0.4},
        {"title": "CXMT blockbuster IPO Shanghai", "overall_sentiment_score": -0.2},
        {"title": "China DUV lithography development", "overall_sentiment_score": -0.3},
        {"title": "Export restriction concerns weigh on chips", "overall_sentiment_score": -0.25},
    ],
    sox_precos=_SOX_ESTICADO,
    wti_hoje=70.0, wti_anterior=69.9, yield_10y_hoje=4.60, yield_10y_anterior=4.58,
)

GOLDEN_30_07_REPIQUE = dict(
    yield_30y_today=5.15, yield_30y_prev=5.21, near_fomc_window=False,
    sk_hynix_pct=1.0, samsung_pct=0.5, kospi_pct=0.8,
    eps_surprise_pct=2.0, revenue_surprise_pct=1.5, premarket_reaction_pct=2.0,
    manchetes=[], sox_precos=_SOX_PARADO,
    wti_hoje=84.0, wti_anterior=84.0, yield_10y_hoje=4.60, yield_10y_anterior=4.60,
)

GOLDEN_18_08_PETROLEO = dict(
    yield_30y_today=5.31, yield_30y_prev=5.19, near_fomc_window=False,
    sk_hynix_pct=0.0, samsung_pct=0.0, kospi_pct=0.0,
    eps_surprise_pct=None, revenue_surprise_pct=None, premarket_reaction_pct=None,
    manchetes=[], sox_precos=_SOX_PARADO,
    wti_hoje=84.15, wti_anterior=79.5, yield_10y_hoje=4.72, yield_10y_anterior=4.60,
)


@pytest.fixture
def modulo():
    return MacroRiskModule()


def _ativos(r: dict) -> set[str]:
    return {n for n in mr.PESOS if r[n]["active"]}


# ── os dois episódios são reconhecidos, e são DIFERENTES ────────────────────

def test_29_07_dispara_o_conjunto_de_contagio(modulo):
    r = modulo.evaluate(**GOLDEN_29_07)
    assert _ativos(r) == {
        "RATE_SHOCK", "ASIA_MEMORY_CONTAGION", "PRICED_FOR_PERFECTION",
        "CHINA_COMPETITION_RISK", "OVEREXTENDED_SECTOR",
    }
    assert r["cobertura_pct"] == 100
    assert r["aggregate_score"] >= 70


def test_30_07_o_repique_nao_dispara_nada(modulo):
    """O dia seguinte: yield cede, Ásia no azul, setor sem extensão. Um módulo
    que não sabe desligar não serve para modular nada."""
    r = modulo.evaluate(**GOLDEN_30_07_REPIQUE)
    assert _ativos(r) == set()
    assert r["aggregate_score"] == 0
    assert r["cobertura_pct"] == 100


def test_18_08_e_um_padrao_DIFERENTE_de_29_07(modulo):
    """O teste que justifica o 6º sinal existir. Os dois episódios são quedas
    de IA/semis, mas um é setorial (Ásia, valuation) e o outro é macro
    (petróleo, geopolítica). Tratá-los igual levaria a cortar posição do mesmo
    jeito em situações que pedem reações opostas."""
    julho = modulo.evaluate(**GOLDEN_29_07)
    agosto = modulo.evaluate(**GOLDEN_18_08_PETROLEO)

    assert "GEOPOLITICAL_OIL_SHOCK" in _ativos(agosto)
    assert "ASIA_MEMORY_CONTAGION" not in _ativos(agosto)
    assert "OVEREXTENDED_SECTOR" not in _ativos(agosto)
    # Risco real, mas bem menor que o de julho -- e é essa distância que vira
    # tamanho de posição.
    assert agosto["aggregate_score"] < julho["aggregate_score"]


def test_sem_balanco_na_janela_nao_e_buraco_de_dado(modulo):
    """18/08 não teve earnings. Isso é resposta COMPLETA sobre o sinal, não
    falta de dado -- senão todo dia comum do calendário puniria o Kelly."""
    r = modulo.evaluate(**GOLDEN_18_08_PETROLEO)
    assert r["PRICED_FOR_PERFECTION"]["status"] == mr.NAO_APLICAVEL
    assert "PRICED_FOR_PERFECTION" not in r["fontesDegradadas"]
    assert r["cobertura_pct"] == 100


# ── cegueira ────────────────────────────────────────────────────────────────

def test_calmo_e_cego_NAO_produzem_a_mesma_saida(modulo):
    """O bug original em um teste. Medido:

        mercado calmo, tudo medido : score=0  kelly=0.2500
        NADA foi coletado          : score=0  kelly=0.2500

    Duas situações opostas, saída idêntica -- e a errada era a segunda, porque
    autorizava posição cheia num dia sobre o qual o sistema não sabia nada."""
    calmo = modulo.evaluate(**GOLDEN_30_07_REPIQUE)
    cego = modulo.evaluate()

    assert calmo["aggregate_score"] == 0
    assert cego["aggregate_score"] is None
    assert cego["cobertura_pct"] < calmo["cobertura_pct"]
    assert apply_macro_risk_modifier(0.25, cego) < apply_macro_risk_modifier(0.25, calmo)


def test_cegueira_reduz_a_posicao(modulo):
    cego = modulo.evaluate()
    assert apply_macro_risk_modifier(0.25, cego) < 0.25


def test_sinal_cego_nunca_dispara_alarme(modulo):
    """Sem dado não pode virar flag ativo: alarme falso gasta a credibilidade
    do painel tão rápido quanto silêncio falso."""
    cego = modulo.evaluate()
    assert _ativos(cego) == set()


def test_toda_fonte_que_falhou_e_nomeada(modulo):
    """Degradação anunciada, não silenciosa -- mesma regra do Radar. Sem o
    motivo, a tela mostra um traço e o operador não sabe o que consertar."""
    cego = modulo.evaluate()
    assert set(cego["fontesDegradadas"]) == {
        "RATE_SHOCK", "ASIA_MEMORY_CONTAGION", "CHINA_COMPETITION_RISK",
        "OVEREXTENDED_SECTOR", "GEOPOLITICAL_OIL_SHOCK",
    }
    assert all(m.strip() for m in cego["fontesDegradadas"].values())


def test_score_exige_cobertura_minima(modulo):
    """Cobertura baixa não gera score. Um 0 apurado sobre 35% do peso tem a
    mesma cara de um 0 apurado sobre 100% -- e diz coisa muito diferente."""
    parcial = modulo.evaluate(sk_hynix_pct=-14.65, samsung_pct=-13.0, kospi_pct=-8.0)
    assert parcial["cobertura_pct"] < mr.COBERTURA_MINIMA_PCT
    assert parcial["aggregate_score"] is None
    # mas o flag medido continua visível: cobertura baixa não apaga o que foi visto
    assert parcial["ASIA_MEMORY_CONTAGION"]["active"] is True


def test_asia_avalia_com_o_que_houver(modulo):
    """Kospi sem as ações ainda é leitura boa. Exigir as três apagaria um sinal
    forte por falta de fonte secundária."""
    r = modulo.evaluate(kospi_pct=-8.0)
    assert r["ASIA_MEMORY_CONTAGION"]["active"] is True
    assert r["ASIA_MEMORY_CONTAGION"]["status"] == mr.OK
    assert "parcial" in r["ASIA_MEMORY_CONTAGION"]["motivo"]


def test_serie_curta_do_SOX_e_sem_dado_e_nao_calmo():
    """A versão original devolvia inativo com uma nota em `details`, e nota não
    chega ao Kelly: histórico truncado virava permissão para posição cheia."""
    curto = mr.check_overextended([100.0] * 10)
    assert curto.status == mr.SEM_DADO
    assert curto.active is False


def test_lista_de_manchetes_vazia_e_medicao_de_verdade():
    """`[]` é "nada saiu hoje"; `None` é "a busca falhou". Confundir os dois
    transforma um feed quebrado em ausência de risco."""
    assert mr.check_china_risk([]).status == mr.OK
    assert mr.check_china_risk(None).status == mr.SEM_DADO


# ── o modulador de Kelly ────────────────────────────────────────────────────

def test_dia_limpo_nao_mexe_no_kelly(modulo):
    limpo = modulo.evaluate(**GOLDEN_30_07_REPIQUE)
    assert apply_macro_risk_modifier(0.25, limpo) == 0.25


def test_julho_corta_posicao_com_forca(modulo):
    julho = modulo.evaluate(**GOLDEN_29_07)
    assert apply_macro_risk_modifier(0.25, julho) < 0.10


def test_setor_esticado_amplifica_os_outros(modulo):
    """O multiplicador é a ideia central do 5º sinal: o mesmo gatilho custa mais
    caro quando todo mundo já está comprado no mesmo trade."""
    com = modulo.evaluate(**GOLDEN_29_07)
    sem = modulo.evaluate(**{**GOLDEN_29_07, "sox_precos": _SOX_PARADO})

    assert com["OVEREXTENDED_SECTOR"]["active"] is True
    assert sem["OVEREXTENDED_SECTOR"]["active"] is False
    assert apply_macro_risk_modifier(0.25, com) < apply_macro_risk_modifier(0.25, sem)


def test_o_modulador_nunca_devolve_negativo_nem_aumenta(modulo):
    """Modulador que aumenta posição deixa de ser proteção. Fixa a direção."""
    for dados in (GOLDEN_29_07, GOLDEN_30_07_REPIQUE, GOLDEN_18_08_PETROLEO, {}):
        saida = apply_macro_risk_modifier(0.25, modulo.evaluate(**dados))
        assert 0 <= saida <= 0.25


def test_payload_desconhecido_nao_explode():
    """O modulador roda no confluence_engine, que não pode cair porque o macro
    veio malformado."""
    assert apply_macro_risk_modifier(0.25, {}) == 0.25
    assert apply_macro_risk_modifier(0.25, {"RATE_SHOCK": None}) == 0.25


# ── higiene ─────────────────────────────────────────────────────────────────

def test_os_pesos_somam_100():
    """A cobertura é lida como porcentagem na tela. Se a soma mudar, ela vira
    um número sem unidade."""
    assert sum(mr.PESOS.values()) == 100


def test_todo_sinal_avaliado_tem_peso(modulo):
    r = modulo.evaluate()
    avaliados = {k for k, v in r.items() if isinstance(v, dict) and "flag" in v}
    assert avaliados == set(mr.PESOS)


# ── o sinal de earnings, com a fonte que existe ─────────────────────────────
#
# A fonte disponível (earnings_dates do yfinance) publica surpresa de EPS e não
# de receita -- a de receita viria da FMP, que devolve 402 nesta conta desde
# 18/08/2026. O check original exigia as duas.

def test_receita_ausente_nao_bloqueia_o_sinal():
    """Exigir a receita deixaria o sinal em sem_dado PERMANENTE, que é pior que
    ausente: puniria o Kelly todo dia sem nunca poder disparar. O essencial do
    padrão é "número bom, ação caiu", e o EPS sozinho já o expressa."""
    r = mr.check_priced_for_perfection(5.0, None, -9.0)
    assert r.status == mr.OK
    assert r.active is True


def test_receita_presente_endurece_o_criterio():
    """Quando ela existe, é usada: bater no EPS e furar na receita não é o
    padrão que o sinal descreve."""
    assert mr.check_priced_for_perfection(5.0, -3.0, -9.0).active is False
    assert mr.check_priced_for_perfection(5.0, 3.0, -9.0).active is True


def test_sem_balanco_continua_nao_aplicavel():
    r = mr.check_priced_for_perfection(None, None, None)
    assert r.status == mr.NAO_APLICAVEL


def test_balanco_sem_reacao_e_sem_dado():
    """Surpresa sem a reação de preço não diz nada: o sinal é sobre a DISTÂNCIA
    entre o número e o que o mercado fez com ele."""
    r = mr.check_priced_for_perfection(5.0, None, None)
    assert r.status == mr.SEM_DADO
    assert "reação" in r.motivo


def test_bateu_e_subiu_nao_dispara():
    assert mr.check_priced_for_perfection(5.0, None, 2.0).active is False


def test_errou_e_caiu_nao_e_este_sinal():
    """Queda depois de resultado RUIM é reação normal -- o sinal existe para o
    caso contrário, que é o que revela expectativa esticada."""
    assert mr.check_priced_for_perfection(-4.0, None, -9.0).active is False
