"""
Fôlego de caixa -- quantos trimestres a empresa aguenta queimando o que queima.

Quatro armadilhas dominam esta suíte, porque são as que produzem número certo
com leitura errada:

1. LOOK-AHEAD. O balanço do trimestre encerrado em 30/06 só existe para quem
   olha de fora quando a empresa reporta, semanas depois.
2. FÔLEGO DE QUEM NÃO QUEIMA. Caixa dividido por queima quase nula devolve
   "800 trimestres", que parece solidez e é só divisão por quase nada.
3. UM TRIMESTRE NÃO É TENDÊNCIA. Um pagamento concentrado viraria pânico.
4. REESTRUTURAÇÃO QUEBRA A SÉRIE. Comparar através de um evento desses mede
   contabilidade, não operação -- o caso WOLF que motivou o módulo.

Sem rede: a cascata yfinance -> Alpha Vantage é injetada.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_folego_de_caixa.py -v
"""
import pytest

from agent import folego_de_caixa as fc

_FINS = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
_HOJE = "2026-08-25"


def _bal(fim, caixa=1e9, divida=3e9, ac=2e9, pc=1e9, acoes=150e6):
    return {"trimestre": fc.trimestre_calendario(fim), "fimFiscal": fim,
            "disponivelEm": fc.disponivel_em(fim), "caixa": caixa,
            "divida": divida, "ativoCirculante": ac,
            "passivoCirculante": pc, "acoesEmCirculacao": acoes,
            "fonte": "yfinance"}


def _flu(fim, ocf=-50e6, capex=120e6):
    return {"trimestre": fc.trimestre_calendario(fim), "fimFiscal": fim,
            "disponivelEm": fc.disponivel_em(fim), "caixaOperacional": ocf,
            "capex": capex, "fonte": "yfinance"}


def _serie(caixas=None, ocfs=None, dividas=None, acoes=None):
    caixas = caixas or [1.4e9, 1.2e9, 1.0e9, 0.85e9, 0.7e9]
    ocfs = ocfs or [-50e6] * 5
    dividas = dividas or [3e9] * 5
    acoes = acoes or [150e6] * 5
    b = [_bal(f, caixa=c, divida=d, acoes=a)
         for f, c, d, a in zip(_FINS, caixas, dividas, acoes)]
    fl = [_flu(f, ocf=o) for f, o in zip(_FINS, ocfs)]
    return fc.montar_serie(b, fl, hoje=_HOJE)


# ── look-ahead ───────────────────────────────────────────────────────────────

def test_disponivel_em_fica_depois_do_fim_do_trimestre():
    assert fc.disponivel_em("2026-06-30") == "2026-08-14"  # +45 dias


def test_trimestre_fechado_mas_nao_divulgado_nao_entra():
    """O balanço de 30/06 existe no yfinance antes de a empresa reportar.
    Usá-lo em 01/07 é o mesmo vício que o backtest carregou até 20/08/2026."""
    serie = fc.montar_serie([_bal("2026-03-31"), _bal("2026-06-30")],
                            [_flu("2026-03-31"), _flu("2026-06-30")],
                            hoje="2026-07-01")
    assert [l["trimestre"] for l in serie] == ["2026Q1"]
    serie = fc.montar_serie([_bal("2026-03-31"), _bal("2026-06-30")],
                            [_flu("2026-03-31"), _flu("2026-06-30")],
                            hoje="2026-08-20")
    assert [l["trimestre"] for l in serie] == ["2026Q1", "2026Q2"]


# ── fôlego só existe para quem queima ────────────────────────────────────────

def test_empresa_queimando_ganha_folego_em_trimestres():
    r = fc.avaliar(_serie())
    # OCF -50M e capex 120M -> FCF -170M por trimestre; caixa 700M.
    assert r["queimaMedia"] == pytest.approx(170e6)
    assert r["folegoTrimestres"] == pytest.approx(4.1, abs=0.1)
    assert r["geraCaixa"] is False


