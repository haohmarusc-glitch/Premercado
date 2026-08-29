"""
Etapa 2 dos múltiplos locais: a ATIVAÇÃO.

A etapa 1 calculou sem publicar -- `fundamentos_sec` ficou desconectado de
propósito, e quatro rodadas de modo sombra contra arquivamentos reais
acharam nove defeitos antes que qualquer número chegasse a um relatório.
Aqui os números passam a sair pelo `get_fundamentals_valuation()`, e o que
este arquivo cobre é justamente o que a etapa 1 NÃO podia cobrir: a costura.

As armadilhas da costura são outras das da aritmética:

- **Escala.** `roe: 0.20` é 20%, e quem lê `roe_ttm: 0.2` escreve "0,2%".
  Errar por cem não estoura nada.
- **Atribuição.** Creditar à FMP número que veio da SEC é falso, e a linha
  de fontes é o que o leitor usa para medir a profundidade da análise.
- **Acoplamento.** DCF e múltiplos têm fontes independentes; se uma queda
  derrubar as duas metades, a troca de provedor não terá servido para nada.
- **Silêncio.** Métrica que não deu para calcular tem que sair COM MOTIVO.
  Um campo ausente e um campo com motivo são a mesma coisa para o payload
  e coisas opostas para quem lê.

Rodar: pytest artifacts/api-server/src/__tests__/test_valuation_ativado.py -v
"""
from unittest import mock

import pytest

from agent import config as agent_config
from agent import fundamentos_sec, tools

from test_fundamentos_sec import _emissor, _f, _inst, _quatro_trimestres


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    # `get_fundamentals_valuation` e `companyfacts` são @cached em disco: sem
    # isto, o primeiro teste grava o resultado mockado e os seguintes leem o
    # valor velho em vez de rodar com o mock próprio.
    monkeypatch.setattr(agent_config, "CACHE_ENABLED", False)


