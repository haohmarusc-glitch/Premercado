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
            "disponivelEm": fc.disponivel_em(fim), "caixaUsd": caixa,
            "dividaUsd": divida, "ativoCirculanteUsd": ac,
            "passivoCirculanteUsd": pc, "acoesEmCirculacao": acoes,
            "fonte": "yfinance"}


def _flu(fim, ocf=-50e6, capex=120e6):
    return {"trimestre": fc.trimestre_calendario(fim), "fimFiscal": fim,
            "disponivelEm": fc.disponivel_em(fim), "caixaOperacionalUsd": ocf,
            "capexUsd": capex, "fonte": "yfinance"}


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
    assert r["queimaMediaUsd"] == pytest.approx(170e6)
    assert r["folegoTrimestres"] == pytest.approx(4.1, abs=0.1)
    assert r["geraCaixa"] is False


def test_quem_gera_caixa_nao_recebe_numero_de_folego():
    """Dividir caixa por queima quase nula devolve um número enorme que
    parece solidez e é só divisão por quase nada. A AUSÊNCIA é a informação."""
    r = fc.avaliar(_serie(ocfs=[900e6] * 5))
    assert r["folegoTrimestres"] is None
    assert r["geraCaixa"] is True


def test_queima_abaixo_do_piso_nao_vira_folego_gigante():
    assert fc.folego_trimestres(1e9, fc.QUEIMA_MINIMA_USD - 1) is None
    assert fc.folego_trimestres(1e9, 100e6) == 10.0


def test_sem_caixa_conhecido_nao_inventa_folego():
    assert fc.folego_trimestres(None, 100e6) is None


# ── um trimestre não é tendência ─────────────────────────────────────────────

def test_queima_usa_a_media_da_janela_nao_o_ultimo_trimestre():
    """Um trimestre com pagamento concentrado viraria pânico; um com
    recebimento atrasado viraria falsa calma."""
    # Três trimestres neutros e um de queima forte.
    linhas = [{"fcfUsd": 0.0}, {"fcfUsd": 0.0}, {"fcfUsd": 0.0}, {"fcfUsd": -400e6}]
    assert fc.queima_media(linhas, trimestres=4) == pytest.approx(100e6)


def test_janela_inteira_no_denominador_e_nao_so_os_trimestres_de_queima():
    """Um ano queimando todo trimestre tem queima média MAIOR que um ano com
    a mesma queima concentrada num trimestre só -- a conta tem que dizer isso."""
    concentrada = fc.queima_media([{"fcfUsd": 0.0}] * 3 + [{"fcfUsd": -400e6}], 4)
    espalhada = fc.queima_media([{"fcfUsd": -100e6}] * 4, 4)
    assert concentrada == espalhada == pytest.approx(100e6)
    pior = fc.queima_media([{"fcfUsd": -400e6}] * 4, 4)
    assert pior > espalhada


def test_periodo_sem_queima_devolve_zero_e_nao_none():
    """Zero é uma afirmação (não queimou); None é ausência de dado."""
    assert fc.queima_media([{"fcfUsd": 10e6}, {"fcfUsd": 20e6}]) == 0.0
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
    assert fc.mesclar_bruto(antigo, novo)["WOLF"]["balanco"][0]["caixaUsd"] == 2e9


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