def test_quem_gera_caixa_nao_recebe_numero_de_folego():
    """Dividir caixa por queima quase nula devolve um número enorme que
    parece solidez e é só divisão por quase nada. A AUSÊNCIA é a informação."""
    r = fc.avaliar(_serie(ocfs=[900e6] * 5))
    assert r["folegoTrimestres"] is None
    assert r["geraCaixa"] is True


def test_queima_abaixo_do_piso_nao_vira_folego_gigante():
    assert fc.folego_trimestres(1e9, fc.QUEIMA_MINIMA - 1) is None
    assert fc.folego_trimestres(1e9, 100e6) == 10.0


def test_sem_caixa_conhecido_nao_inventa_folego():
    assert fc.folego_trimestres(None, 100e6) is None


# ── um trimestre não é tendência ─────────────────────────────────────────────

def test_queima_usa_a_media_da_janela_nao_o_ultimo_trimestre():
    """Um trimestre com pagamento concentrado viraria pânico; um com
    recebimento atrasado viraria falsa calma."""
    # Três trimestres neutros e um de queima forte.
    linhas = [{"fcf": 0.0}, {"fcf": 0.0}, {"fcf": 0.0}, {"fcf": -400e6}]
    assert fc.queima_media(linhas, trimestres=4) == pytest.approx(100e6)


def test_janela_inteira_no_denominador_e_nao_so_os_trimestres_de_queima():
    """Um ano queimando todo trimestre tem queima média MAIOR que um ano com
    a mesma queima concentrada num trimestre só -- a conta tem que dizer isso."""
    concentrada = fc.queima_media([{"fcf": 0.0}] * 3 + [{"fcf": -400e6}], 4)
    espalhada = fc.queima_media([{"fcf": -100e6}] * 4, 4)
    assert concentrada == espalhada == pytest.approx(100e6)
    pior = fc.queima_media([{"fcf": -400e6}] * 4, 4)
    assert pior > espalhada


def test_periodo_sem_queima_devolve_zero_e_nao_none():
    """Zero é uma afirmação (não queimou); None é ausência de dado."""
    assert fc.queima_media([{"fcf": 10e6}, {"fcf": 20e6}]) == 0.0
    assert fc.queima_media([]) is None
    assert fc.queima_media([{"outro": 1}]) is None


# ── reestruturação quebra a série ────────────────────────────────────────────

def test_queda_abrupta_de_divida_marca_quebra_de_serie():
    """O caso WOLF: lucro anual positivo com prejuízo trimestral é assinatura
    de ganho não-recorrente de reestruturação. Comparar a/a através disso
    mede contabilidade, não operação."""
    r = fc.avaliar(_serie(dividas=[6e9, 6e9, 6e9, 1.2e9, 1.2e9]))
    assert r["quebraDeSerie"] is True


def test_emissao_grande_de_acoes_tambem_marca():
    r = fc.avaliar(_serie(acoes=[150e6, 150e6, 150e6, 600e6, 600e6]))
    assert r["quebraDeSerie"] is True


def test_variacao_normal_nao_marca_quebra():
    """Marcar demais transforma o aviso em ruído que ninguém lê."""
    r = fc.avaliar(_serie(dividas=[3e9, 3.1e9, 3.2e9, 3.3e9, 3.35e9],
                          acoes=[150e6, 151e6, 152e6, 153e6, 154e6]))
    assert r["quebraDeSerie"] is False


def test_quebra_antiga_continua_valendo_para_a_serie():
    """Uma reestruturação três trimestres atrás ainda invalida a comparação
    a/a de hoje -- o aviso é da SÉRIE, não só do último trimestre."""
    r = fc.avaliar(_serie(dividas=[6e9, 1.2e9, 1.2e9, 1.2e9, 1.2e9]))
    assert r["quebraDeSerie"] is True


# ── contas de balanço ────────────────────────────────────────────────────────

def test_divida_liquida_e_divida_menos_caixa():
    assert fc.divida_liquida(1e9, 3e9) == 2e9
    assert fc.divida_liquida(4e9, 3e9) == -1e9