class _Resposta:
    def __init__(self, payload=None, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _montar(monkeypatch, *, facts=None, dcf=None, preco_yf=20.0, chave="k" * 20):
    """Liga o `get_fundamentals_valuation` a um emissor sintético.

    A SEC entra pelo `companyfacts` (o cálculo REAL roda, com as mesmas
    fixtures da etapa 1 -- é o caminho inteiro que está sob teste, não um
    dicionário escrito à mão que concordaria com qualquer bug). A FMP entra
    pelo SESSION. `dcf=None` deixa a FMP fora.
    """
    if chave is None:
        monkeypatch.delenv("FMP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FMP_API_KEY", chave)
    monkeypatch.setattr(tools, "_resolve_cik", lambda t: "0000000001")
    monkeypatch.setattr(fundamentos_sec, "companyfacts",
                        lambda cik: facts if facts is not None else _emissor())
    chamadas = []

    def _get(url, params=None, timeout=None):
        chamadas.append(url)
        if dcf is None:
            raise RuntimeError("a FMP não deveria ter sido chamada")
        return _Resposta(dcf)

    fast = type("FastInfo", (), {"last_price": preco_yf})()
    ctx_session = mock.patch.object(tools, "SESSION")
    ctx_yf = mock.patch.object(tools.yf, "Ticker",
                               return_value=type("T", (), {"fast_info": fast})())
    with ctx_session as sess, ctx_yf:
        sess.get.side_effect = _get
        return tools.get_fundamentals_valuation("NVDA"), chamadas


# ── As oito métricas chegam, com a escala certa ───────────────────────────────

def test_oito_metricas_com_os_nomes_do_painel(monkeypatch):
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert val["configured"] is True
    assert val["market_cap"] == 2000
    assert val["pe_ratio_ttm"] == 20.0
    assert val["pb_ratio"] == pytest.approx(3.3333, abs=1e-3)
    assert val["ev_to_ebitda_ttm"] == pytest.approx(15.0, abs=1e-4)
    assert val["net_debt_to_ebitda_ttm"] == pytest.approx(0.7143, abs=1e-3)
    # O emissor da fixture tem 4 trimestres; crescimento pede 8. Que ele seja
    # o ÚNICO ausente é a asserção: uma métrica indisponível não contamina as
    # outras, e a que falta diz quantos trimestres faltaram.
    assert set(val["multiplos_indisponiveis"]) == {"revenue_growth_pct_ttm"}
    assert "precisa de 8" in val["multiplos_indisponiveis"]["revenue_growth_pct_ttm"]


def test_percentuais_saem_em_percentual_e_nao_em_fracao(monkeypatch):
    """A armadilha da costura: `roe: 0.20` é 20%, e um campo chamado
    `roe_ttm` valendo 0.2 vira "ROE de 0,2%" na primeira leitura -- erro por
    cem que não estoura nada. O sufixo `_pct` e a escala andam juntos."""
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert val["roe_pct_ttm"] == 20.0
    assert val["net_margin_pct_ttm"] == 10.0
    assert val["fcf_yield_pct_ttm"] == 6.0
    # E o número em FRAÇÃO não pode estar publicado sob nenhum nome: seria
    # exatamente o valor que o leitor escreveria como percentual.
    assert 0.2 not in val.values()
    assert "roe_ttm" not in val, "o nome sem unidade era a própria armadilha"


def test_crescimento_de_receita_em_percentual(monkeypatch):
    """Oito trimestres: 200 x4 depois 250 x4 -> TTM 1000 contra 800 = +25%."""
    facts = _emissor()
    facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": (
        _quatro_trimestres([200, 200, 200, 200], ano=2024)
        + _quatro_trimestres([250, 250, 250, 250], ano=2025))}}
    val, _ = _montar(monkeypatch, facts=facts, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert val["revenue_growth_pct_ttm"] == 25.0


# ── Proveniência: a restrição que dá para conferir ────────────────────────────

def test_cada_multiplo_publicado_diz_de_onde_veio(monkeypatch):
    """Período, formulário, accession e tags em UMA linha, ao lado do número.

    A proveniência completa é aninhada e grande demais para um prompt. Se ela
    ficar só no JSON de depuração, conferir um múltiplo contra o 10-Q volta a
    exigir adivinhação -- e um número que não dá para conferir é
    indistinguível de um número errado.
    """
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    fontes = val["multiplos_fontes"]
    assert set(fontes) >= {"pe_ratio_ttm", "pb_ratio", "roe_pct_ttm"}
    linha = fontes["pe_ratio_ttm"]
    assert "10-Q" in linha or "10-K" in linha
    assert "accn" in linha and "arquivado em" in linha
    assert "período" in linha
    assert "tags" in linha
    assert val["multiplos_fonte"].startswith("SEC XBRL")


def test_fonte_curta_aponta_o_arquivamento_mais_recente():
    """Um TTM cruza quatro arquivamentos; é o ÚLTIMO que data o número."""
    metrica = {"valor": 1.0, "proveniencia": {
        "periodo": "2024-01-01..2024-12-31",
        "trimestres": [
            {"fim": "2024-03-31", "form": "10-Q", "accn": "a-1", "filed": "2024-04-20"},
            {"fim": "2024-12-31", "form": "10-K", "accn": "a-9", "filed": "2025-02-10"},
        ],
        "tags": ["Revenues"]}}
    linha = fundamentos_sec.fonte_curta(metrica)
    assert "a-9" in linha and "2025-02-10" in linha
    assert "a-1" not in linha


def test_fonte_curta_cala_sobre_metrica_sem_valor():
    assert fundamentos_sec.fonte_curta({"valor": None, "indisponivel": "x"}) is None


# ── As duas metades falham em separado ────────────────────────────────────────

def test_sem_chave_da_fmp_os_multiplos_ainda_saem(monkeypatch):
    """A mudança de semântica: `configured` era "a FMP tem chave". Hoje os
    múltiplos não pedem chave nenhuma, e devolver `configured=false` faria a
    tela descartar oito números que estavam prontos."""
    val, chamadas = _montar(monkeypatch, chave=None)
    assert val["configured"] is True
    assert val["pe_ratio_ttm"] == 20.0
    assert val["dcf_fair_value"] is None
    assert "FMP_API_KEY" in val["dcf_indisponivel"]
    assert chamadas == [], "sem chave, nem se tenta a FMP"
    assert "indisponivel" not in val, "veio múltiplo: o painel não está vazio"
    assert val["multiplos_fonte"].startswith("SEC XBRL")


def test_a_fmp_nao_e_mais_consultada_para_multiplos(monkeypatch):
    """O endpoint `key-metrics-ttm` respondia 402 (o plano da conta não o
    cobre). Continuar chamando gastaria cota para receber uma recusa."""
    _val, chamadas = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert len(chamadas) == 1, chamadas
    assert "discounted-cash-flow" in chamadas[0]
    assert not any("key-metrics" in u for u in chamadas)


def test_sec_fora_do_ar_nao_derruba_o_dcf(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "k" * 20)
    monkeypatch.setattr(tools, "_resolve_cik", lambda t: "0000000001")

    def _explode(cik):
        raise RuntimeError("data.sec.gov fora do ar")

    monkeypatch.setattr(fundamentos_sec, "companyfacts", _explode)
    with mock.patch.object(tools, "SESSION") as sess:
        sess.get.return_value = _Resposta([{"dcf": 220.0, "Stock Price": 200.0}])
        val = tools.get_fundamentals_valuation("NVDA")
    assert val["dcf_fair_value"] == 220.0
    assert val["dcf_implied_upside_pct"] == 10.0
    assert "pe_ratio_ttm" not in val
    assert "fora do ar" in val["multiplos_indisponiveis"]["pe_ratio_ttm"]
    assert "indisponivel" not in val, "o DCF veio; o painel não está vazio"


def test_emissor_estrangeiro_sai_com_o_motivo_e_sem_numero(monkeypatch):
    """IFRS tem outro conjunto de tags. Adaptar em silêncio produziria número
    calculado sobre conceito diferente -- o caso de BABA e SKHY."""
    ifrs = {"facts": {"ifrs-full": {"Revenue": {"units": {"USD": [
        _f("2025-01-01", "2025-03-31", 10, form="20-F")]}}}}}
    val, _ = _montar(monkeypatch, facts=ifrs, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert "pe_ratio_ttm" not in val
    motivos = set(val["multiplos_indisponiveis"].values())
    assert len(motivos) == 1
    assert "us-gaap" in motivos.pop().lower() or "ifrs" in str(motivos).lower()
    assert val["dcf_fair_value"] == 22.0


def test_nada_de_nada_sai_com_motivo_unico(monkeypatch):
    """Nem DCF nem um múltiplo. Quem chama precisa do MOTIVO, não de um
    dicionário vazio: "a FMP não tem cobertura" era a única explicação que a
    tela conseguia dar, e hoje estaria errada em quase todo caso."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(tools, "_resolve_cik", lambda t: None)
    val = tools.get_fundamentals_valuation("NVDA")
    assert val["configured"] is True
    assert "CIK desconhecido" in val["indisponivel"]
    assert "FMP_API_KEY" in val["indisponivel"]


# ── Um preço só ───────────────────────────────────────────────────────────────

def test_o_preco_do_dcf_e_o_mesmo_que_calcula_os_multiplos(monkeypatch):
    """Preço divergente entre painéis já custou uma análise inteira neste
    repo. Se o painel mostrasse 20 e o P/L usasse 25, refazer a conta seria
    impossível -- e nada no texto denunciaria."""
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}],
                     preco_yf=999.0)
    assert val["current_price"] == 20.0
    # capitalização = preço x 100 ações; P/L = cap / 100 de lucro TTM.
    assert val["market_cap"] == 2000
    assert val["pe_ratio_ttm"] == 20.0


def test_sem_preco_da_fmp_o_yfinance_serve_as_duas_metades(monkeypatch):
    val, _ = _montar(monkeypatch, dcf=[{"equityValuePerShare": 22.0}], preco_yf=20.0)
    assert val["current_price"] == 20.0
    assert val["dcf_fair_value"] == 22.0
    assert val["pe_ratio_ttm"] == 20.0


# ── O motivo da indisponibilidade sobrevive inteiro ───────────────────────────

def test_metrica_sem_d_e_a_chega_ao_painel_com_o_motivo(monkeypatch):
    """O caso MRVL: a depreciação só sai no 10-K, e metade do D&A não tem
    base trimestral. `indisponível` com motivo é a resposta certa -- somar
    depreciação anual a amortização TTM compararia janelas diferentes."""
    facts = _emissor()
    del facts["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    val, _ = _montar(monkeypatch, facts=facts, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert "ev_to_ebitda_ttm" not in val
    assert "net_debt_to_ebitda_ttm" not in val
    motivo = val["multiplos_indisponiveis"]["ev_to_ebitda_ttm"]
    assert "D&A" in motivo
    # As outras seguem valendo: uma métrica indisponível não é um painel vazio.
    assert val["pe_ratio_ttm"] == 20.0
    assert val["roe_pct_ttm"] == 20.0


def test_a_chave_da_fmp_nao_vaza_pelo_campo_novo(monkeypatch):
    """`dcf_indisponivel` viaja DENTRO do payload do modelo -- um caminho que
    o `_faltou` do analise_rapida_ia não cobre. Mascarar só na ponta foi o que
    deixou a chave vazar duas vezes; este campo mascara na origem."""
    chave = "MINHACHAVESECRETA1234"
    monkeypatch.setenv("FMP_API_KEY", chave)
    monkeypatch.setattr(tools, "_resolve_cik", lambda t: "0000000001")
    monkeypatch.setattr(fundamentos_sec, "companyfacts", lambda cik: _emissor())

    class _Erro(Exception):
        pass

    with mock.patch.object(tools, "SESSION") as sess, \
         mock.patch.object(tools.yf, "Ticker",
                           return_value=type("T", (), {
                               "fast_info": type("F", (), {"last_price": 20.0})()})()):
        sess.get.side_effect = _Erro(
            f"402 Client Error for url: https://financialmodelingprep.com/"
            f"stable/discounted-cash-flow?symbol=NVDA&apikey={chave}")
        val = tools.get_fundamentals_valuation("NVDA")
    assert chave not in val["dcf_indisponivel"]
    assert "MASKED" in val["dcf_indisponivel"]


def test_metrica_ttm_nao_anuncia_um_instante_como_periodo(monkeypatch):
    """ROE e dívida líquida/EBITDA misturam estoque com fluxo por definição: o
    denominador é um instante do balanço, o numerador é um TTM. A proveniência
    trazia só a data do balanço, e `fonte_curta` acabava anunciando esse
    instante como "período" da métrica -- meia verdade que a proveniência
    existe justamente para não deixar passar."""
    facts = _emissor()
    facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": (
        _quatro_trimestres([200] * 4, ano=2024)
        + _quatro_trimestres([250] * 4, ano=2025))}}
    val, _ = _montar(monkeypatch, facts=facts, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    for nome in ("roe_pct_ttm", "net_debt_to_ebitda_ttm"):
        linha = val["multiplos_fontes"][nome]
        assert ".." in linha, f"{nome} sem janela: {linha}"
        assert "período 2025-" in linha, f"{nome} datado pelo balanço: {linha}"


def test_a_capitalizacao_tambem_diz_de_onde_veio(monkeypatch):
    """Preço de hoje x ações da CAPA do arquivamento. Qual arquivamento foi
    esse é o que separa "ações em circulação" da média ponderada diluída --
    usar a segunda dataria a capitalização no passado."""
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert "accn" in val["multiplos_fontes"]["market_cap"]


def test_toda_metrica_publicada_cita_um_arquivamento(monkeypatch):
    """A restrição não admite exceção: período, formulário, accession e data.

    O crescimento de receita era a única das oito que saía sem nenhum deles --
    a proveniência dele só tinha janelas e tags, e a linha ficava conferível
    só de palavra. Este teste varre TODAS, para a próxima métrica nova não
    repetir a mesma omissão sem que alguém note.
    """
    facts = _emissor()
    facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": (
        _quatro_trimestres([200] * 4, ano=2024)
        + _quatro_trimestres([250] * 4, ano=2025))}}
    val, _ = _montar(monkeypatch, facts=facts, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    publicados = [n for n, _, _ in tools._MULTIPLOS_DA_SEC if n in val]
    assert len(publicados) == 8, publicados
    for nome in publicados + ["market_cap"]:
        linha = val["multiplos_fontes"][nome]
        assert "accn" in linha and "arquivado em" in linha, f"{nome}: {linha}"


def test_instantaneo_nao_e_chamado_de_periodo(monkeypatch):
    """P/VP usa o balanço de UMA data por definição (patrimônio é estoque, não
    se soma). Chamar essa data de "período" contradiria, na mesma linha, o que
    a métrica faz."""
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert "posição em 2025-12-31" in val["multiplos_fontes"]["pb_ratio"]
    assert "período" not in val["multiplos_fontes"]["pb_ratio"]
    # E o contrário segue valendo: TTM continua sendo período.
    assert "período 2025-01-01..2025-12-31" in val["multiplos_fontes"]["pe_ratio_ttm"]


def test_o_pvp_nao_se_chama_ttm(monkeypatch):
    """Patrimônio é ESTOQUE -- vem do balanço mais recente, sem soma de
    trimestre nenhuma. O cálculo sempre esteve certo (armadilha 4), mas o campo
    se chamava `pb_ratio_ttm` e saiu na tela do MRVL como "P/B TTM de 10,25",
    contando ao leitor uma coisa que o número não é.

    A proveniência já denunciava a contradição sozinha: dizia "posição em
    <data>", não "período <a..b>", ao lado de um nome que prometia TTM."""
    val, _ = _montar(monkeypatch, dcf=[{"dcf": 22.0, "Stock Price": 20.0}])
    assert "pb_ratio" in val
    assert "pb_ratio_ttm" not in val
    assert "posição em" in val["multiplos_fontes"]["pb_ratio"]
