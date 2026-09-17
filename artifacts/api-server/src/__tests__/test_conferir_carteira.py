"""Conferidor de carteira: extrato da corretora x app.

O caso que fundou o script (17/09/2026). Oito posições na corretora, e o
app conhecia sete delas — uma com o tamanho certo:

    SNDK   US$ 238 na corretora, INEXISTENTE no app
    MRVL   US$ 200 no app, US$ 500 na corretora  (compras não lançadas)
    ARM    US$ 700 no app, US$ 600 na corretora  (venda não baixada)

Treze por cento da carteira fora do radar do agente, sem nenhum sintoma na
tela. Nada no sistema detectava isso: uma compra feita na corretora e não
lançada é invisível para sempre, e o app não tem como saber que não sabe.

Os números aqui são os REAIS daquele dia -- print da corretora de um lado,
saída do psql do outro. Fixture inventada não teria produzido o empate de
cinco posições ao centavo que é o que valida a leitura do extrato.
"""

import io

import pytest

from agent.scripts import conferir_carteira as cc

# Print da corretora, 17/09/2026.
_EXTRATO_REAL = """
NVDA  1.434,52  +66,86
ARM     521,52  -78,47
MRVL    496,18   -3,76
SNDK    238,10   -0,02
INTC    210,69  +10,69
ADI     191,23   -8,18
AVGO     94,51   -5,48
BABA     86,16  -13,83
"""

# Saída do psql do mesmo dia: (investido_aberto, abertos, vendidos, etf)
_BANCO_REAL = {
    "NVDA": (1369.00, 6, 0, False), "ARM": (700.00, 2, 1, False),
    "INTC": (200.00, 1, 1, False),  "MRVL": (200.00, 1, 0, False),
    "ADI":  (200.00, 1, 0, False),  "BABA": (100.00, 1, 0, False),
    "AVGO": (100.00, 1, 1, False),  "SGOV": (0.0,    1, 0, True),
}


def _api_de(banco: dict):
    ids = {i: t for i, t in enumerate(banco, 1)}

    def api(caminho: str):
        if caminho == "/portfolio":
            return [{"id": i, "ticker": t, "isEtf": banco[t][3],
                     "investedAmount": banco[t][0]} for i, t in ids.items()]
        val, abertos, vendidos, _ = banco[ids[int(caminho.split("/")[2])]]
        lotes = [{"amount": val / abertos, "saleDate": None, "salePrice": None}
                 for _ in range(abertos)]
        lotes += [{"amount": 100.0, "saleDate": "2026-08-01", "salePrice": "110"}
                  for _ in range(vendidos)]
        return lotes
    return api


@pytest.fixture
def caso_real():
    extrato, _ = cc.ler_extrato(io.StringIO(_EXTRATO_REAL))
    app = cc.ler_app(api=_api_de(_BANCO_REAL))
    return cc.conferir(extrato, app), extrato, app


def test_acha_a_posicao_que_o_app_nao_tem(caso_real):
    """A pior das três: o agente não analisa, não alerta, não cria plano de
    saída, e nada na tela sugere que falta algo."""
    achados, _, _ = caso_real
    assert [t for t, _ in achados["so_na_corretora"]] == ["SNDK"]


def test_separa_compra_nao_lancada_de_venda_nao_baixada(caso_real):
    """O sinal da diferença diz o que fazer, e são coisas opostas: lançar
    uma compra ou dar baixa numa venda."""
    achados, _, _ = caso_real
    por_ticker = {t: (e, a) for t, e, a in achados["valor_diferente"]}
    assert set(por_ticker) == {"ARM", "MRVL"}

    e, a = por_ticker["MRVL"]
    assert e["custo"] - a["lotes"] == pytest.approx(299.94, abs=0.01), "compra faltando"
    e, a = por_ticker["ARM"]
    assert e["custo"] - a["lotes"] == pytest.approx(-100.01, abs=0.01), "venda faltando"


def test_arredondamento_e_taxa_nao_viram_divergencia(caso_real):
    """NVDA saiu -1,34 sobre 1.369 (seis lotes) e ADI -0,59 sobre 200. Se a
    tolerância não cobrisse isso, o relatório apontaria cinco problemas por
    rodada e ninguém leria o sexto, que é real."""
    achados, _, _ = caso_real
    assert {t for t, _, _ in achados["ok"]} == {"NVDA", "INTC", "ADI", "AVGO", "BABA"}


def test_etf_nao_vira_falso_positivo(caso_real):
    """SGOV está no app e não no extrato, mas é ETF de caixa -- fora da
    análise do agente por decisão, não por esquecimento."""
    achados, _, _ = caso_real
    assert "SGOV" not in [t for t, _ in achados["so_no_app"]]