def test_falta_de_dado_nao_vira_zero_implicito():
    """Zero de dívida é uma afirmação sobre a empresa; ausência não é."""
    assert fc.divida_liquida(None, 3e9) is None
    assert fc.divida_liquida(1e9, None) is None


def test_capex_entra_pela_magnitude_qualquer_que_seja_o_sinal():
    """O yfinance devolve capex negativo (saída de caixa) e a AV positivo.
    Somar o negativo inflaria o FCF justamente no trimestre de capex pesado."""
    assert fc.fluxo_livre(100e6, -30e6) == fc.fluxo_livre(100e6, 30e6) == 70e6


def test_liquidez_corrente_e_ativo_sobre_passivo():
    assert fc.liquidez_corrente(2e9, 1e9) == 2.0
    assert fc.liquidez_corrente(2e9, 0) is None
    assert fc.liquidez_corrente(None, 1e9) is None


def test_nan_entra_como_ausencia_e_nao_como_zero():
    assert fc._num(float("nan")) is None
    assert fc._num("lixo") is None
    assert fc._num(None) is None
    assert fc._num("3.5") == 3.5


def test_serie_vazia_declara_em_vez_de_inventar():
    r = fc.avaliar([])
    assert r["disponivel"] is False and "nota" in r


# ── cascata de fontes ────────────────────────────────────────────────────────

def _bruto(fins, fonte="yfinance"):
    return {"balanco": [{**_bal(f), "fonte": fonte} for f in fins],
            "fluxo": [{**_flu(f), "fonte": fonte} for f in fins]}


def test_yfinance_suficiente_dispensa_a_alpha_vantage():
    """Cada ticker custa DUAS chamadas na AV (balanço + fluxo) de um teto real
    de 25/dia -- fôlego não paga cota por profundidade."""
    chamou = []
    fc.coletar(["WOLF"], pausa_s=0, yf_fn=lambda t: _bruto(_FINS),
               av_fn=lambda t: chamou.append(t) or {"balanco": [], "fluxo": []})
    assert chamou == []


def test_yfinance_vazio_cai_para_a_alpha_vantage(capsys):
    col = fc.coletar(["WOLF"], pausa_s=0,
                     yf_fn=lambda t: {"balanco": [], "fluxo": []},
                     av_fn=lambda t: _bruto(_FINS, fonte="alpha_vantage"))
    assert col["porTicker"]["WOLF"]["balanco"]
    assert "tentando Alpha Vantage" in capsys.readouterr().err


def test_profundidade_conta_trimestres_com_balanco_E_fluxo():
    """Balanço sem o fluxo do mesmo trimestre não fecha a conta de FCF."""
    so_balanco = {"balanco": _bruto(_FINS)["balanco"], "fluxo": []}
    assert fc._profundidade(so_balanco) == 0
    assert fc._profundidade(_bruto(_FINS)) == len(_FINS)


def test_historico_guardado_dispensa_a_alpha_vantage():
    chamou = []
    fc.coletar(["WOLF"], pausa_s=0, guardado={"WOLF": _bruto(_FINS)},
               yf_fn=lambda t: _bruto(["2026-09-30"]),
               av_fn=lambda t: chamou.append(t) or {"balanco": [], "fluxo": []})
    assert chamou == []


def test_yfinance_que_estoura_nao_derruba_o_ticker(capsys):
    def _explode(t):
        raise RuntimeError("rede fora")
    col = fc.coletar(["WOLF"], pausa_s=0, yf_fn=_explode,
                     av_fn=lambda t: _bruto(_FINS, fonte="alpha_vantage"))
    assert "WOLF" in col["porTicker"]
    assert "yfinance falhou" in capsys.readouterr().err


def test_sem_dado_nas_duas_fontes_vira_falha_declarada(capsys):
    vazio = {"balanco": [], "fluxo": []}
    col = fc.coletar(["WOLF"], pausa_s=0, yf_fn=lambda t: vazio, av_fn=lambda t: vazio)
    assert col["falhas"] == ["WOLF"] and col["porTicker"] == {}
    assert "SEM BALANÇO" in capsys.readouterr().err


# ── profundidade não regride ─────────────────────────────────────────────────

def test_mesclar_guarda_o_alcance_do_bruto_anterior():
    m = fc.mesclar_bruto({"WOLF": _bruto(_FINS)}, {"WOLF": _bruto(["2026-09-30"])})
    assert len(m["WOLF"]["balanco"]) == len(_FINS) + 1


def test_no_empate_de_trimestre_o_novo_vence():
    antigo = {"WOLF": {"balanco": [_bal("2026-06-30", caixa=1e9)], "fluxo": []}}
    novo = {"WOLF": {"balanco": [_bal("2026-06-30", caixa=2e9)], "fluxo": []}}
    assert fc.mesclar_bruto(antigo, novo)["WOLF"]["balanco"][0]["caixa"] == 2e9


def test_montar_com_guardado_nao_encolhe_a_serie():
    fundo = fc.montar(["WOLF"], pausa_s=0, hoje=_HOJE, yf_fn=lambda t: _bruto(_FINS),
                      av_fn=lambda t: {"balanco": [], "fluxo": []})
    raso = fc.montar(["WOLF"], pausa_s=0, hoje=_HOJE,
                     bruto_anterior=fundo["porTicker"],
                     yf_fn=lambda t: _bruto(["2026-06-30"]),
                     av_fn=lambda t: {"balanco": [], "fluxo": []})
    assert len(raso["series"]["WOLF"]) == len(_FINS)
    assert raso["serieRasa"] == []


def test_falha_total_com_guardado_vira_usandoGuardado(capsys):
    fundo = fc.montar(["WOLF"], pausa_s=0, hoje=_HOJE, yf_fn=lambda t: _bruto(_FINS),
                      av_fn=lambda t: {"balanco": [], "fluxo": []})
    vazio = {"balanco": [], "fluxo": []}
    d = fc.montar(["WOLF"], pausa_s=0, hoje=_HOJE, bruto_anterior=fundo["porTicker"],
                  yf_fn=lambda t: vazio, av_fn=lambda t: vazio)
    assert d["usandoGuardado"] == ["WOLF"] and d["falhas"] == []
    assert "balanço guardado" in capsys.readouterr().err


# ── overlay ──────────────────────────────────────────────────────────────────

def test_overlay_faz_ida_e_volta(tmp_path):
    caminho = str(tmp_path / "folego.json")
    d = fc.montar(["WOLF"], pausa_s=0, hoje=_HOJE, yf_fn=lambda t: _bruto(_FINS),
                  av_fn=lambda t: {"balanco": [], "fluxo": []})
    assert fc.gravar_overlay(d, caminho) is True
    lido = fc.ler_overlay(caminho)
    assert lido["resumo"]["WOLF"]["folegoTrimestres"] == d["resumo"]["WOLF"]["folegoTrimestres"]


def test_overlay_ausente_devolve_none_sem_estourar(tmp_path):
    assert fc.ler_overlay(str(tmp_path / "nao_existe.json")) is None


def test_overlay_corrompido_avisa_e_degrada(tmp_path, capsys):
    caminho = str(tmp_path / "folego.json")
    open(caminho, "w").write("{quebrado")
    assert fc.ler_overlay(caminho) is None
    assert "overlay ilegível" in capsys.readouterr().err


def test_overlay_mora_no_diretorio_persistido():
    """Overlay fora de /var/cache/premercado morre em todo `up --build` --
    ver test_cache_persistente.py para o incidente de 25/08/2026."""
    assert fc.OVERLAY_PATH_DEFAULT.startswith("/var/cache/premercado/")


# ── disjuntor de limite diário da Alpha Vantage ──────────────────────────────
#
# Em 25/08/2026 a AV recusou por limite diário na PRIMEIRA chamada do capex e
# o coletor tentou os outros quatro tickers assim mesmo -- debitando o nosso
# orçamento e dormindo 13s entre cada uma para receber a mesma recusa. A
# recusa é da CHAVE, não do ticker.