def test_o_total_bate_com_a_conta_manual(caso_real):
    _, extrato, app = caso_real
    assert sum(v["custo"] for v in extrato.values()) == pytest.approx(3305.10, abs=0.01)
    assert sum(v["lotes"] for v in app.values() if not v["etf"]) == pytest.approx(2869.00, abs=0.01)


# ── o que impede o conferidor de mentir ──────────────────────────────────

def test_sem_sinal_nao_inventa_custo():
    """Na tela da corretora o sinal do resultado é a COR, e ela não sobrevive
    ao copiar. Tratar "66,86" como positivo daria custo 1367,66; como
    negativo, 1501,38. Adivinhar erraria metade das linhas, então a linha
    entra só como presença e o relatório diz isso."""
    extrato, _ = cc.ler_extrato(io.StringIO("NVDA 1434.52 66.86\nARM 521.52 -78.47\n"))
    assert extrato["NVDA"]["custo"] is None
    assert extrato["ARM"]["custo"] == pytest.approx(599.99, abs=0.01)


@pytest.mark.parametrize("txt,esperado", [
    ("1.434,52", 1434.52),      # pt-BR
    ("1,434.52", 1434.52),      # en-US
    ("1434.52", 1434.52),
    ("US$ 1.434,52", 1434.52),
    ("-78,47", -78.47),
    ("86,16", 86.16),
])
def test_formatos_de_numero(txt, esperado):
    assert cc._numero(txt) == pytest.approx(esperado)


def test_lixo_da_tela_e_ignorado_e_relatado():
    """Copiar da tela traz cabeçalho e barra de status junto. Ignorar é
    certo; ignorar em SILÊNCIO não é -- uma posição mal formatada sumiria
    da conferência sem deixar rastro."""
    extrato, ignoradas = cc.ler_extrato(io.StringIO(
        "Mercado aberto\nNVDA 1434.52 +66.86\n13:23 TEMU\nAjuda\n"))
    assert list(extrato) == ["NVDA"]
    assert len(ignoradas) == 3


def test_posicao_totalmente_vendida_nao_conta_como_aberta():
    """`GET /portfolio` devolve TAMBÉM as encerradas -- o frontend precisa
    delas para a seção "Ações Vendidas". Quem decide o que está aberto são
    os lotes; pela lista, toda ação já vendida viraria "só no app"."""
    app = cc.ler_app(api=_api_de({"MU": (200.0, 0, 1, False)}))
    assert app == {}


def test_campo_guardado_fora_de_sincronia_e_achado_proprio():
    """`investedAmount` deveria ser a soma dos lotes abertos, mas
    `PUT /portfolio/:id` edita campo direto -- é a armadilha do §1 do
    playbook, a mesma que deixou a MU aparecendo ativa depois de vendida.
    Independe da corretora: um lado do app discordando do outro."""
    def api(caminho):
        if caminho == "/portfolio":
            return [{"id": 1, "ticker": "NVDA", "isEtf": False,
                     "investedAmount": 1500.00}]     # guardado
        return [{"amount": 500.0, "saleDate": None, "salePrice": None}] * 2  # lotes = 1000
    achados = cc.conferir(cc.ler_extrato(io.StringIO("NVDA 1050 +50\n"))[0],
                          cc.ler_app(api=api))
    assert [(t, a["guardado"], a["lotes"]) for t, a in achados["campo_dessincronizado"]] \
        == [("NVDA", 1500.0, 1000.0)]


def test_mesma_acao_em_duas_posicoes_soma():
    """Nada impede duas LINHAS de posição com o mesmo ticker (compra nova
    cadastrada como posição separada, em vez de lote da existente). A
    corretora mostra a consolidada, então somar é o único jeito de as duas
    pontas falarem da mesma coisa -- sem isso, uma carteira legítima de 500
    apareceria como divergência de 300."""
    def api(caminho):
        if caminho == "/portfolio":
            return [
                {"id": 1, "ticker": "NVDA", "isEtf": False, "investedAmount": 300.0},
                {"id": 2, "ticker": "NVDA", "isEtf": False, "investedAmount": 200.0},
            ]
        valor = {1: 300.0, 2: 200.0}[int(caminho.split("/")[2])]
        return [{"amount": valor, "saleDate": None, "salePrice": None}]

    app = cc.ler_app(api=api)
    assert list(app) == ["NVDA"], "as duas linhas têm de virar uma entrada"
    assert app["NVDA"]["lotes"] == pytest.approx(500.0)
    assert app["NVDA"]["abertos"] == 2

    # E a conferência contra a corretora fecha, em vez de acusar diferença.
    achados = cc.conferir(cc.ler_extrato(io.StringIO("NVDA 520 +20\n"))[0], app)
    assert not achados["valor_diferente"]