@pytest.fixture(autouse=True)
def _disjuntor_limpo():
    from agent import alpha_vantage_provider as avp
    avp._resetar_limite_diario()
    yield
    avp._resetar_limite_diario()


def test_reconhece_o_aviso_de_limite_diario():
    from agent import alpha_vantage_provider as avp
    assert avp.aviso_e_limite_diario(
        "We have detected your API key as *** and our standard API rate limit "
        "is 25 requests per day.") is True
    assert avp.aviso_e_limite_diario("Invalid API call. Please retry.") is False
    assert avp.aviso_e_limite_diario(None) is False


def test_limite_diario_interrompe_o_resto_da_rodada():
    """Cada ticker custa DUAS chamadas aqui, então desistir cedo vale ainda
    mais que no capex -- e o que mais dói é a PAUSA, que acontece antes da
    chamada e portanto não seria poupada por um disjuntor só lá dentro."""
    from agent import alpha_vantage_provider as avp
    chamadas = []

    def _av(tk):
        chamadas.append(tk)
        avp.marcar_limite_diario()
        raise RuntimeError("limite diário")

    vazio = {"balanco": [], "fluxo": []}
    col = fc.coletar(["WOLF", "MU", "NVDA"], pausa_s=0,
                     yf_fn=lambda t: vazio, av_fn=_av)
    assert chamadas == ["WOLF"], "os outros dois nem podiam ser tentados"
    assert col["falhas"] == ["WOLF", "MU", "NVDA"]


def test_com_o_disjuntor_armado_nem_a_pausa_e_paga(monkeypatch):
    """A pausa de 13s por ticker é o custo real de insistir: cinco tickers
    dariam quase um minuto esperando para receber a mesma recusa."""
    from agent import alpha_vantage_provider as avp
    dormiu = []
    monkeypatch.setattr(fc.time, "sleep", lambda s: dormiu.append(s))
    avp.marcar_limite_diario()
    vazio = {"balanco": [], "fluxo": []}
    fc.coletar(["WOLF", "MU", "NVDA"], pausa_s=13,
               yf_fn=lambda t: vazio,
               av_fn=lambda t: (_ for _ in ()).throw(AssertionError("nem devia chamar")))
    assert dormiu == []


def test_av_json_desiste_sozinho_com_o_disjuntor_armado(monkeypatch):
    from agent import alpha_vantage_provider as avp
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "chave-de-teste")
    avp.marcar_limite_diario()
    with pytest.raises(RuntimeError, match="já recusou por limite diário"):
        fc._av_json("BALANCE_SHEET", "WOLF")


def test_sem_chave_nao_arma_nem_estoura(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert fc._av_json("BALANCE_SHEET", "WOLF") is None


# ── quem queima é quem TERMINA com menos caixa ───────────────────────────────
#
# A primeira versão somava só os trimestres NEGATIVOS. Parecia conservador e
# estava errado: empresa com três trimestres fortes e um fraco virava
# "queimando", e dividir o caixa por essa queima fantasma produzia fôlego
# absurdo. Na primeira rodada real (25/08/2026): GOOGL com 165,7 trimestres
# (41 anos) e TSLA com 158,4 -- aritmética certa sobre pergunta errada.

def test_um_trimestre_fraco_no_ano_bom_nao_e_queima():
    """O caso GOOGL: FCF muito positivo em três trimestres e negativo em um."""
    linhas = [{"fcf": 20e9}, {"fcf": 18e9}, {"fcf": 22e9}, {"fcf": -5.9e9}]
    assert fc.queima_media(linhas) == 0.0
    assert fc.folego_trimestres(242e9, fc.queima_media(linhas)) is None


def test_o_caso_googl_nao_produz_mais_41_anos_de_folego():
    r = fc.avaliar(_serie(caixas=[242e9] * 5,
                          ocfs=[30e9, 28e9, 32e9, 30e9, -5.9e9]))
    assert r["geraCaixa"] is True
    assert r["folegoTrimestres"] is None, "165,7 trimestres não é fôlego, é ruído"


def test_queima_liquida_desconta_os_trimestres_bons():
    """Metade queimando e metade gerando o mesmo tanto não é queima nenhuma."""
    assert fc.queima_media([{"fcf": -100e6}, {"fcf": 100e6},
                            {"fcf": -100e6}, {"fcf": 100e6}]) == 0.0
    # Já com o bom cobrindo só parte do ruim, sobra queima líquida.
    assert fc.queima_media([{"fcf": -100e6}, {"fcf": 50e6},
                            {"fcf": -100e6}, {"fcf": 50e6}]) == pytest.approx(25e6)


def test_queimador_de_verdade_continua_com_folego():
    """O conserto não pode apagar o sinal de quem queima mesmo (perfil WOLF)."""
    r = fc.avaliar(_serie())
    assert r["geraCaixa"] is False
    assert r["folegoTrimestres"] == pytest.approx(4.1, abs=0.1)


# ── deterioração recente não pode se esconder na janela ──────────────────────

def test_janela_positiva_com_ultimo_trimestre_queimando_e_declarada():
    """É o preço de olhar o líquido: uma virada recente fica atrás dos
    trimestres bons. Em vez de encurtar a janela, o sinal é declarado."""
    assert fc.piorando([{"fcf": 20e9}, {"fcf": 18e9},
                        {"fcf": 22e9}, {"fcf": -5.9e9}]) is True


def test_quem_vai_bem_ate_o_fim_nao_e_marcado():
    assert fc.piorando([{"fcf": 10e9}] * 4) is False


def test_quem_ja_queima_no_liquido_nao_precisa_do_aviso():
    """Aviso de deterioração em quem já está com fôlego contado seria ruído --
    o número do fôlego já diz o que precisa ser dito."""
    assert fc.piorando([{"fcf": -100e6}] * 4) is False


def test_avaliar_publica_o_aviso_de_piora():
    r = fc.avaliar(_serie(ocfs=[30e9, 28e9, 32e9, 30e9, -5.9e9]))
    assert r["piorando"] is True and r["geraCaixa"] is True


# ── moeda: nem todo mundo reporta em dólar ───────────────────────────────────

def test_a_moeda_do_balanco_e_declarada_e_nao_presumida():
    """A SK Hynix reporta em WON: o campo chamado `caixaUsd` trouxe 54
    TRILHÕES na primeira rodada real -- número certo, rótulo mentiroso."""
    b = [{**_bal(f), "moeda": "KRW"} for f in _FINS]
    fl = [{**_flu(f), "moeda": "KRW"} for f in _FINS]
    r = fc.avaliar(fc.montar_serie(b, fl, hoje=_HOJE))
    assert r["moeda"] == "KRW"


def test_nenhum_campo_do_resumo_afirma_dolar():
    """Amarra por leitura das chaves: o sufixo Usd é uma afirmação sobre a
    moeda, e afirmá-la sem conferir foi o defeito."""
    r = fc.avaliar(_serie())
    assert [k for k in r if k.lower().endswith("usd")] == []


def test_folego_e_liquidez_nao_dependem_da_moeda():
    """São RAZÕES: numerador e denominador na mesma moeda, quociente igual em
    qualquer uma. Por isso o fôlego da SK Hynix vale mesmo sem conversão."""
    em_dolar = fc.avaliar(_serie())
    b = [{**_bal(f, caixa=c * 1380, divida=3e9 * 1380), "moeda": "KRW"}
         for f, c in zip(_FINS, [1.4e9, 1.2e9, 1.0e9, 0.85e9, 0.7e9])]
    fl = [{**_flu(f, ocf=-50e6 * 1380, capex=120e6 * 1380), "moeda": "KRW"}
          for f in _FINS]
    em_won = fc.avaliar(fc.montar_serie(b, fl, hoje=_HOJE))
    assert em_won["folegoTrimestres"] == pytest.approx(em_dolar["folegoTrimestres"])
    assert em_won["caixa"] != em_dolar["caixa"], "os absolutos, sim, mudam"


def test_moeda_ausente_vira_none_e_nao_dolar_por_omissao():
    r = fc.avaliar(_serie())
    assert r["moeda"] is None
