"""
Capex dos hiperescaladores -- a tese de IA/data center como FATO datado.

Duas armadilhas dominam esta suíte, porque são as que produzem número certo
com leitura errada:

1. LOOK-AHEAD. O capex do trimestre encerrado em 30/06 só existe para quem
   olha de fora quando a empresa reporta, semanas depois. Usá-lo a partir do
   fim do trimestre é o mesmo vício que o backtest carregou até 20/08/2026.
2. TRIMESTRE INCOMPLETO. Somar o grupo com três das cinco empresas
   reportadas produz um total menor que o anterior -- uma "queda de capex"
   que é só calendário.

Sem rede: a cascata yfinance -> Alpha Vantage é injetada.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_capex_hyperscalers.py -v
"""
import sys

import pytest

from agent import capex_hyperscalers as cx


def _linha(trimestre_fim, capex, fonte="yfinance"):
    return {"trimestre": cx.trimestre_calendario(trimestre_fim), "fimFiscal": trimestre_fim,
            "capexUsd": float(capex), "disponivelEm": cx.disponivel_em(trimestre_fim),
            "fonte": fonte}


@pytest.fixture
def stub_av(monkeypatch):
    """Registra os módulos de import PLANO que o coletor usa quando roda como
    script solto, e devolve uma função para trocar a resposta HTTP.

    Duas armadilhas que este fixture existe para fechar, ambas vividas em
    25/08/2026:

    1. Stub PARCIAL muda o comportamento em silêncio. Um SimpleNamespace com
       só os nomes que o teste lembrou faz o `from alpha_vantage_provider
       import ...` lá dentro falhar, cair no fallback `from agent....` e
       exercitar outro caminho sem dizer nada. Por isso o namespace é montado
       A PARTIR do módulo real, trocando só `_api_key`.
    2. sys.modules é GLOBAL. Registrado na mão, o stub vazava deste arquivo
       para outros testes da suíte -- e um teste que pedia "sem chave
       configurada" recebia a chave falsa daqui. monkeypatch.setitem desfaz
       ao fim de cada teste."""
    import types as _types
    from agent import alpha_vantage_provider as _real

    nomes = {k: getattr(_real, k) for k in dir(_real) if not k.startswith("__")}
    nomes["_api_key"] = lambda: "chave"  # a única coisa de fato substituída
    monkeypatch.setitem(sys.modules, "alpha_vantage_provider",
                        _types.SimpleNamespace(**nomes))
    monkeypatch.setitem(sys.modules, "provider_health", _types.SimpleNamespace(
        consumir_orcamento_diario=lambda *a, **k: True))

    def responder(corpo: dict):
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return corpo
        monkeypatch.setitem(sys.modules, "http_retry", _types.SimpleNamespace(
            SESSION=_types.SimpleNamespace(get=lambda *a, **k: _Resp())))
    return responder


# ── trimestre-calendário: somar maçãs com maçãs ──────────────────────────────

def test_trimestre_calendario_normaliza_anos_fiscais_diferentes():
    """O ano fiscal da Microsoft fecha em junho, o da Alphabet em dezembro --
    mas os TRIMESTRES das duas terminam nos mesmos meses. Agregar por
    trimestre-calendário é o que torna a soma legítima."""
    assert cx.trimestre_calendario("2026-06-30") == "2026Q2"  # MSFT: fim de ano fiscal
    assert cx.trimestre_calendario("2026-06-30") == cx.trimestre_calendario("2026-04-15")
    assert cx.trimestre_calendario("2026-12-31") == "2026Q4"
    assert cx.trimestre_calendario("lixo") is None


# ── look-ahead ───────────────────────────────────────────────────────────────

def test_disponivel_em_fica_depois_do_fim_do_trimestre():
    assert cx.disponivel_em("2026-06-30") == "2026-08-14"  # +45 dias

def test_trimestre_ainda_nao_divulgado_nao_entra():
    """O caso que seria look-ahead: o trimestre fechou, a empresa ainda não
    reportou, e o número já apareceria no painel."""
    por_empresa = {"MSFT": [_linha("2026-03-31", 30e9), _linha("2026-06-30", 35e9)]}
    # Em 01/07 o trimestre de junho existe no yfinance mas ainda não foi
    # divulgado (disponível só em 14/08).
    agregado = cx.agregar(por_empresa, hoje="2026-07-01")
    assert [r["trimestre"] for r in agregado] == ["2026Q1"]
    # Depois da divulgação, entra.
    agregado = cx.agregar(por_empresa, hoje="2026-08-20")
    assert [r["trimestre"] for r in agregado] == ["2026Q1", "2026Q2"]

def test_disponivel_em_do_agregado_e_o_da_ultima_empresa():
    """O trimestre do GRUPO só está completo quando a última reportou."""
    por = {
        "MSFT": [_linha("2026-06-30", 35e9)],
        "AMZN": [{**_linha("2026-06-30", 30e9), "disponivelEm": "2026-09-01"}],
    }
    r = cx.agregar(por, hoje="2026-09-30")[0]
    assert r["disponivelEm"] == "2026-09-01"


# ── trimestre incompleto ─────────────────────────────────────────────────────

def test_trimestre_parcial_e_marcado_e_nao_gera_variacao():
    """A leitura errada mais fácil: 3 de 5 empresas reportadas viram um total
    menor, e o painel mostraria 'capex caiu 40%'."""
    por = {
        "MSFT": [_linha("2026-03-31", 30e9), _linha("2026-06-30", 35e9)],
        "AMZN": [_linha("2026-03-31", 25e9)],  # ainda não reportou o Q2
    }
    ag = {r["trimestre"]: r for r in cx.agregar(por, hoje="2026-09-30")}
    assert ag["2026Q1"]["completo"] is True
    assert ag["2026Q2"]["completo"] is False
    assert ag["2026Q2"]["variacaoQoQPct"] is None, "variação contra parcial inventa queda"

def test_variacao_entre_trimestres_completos_e_calculada():
    por = {
        "MSFT": [_linha("2025-06-30", 20e9), _linha("2026-03-31", 30e9), _linha("2026-06-30", 36e9)],
        "AMZN": [_linha("2025-06-30", 20e9), _linha("2026-03-31", 30e9), _linha("2026-06-30", 36e9)],
    }
    ag = {r["trimestre"]: r for r in cx.agregar(por, hoje="2026-09-30")}
    assert ag["2026Q2"]["variacaoQoQPct"] == pytest.approx(20.0)   # 60 -> 72
    assert ag["2026Q2"]["variacaoYoYPct"] == pytest.approx(80.0)   # 40 -> 72


# ── resumo (o que o Veredito cita) ───────────────────────────────────────────

def test_resumo_usa_o_ultimo_trimestre_completo():
    por = {
        "MSFT": [_linha("2026-03-31", 30e9), _linha("2026-06-30", 36e9)],
        "AMZN": [_linha("2026-03-31", 30e9)],
    }
    r = cx.resumo(cx.agregar(por, hoje="2026-09-30"))
    assert r["trimestre"] == "2026Q1", "parcial não pode virar a manchete"
    assert r["totalUsdBi"] == pytest.approx(60.0)

def test_resumo_classifica_a_direcao():
    def _por(q1, q2):
        return {"A": [_linha("2026-03-31", q1), _linha("2026-06-30", q2)]}
    assert cx.resumo(cx.agregar(_por(30e9, 36e9), hoje="2026-09-30"))["direcao"] == "acelerando"
    assert cx.resumo(cx.agregar(_por(30e9, 20e9), hoje="2026-09-30"))["direcao"] == "desacelerando"
    assert cx.resumo(cx.agregar(_por(30e9, 30.3e9), hoje="2026-09-30"))["direcao"] == "estável"

def test_sem_trimestre_completo_declara_em_vez_de_inventar():
    r = cx.resumo([])
    assert r["disponivel"] is False and "nota" in r


# ── cascata de fontes ────────────────────────────────────────────────────────

def test_yfinance_profundo_dispensa_a_alpha_vantage():
    """A chave da AV permite 25 chamadas/dia e nós nos limitamos a 15,
    disputadas com earnings e notícias: quando o yfinance já traz histórico
    suficiente, não se gasta chamada."""
    fundo = [_linha(f"202{a}-{m}-28", 30e9) for a in range(3, 6) for m in ("03", "06", "09", "12")]
    chamou_av = []
    cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: fundo, av_fn=lambda t: chamou_av.append(t) or [])
    assert chamou_av == []


def test_yfinance_raso_e_complementado_pela_alpha_vantage(capsys):
    """O incidente da primeira rodada real (25/08/2026): o yfinance devolve
    ~4-5 trimestres, e com isso a variação a/a some e o experimento de regime
    fica sem lado de contraste. A AV tem 81 trimestres -- ela entra pela
    PROFUNDIDADE, não só quando a primária falha."""
    raso = [_linha("2026-03-31", 35e9), _linha("2026-06-30", 40e9)]
    profundo = [_linha(f"202{a}-{m}-28", 20e9) for a in range(3, 6) for m in ("03", "06", "09", "12")]
    col = cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: raso, av_fn=lambda t: profundo)
    linhas = col["porEmpresa"]["MSFT"]
    assert len(linhas) > len(raso), "a série tem que ficar mais funda"
    # A frase soma yfinance e overlay porque é a profundidade MESCLADA que
    # decide se vale gastar cota -- aqui não há overlay, então são os 2.
    assert "só 2 trimestres somando yfinance e overlay" in capsys.readouterr().err


def test_no_empate_de_trimestre_o_yfinance_vence():
    """Trocar a fonte no meio da série criaria degrau artificial justamente
    na variação t/t, que é o número que se lê."""
    yf = [{**_linha("2026-06-30", 40e9), "fonte": "yfinance"}]
    av = [{**_linha("2026-06-30", 39e9), "fonte": "alpha_vantage"},
          {**_linha("2026-03-31", 30e9), "fonte": "alpha_vantage"}]
    combinado = cx.combinar(yf, av)
    por_t = {l["trimestre"]: l for l in combinado}
    assert por_t["2026Q2"]["capexUsd"] == 40e9
    assert por_t["2026Q2"]["fonte"] == "yfinance"
    assert len(combinado) == 2, "o trimestre que só a AV tem entra"


def test_coleta_rasa_e_declarada(capsys):
    col = cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: [_linha("2026-06-30", 35e9)],
                     av_fn=lambda t: [])
    assert col["rasos"] == ["MSFT"]
    # A frase fala da COLETA, não do histórico: o overlay guardado pode cobrir
    # a profundidade, e `montar` é quem decide isso depois da mesclagem.
    assert "coleta rasa" in capsys.readouterr().err

def test_cai_para_alpha_vantage_quando_o_yfinance_vem_vazio(capsys):
    col = cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: [],
                     av_fn=lambda t: [_linha("2026-06-30", 35e9, fonte="alpha_vantage")])
    assert col["porEmpresa"]["MSFT"][0]["fonte"] == "alpha_vantage"
    assert "sem capex no yfinance" in capsys.readouterr().err

def test_ticker_sem_dado_nas_duas_fontes_e_declarado(capsys):
    col = cx.coletar(["XYZ"], pausa_s=0, yf_fn=lambda t: [], av_fn=lambda t: [])
    assert col["falhas"] == ["XYZ"] and not col["porEmpresa"]
    assert "SEM DADO nas duas fontes" in capsys.readouterr().err

def test_excecao_numa_fonte_nao_derruba_a_coleta(capsys):
    def _explode(t):
        raise RuntimeError("rede fora")
    col = cx.coletar(["MSFT"], pausa_s=0, yf_fn=_explode,
                     av_fn=lambda t: [_linha("2026-06-30", 35e9)])
    assert "MSFT" in col["porEmpresa"]
    assert "yfinance falhou" in capsys.readouterr().err

def test_montar_declara_fontes_e_cobertura():
    d = cx.montar(["MSFT", "AMZN"], pausa_s=0,
                  yf_fn=lambda t: [_linha("2026-06-30", 35e9)], av_fn=lambda t: [])
    assert d["empresasPedidas"] == 2 and d["empresasComDado"] == 2
    assert d["fontes"] == ["yfinance"]


# ── overlay ──────────────────────────────────────────────────────────────────

def test_overlay_faz_ida_e_volta(tmp_path):
    caminho = str(tmp_path / "capex.json")
    d = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: [_linha("2026-06-30", 35e9)], av_fn=lambda t: [])
    assert cx.gravar_overlay(d, caminho) is True
    assert cx.ler_overlay(caminho)["resumo"]["totalUsdBi"] == d["resumo"]["totalUsdBi"]

def test_overlay_ausente_devolve_none_sem_estourar(tmp_path):
    assert cx.ler_overlay(str(tmp_path / "nao_existe.json")) is None

def test_overlay_corrompido_avisa_e_degrada(tmp_path, capsys):
    caminho = str(tmp_path / "capex.json")
    open(caminho, "w").write("{quebrado")
    assert cx.ler_overlay(caminho) is None
    assert "overlay ilegível" in capsys.readouterr().err


# ── throttle da Alpha Vantage: 200 OK com aviso não é "sem dados" ────────────

def test_aviso_de_limite_vira_erro_nomeado(stub_av):
    """O plano grátis limita 5 chamadas/minuto e responde ao estouro com
    200 OK + JSON de aviso. Lido como lista vazia, isso deixou GOOGL e META
    rasos SEM dizer por quê (segunda rodada real, 25/08/2026) -- a terceira
    falha silenciosa da mesma família no mesmo dia."""
    stub_av({"Information": "Thank you for using Alpha Vantage! Our standard "
                            "API rate limit is 5 requests per minute."})
    with pytest.raises(RuntimeError, match="aviso em vez de dados"):
        cx._do_alpha_vantage("GOOGL")

def test_resposta_sem_quarterly_reports_tambem_grita(stub_av):
    """200 OK com corpo inesperado também não é "sem dados": tratar como
    lista vazia é a mesma cegueira, só com outra roupa."""
    stub_av({"symbol": "GOOGL"})
    with pytest.raises(RuntimeError, match="sem quarterlyReports"):
        cx._do_alpha_vantage("GOOGL")


def test_chamadas_a_av_sao_espacadas(monkeypatch):
    """Sem espaçar, as últimas da fila voltam com aviso de limite -- que é
    exatamente o que aconteceu com GOOGL e META."""
    dormidas = []
    monkeypatch.setattr(cx.time, "sleep", lambda s: dormidas.append(s))
    # max_aprofundamentos alto de propósito: aqui o assunto é o ESPAÇAMENTO,
    # e o teto por rodada tem teste próprio.
    cx.coletar(["MSFT", "GOOGL", "AMZN"], yf_fn=lambda t: [], max_aprofundamentos=9,
               av_fn=lambda t: [_linha("2026-06-30", 30e9)], pausa_s=13)
    # três tickers -> duas pausas (a primeira chamada não espera)
    assert dormidas == [13, 13]


def test_um_ticker_so_nao_dorme_a_toa(monkeypatch):
    dormidas = []
    monkeypatch.setattr(cx.time, "sleep", lambda s: dormidas.append(s))
    cx.coletar(["MSFT"], yf_fn=lambda t: [], av_fn=lambda t: [_linha("2026-06-30", 30e9)],
               pausa_s=13)
    assert dormidas == []


# ── profundidade não regride ─────────────────────────────────────────────────
#
# O defeito real: o coletor grava o overlay inteiro a cada rodada, então uma
# rodada com a cota da Alpha Vantage esgotada devolvia a série ao tamanho raso
# do yfinance. A profundidade conquistada na semana anterior sumia, e o
# experimento de regime perdia o lado de contraste.

def _serie(fins, fonte="yfinance"):
    return [_linha(f, 30e9, fonte) for f in fins]


_FUNDOS = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
           "2026-03-31", "2026-06-30"]


def test_mesclar_guarda_o_alcance_do_historico_anterior():
    anterior = {"MSFT": _serie(_FUNDOS)}
    novo = {"MSFT": _serie(["2026-06-30"])}
    assert len(cx.mesclar_bruto(anterior, novo)["MSFT"]) == len(_FUNDOS)


def test_no_empate_de_trimestre_o_novo_vence():
    anterior = {"MSFT": [_linha("2026-06-30", 30e9)]}
    novo = {"MSFT": [_linha("2026-06-30", 35e9)]}
    linhas = cx.mesclar_bruto(anterior, novo)["MSFT"]
    assert len(linhas) == 1 and linhas[0]["capexUsd"] == 35e9


def test_mesclar_aceita_empresa_nova_e_empresa_so_guardada():
    m = cx.mesclar_bruto({"MSFT": _serie(["2026-03-31"])}, {"AMZN": _serie(["2026-06-30"])})
    assert sorted(m) == ["AMZN", "MSFT"]


def test_mesclar_corta_no_teto_de_trimestres_brutos():
    demais = [f"20{a:02d}-03-31" for a in range(0, 60)]
    m = cx.mesclar_bruto({"MSFT": _serie(demais)}, {})
    assert len(m["MSFT"]) == cx.TRIMESTRES_BRUTOS_GUARDADOS
    assert m["MSFT"][-1]["trimestre"] == cx.trimestre_calendario(demais[-1])


def test_montar_com_guardado_nao_encolhe_a_serie():
    fundo = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(_FUNDOS), av_fn=lambda t: [])
    raso = cx.montar(["MSFT"], pausa_s=0, bruto_anterior=fundo["porEmpresa"],
                     yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=lambda t: [])
    assert len(raso["porEmpresa"]["MSFT"]) == len(_FUNDOS)
    assert raso["historicoRaso"] == []


def test_sem_guardado_a_serie_rasa_e_declarada_rasa():
    d = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=lambda t: [])
    assert d["historicoRaso"] == ["MSFT"]


def test_falha_total_com_historico_guardado_vira_usandoGuardado(capsys):
    fundo = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(_FUNDOS), av_fn=lambda t: [])
    d = cx.montar(["MSFT"], pausa_s=0, bruto_anterior=fundo["porEmpresa"],
                  yf_fn=lambda t: [], av_fn=lambda t: [])
    assert d["usandoGuardado"] == ["MSFT"] and d["falhas"] == []
    assert "histórico guardado" in capsys.readouterr().err


def test_falha_total_sem_historico_continua_falha():
    d = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: [], av_fn=lambda t: [])
    assert d["falhas"] == ["MSFT"] and d["usandoGuardado"] == []


def test_montar_publica_o_bruto_para_a_proxima_rodada_mesclar():
    d = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(_FUNDOS), av_fn=lambda t: [])
    assert cx.ler_overlay.__name__  # o overlay é o dicionário inteiro
    assert set(d["porEmpresa"]) == {"MSFT"}


# ── grupo esperado: empresa faltando não pode virar "completo" ───────────────

def test_esperado_explicito_impede_completo_com_empresa_faltando():
    por = {"MSFT": [_linha("2026-06-30", 35e9)], "AMZN": [_linha("2026-06-30", 25e9)]}
    assert cx.agregar(por, hoje="2026-09-30", esperado=5)[0]["completo"] is False
    assert cx.agregar(por, hoje="2026-09-30", esperado=2)[0]["completo"] is True


def test_montar_usa_o_tamanho_do_grupo_pedido_como_esperado():
    d = cx.montar(["MSFT", "AMZN", "ORCL"], pausa_s=0,
                  yf_fn=lambda t: [] if t == "ORCL" else _serie(_FUNDOS),
                  av_fn=lambda t: [])
    # ORCL sem dado: nenhum trimestre pode se dizer completo com 2 de 3.
    assert all(t["completo"] is False for t in d["trimestres"])
    assert d["resumo"]["disponivel"] is False


# ── cota só é gasta pelo que o disco não tem ─────────────────────────────────
#
# A decisão de chamar a Alpha Vantage olhava a profundidade da COLETA. Como o
# yfinance devolve sempre os mesmos ~5 trimestres, isso significava cinco
# chamadas por semana para rebuscar história já guardada -- gasto de um
# orçamento de 15/dia sem nada de novo em troca.

def _av_espiao():
    chamadas = []

    def _av(t):
        chamadas.append(t)
        return _serie(_FUNDOS, fonte="alpha_vantage")
    return chamadas, _av


def test_historico_guardado_dispensa_a_alpha_vantage(capsys):
    chamadas, av = _av_espiao()
    col = cx.coletar(["MSFT"], pausa_s=0, guardado={"MSFT": _serie(_FUNDOS)},
                     yf_fn=lambda t: _serie(["2026-09-30"]), av_fn=av)
    assert chamadas == [], "profundidade já está no disco: não há o que comprar"
    assert col["rasos"] == []
    assert "sem gastar Alpha Vantage" in capsys.readouterr().err


def test_yfinance_vazio_ainda_chama_a_alpha_vantage_mesmo_com_disco_fundo():
    """Histórico guardado dá alcance, não trimestre recente. Sem coleta nova,
    a série congela -- e congelar em silêncio é o defeito que a mesclagem
    poderia ter introduzido."""
    chamadas, av = _av_espiao()
    cx.coletar(["MSFT"], pausa_s=0, guardado={"MSFT": _serie(_FUNDOS)},
               yf_fn=lambda t: [], av_fn=av)
    assert chamadas == ["MSFT"]


def test_disco_raso_e_coleta_rasa_ainda_gastam_cota():
    chamadas, av = _av_espiao()
    cx.coletar(["MSFT"], pausa_s=0, guardado={"MSFT": _serie(["2026-03-31"])},
               yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=av)
    assert chamadas == ["MSFT"]


def test_sem_guardado_a_cascata_segue_como_antes():
    chamadas, av = _av_espiao()
    cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=av)
    assert chamadas == ["MSFT"]


def test_a_profundidade_conta_trimestres_distintos_nao_linhas():
    """Somar len() das duas listas contaria o trimestre repetido duas vezes e
    daria a cota por economizada sem que a série tivesse alcance."""
    repetidos = _serie(["2026-06-30"]) * 12
    assert cx._profundidade_apos_mesclar(repetidos, repetidos) == 1


def test_montar_repassa_o_guardado_para_a_decisao_de_cota():
    fundo = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(_FUNDOS), av_fn=lambda t: [])
    chamadas, av = _av_espiao()
    cx.montar(["MSFT"], pausa_s=0, bruto_anterior=fundo["porEmpresa"],
              yf_fn=lambda t: _serie(["2026-09-30"]), av_fn=av)
    assert chamadas == []


# ── janela publicada ─────────────────────────────────────────────────────────

def test_o_agregado_publica_a_historia_que_o_bruto_guarda():
    """Publicar 12 trimestres deixava o experimento de regime com 11
    'acelerando' e 1 'estável' -- sem lado de contraste, a hipótese fica
    intestável em vez de reprovada. O dado para além disso já está no disco."""
    assert cx.TRIMESTRES_GUARDADOS >= cx.TRIMESTRES_BRUTOS_GUARDADOS
    fins = [f"{ano}-{mes}" for ano in range(2015, 2026) for mes in
            ("03-31", "06-30", "09-30", "12-31")]
    assert len(fins) > cx.TRIMESTRES_BRUTOS_GUARDADOS, "a fixture tem que estourar o teto"
    d = cx.montar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(fins), av_fn=lambda t: [])
    assert len(d["trimestres"]) == cx.TRIMESTRES_BRUTOS_GUARDADOS
    assert d["trimestres"][0]["trimestre"] < "2020Q1", "a janela alcança o ciclo anterior"


# ── a chave não pode vazar no log ────────────────────────────────────────────
#
# Quando a cota estoura, a Alpha Vantage responde com um aviso que ECOA a
# chave em texto claro. Os pontos que imprimem esse aviso estavam escrevendo a
# credencial no stderr do container -- de onde ela vai para o log do Docker,
# para o terminal de quem roda o comando, e para onde essa saída for colada.

def test_aviso_de_cota_nao_carrega_a_chave(monkeypatch):
    from agent import alpha_vantage_provider as avp
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "SEGREDO123456789")
    aviso = ("We have detected your API key as SEGREDO123456789 and our "
             "standard API rate limit is 25 requests per day.")
    limpo = avp.censurar_chave(aviso)
    assert "SEGREDO123456789" not in limpo
    assert "25 requests per day" in limpo, "o motivo tem que sobreviver à censura"


def test_censura_nao_estraga_mensagem_sem_chave(monkeypatch):
    from agent import alpha_vantage_provider as avp
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "SEGREDO123456789")
    assert avp.censurar_chave("limite diário atingido") == "limite diário atingido"


def test_sem_chave_configurada_a_censura_nao_apaga_tudo(monkeypatch):
    """Chave vazia não pode virar um replace('') que estoura a mensagem."""
    from agent import alpha_vantage_provider as avp
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert avp.censurar_chave("aviso qualquer") == "aviso qualquer"


def test_os_tres_pontos_que_logam_aviso_da_av_censuram():
    """Amarra por leitura de fonte: reintroduzir o aviso cru em qualquer um
    deles põe a credencial no log de novo."""
    import pathlib
    base = pathlib.Path(cx.__file__).parent
    for arquivo, marca in (("capex_hyperscalers.py", "aviso em vez de dados"),
                           ("atualizar_earnings.py", "aviso em vez de CSV"),
                           ("alpha_vantage_provider.py", "sem série")):
        fonte = (base / arquivo).read_text(encoding="utf-8")
        # rindex: a frase também aparece em comentário mais acima; o que
        # interessa é o ponto onde ela é de fato emitida.
        i = fonte.rindex(marca)
        assert "censurar_chave" in fonte[i - 200:i + 300], \
            f"{arquivo} loga aviso da AV sem censurar"


# ── a cota vai para quem está mais raso ──────────────────────────────────────

def test_a_fila_da_alpha_vantage_comeca_pelo_mais_raso():
    """Em 25/08/2026 a cota acabou no meio da fila e quem ficou de fora foram
    META e ORCL -- por serem os últimos da lista fixa, não por precisarem
    menos. Com a ordem por carência, a cota do dia vai para o buraco maior."""
    pedidos = []
    guardado = {"MSFT": _serie(_FUNDOS), "META": _serie(["2026-06-30"]), "ORCL": []}
    cx.coletar(["MSFT", "META", "ORCL"], pausa_s=0, guardado=guardado,
               max_aprofundamentos=9,  # aqui o assunto é a ORDEM, não o teto
               yf_fn=lambda t: [], av_fn=lambda t: pedidos.append(t) or [])
    assert pedidos == ["ORCL", "META", "MSFT"]


def test_empate_de_profundidade_e_resolvido_pelo_nome():
    pedidos = []
    cx.coletar(["ORCL", "META", "AMZN"], pausa_s=0, max_aprofundamentos=9,
               yf_fn=lambda t: [], av_fn=lambda t: pedidos.append(t) or [])
    assert pedidos == ["AMZN", "META", "ORCL"], "ordem tem que ser reproduzível"


# ── disjuntor de limite diário da Alpha Vantage ──────────────────────────────
#
# 25/08/2026: a AV recusou por limite diário na PRIMEIRA chamada e o coletor
# tentou os outros quatro assim mesmo, debitando o nosso orçamento e dormindo
# 13s entre cada uma para receber a mesma recusa. A recusa é da CHAVE.

@pytest.fixture(autouse=True)
def _disjuntor_limpo():
    from agent import alpha_vantage_provider as avp
    avp._resetar_limite_diario()
    yield
    avp._resetar_limite_diario()


def test_limite_diario_na_primeira_para_a_fila():
    from agent import alpha_vantage_provider as avp
    chamadas = []

    def _av(t):
        chamadas.append(t)
        avp.marcar_limite_diario()
        raise RuntimeError("limite diário")

    col = cx.coletar(["MSFT", "GOOGL", "AMZN"], pausa_s=0, max_aprofundamentos=9,
                     yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=_av)
    assert chamadas == ["AMZN"], "só o primeiro da fila (ordem por carência)"
    assert sorted(col["rasos"]) == ["AMZN", "GOOGL", "MSFT"]


def test_com_o_disjuntor_armado_a_pausa_nao_e_paga(monkeypatch):
    """Cinco tickers a 13s dariam quase um minuto esperando por nada."""
    from agent import alpha_vantage_provider as avp
    import time as _time
    dormiu = []
    monkeypatch.setattr(_time, "sleep", lambda s: dormiu.append(s))
    monkeypatch.setattr(cx.time, "sleep", lambda s: dormiu.append(s))
    avp.marcar_limite_diario()
    cx.coletar(["MSFT", "GOOGL"], pausa_s=13, yf_fn=lambda t: _serie(["2026-06-30"]),
               av_fn=lambda t: (_ for _ in ()).throw(AssertionError("nem devia chamar")))
    assert dormiu == []


def test_o_yfinance_do_ticker_pulado_nao_e_jogado_fora():
    """Desistir da AV não pode desistir do que o yfinance já trouxe."""
    from agent import alpha_vantage_provider as avp
    avp.marcar_limite_diario()
    col = cx.coletar(["MSFT"], pausa_s=0, yf_fn=lambda t: _serie(["2026-06-30"]),
                     av_fn=lambda t: [])
    assert col["porEmpresa"]["MSFT"], "o trimestre recente do yfinance fica"
    assert col["falhas"] == []


# ── aprofundar aos poucos em vez de tudo de uma vez ─────────────────────────
#
# Tentar as cinco numa rodada exige um dia com cinco chamadas sobrando num teto
# real de 25 dividido com earnings e notícias -- e em 25/08/2026 isso falhou
# TRÊS vezes seguidas. Pior: o nosso contador de orçamento não protege disso,
# porque só conta o que NÓS debitamos e não sabe o que a AV já contou (naquele
# dia a nossa conta marcava 1 enquanto a AV recusava por ter chegado a 25).

def test_o_teto_por_rodada_limita_as_chamadas():
    pedidos = []
    cx.coletar(["MSFT", "GOOGL", "AMZN", "META", "ORCL"], pausa_s=0,
               max_aprofundamentos=2, yf_fn=lambda t: _serie(["2026-06-30"]),
               av_fn=lambda t: pedidos.append(t) or [])
    assert len(pedidos) == 2


def test_quem_ficou_de_fora_e_declarado_e_nao_some(capsys):
    col = cx.coletar(["MSFT", "GOOGL", "AMZN"], pausa_s=0, max_aprofundamentos=1,
                     yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=lambda t: [])
    assert sorted(col["rasos"]) == ["AMZN", "GOOGL", "MSFT"]
    assert "fica para a próxima" in capsys.readouterr().err


def test_o_yfinance_de_quem_ficou_de_fora_nao_e_jogado_fora():
    """Adiar o aprofundamento não pode custar o trimestre recente."""
    col = cx.coletar(["MSFT", "GOOGL"], pausa_s=0, max_aprofundamentos=0,
                     yf_fn=lambda t: _serie(["2026-06-30"]), av_fn=lambda t: [])
    assert sorted(col["porEmpresa"]) == ["GOOGL", "MSFT"]
    assert col["falhas"] == []


def test_a_profundidade_converge_ao_longo_das_rodadas():
    """O ponto todo: sem depender de UM dia com cota sobrando, três rodadas
    semanais levam o grupo inteiro ao fundo. A ordem por carência garante que
    cada rodada atende quem ficou para trás na anterior."""
    grupo = ["MSFT", "GOOGL", "AMZN", "META", "ORCL"]
    guardado: dict = {}
    for _ in range(3):
        col = cx.coletar(grupo, pausa_s=0, max_aprofundamentos=2, guardado=guardado,
                         yf_fn=lambda t: _serie(["2026-06-30"]),
                         av_fn=lambda t: _serie(_FUNDOS, fonte="alpha_vantage"))
        guardado = cx.mesclar_bruto(guardado, col["porEmpresa"])
    fundos = [t for t in grupo if len(guardado.get(t) or []) >= cx.PROFUNDIDADE_MINIMA]
    assert sorted(fundos) == sorted(grupo), "três rodadas têm que fechar o grupo"


def test_uma_rodada_sozinha_nao_fecha_o_grupo():
    """O contraste do teste acima: é a REPETIÇÃO que resolve, e o teto é o
    preço consciente disso."""
    col = cx.coletar(["MSFT", "GOOGL", "AMZN", "META", "ORCL"], pausa_s=0,
                     max_aprofundamentos=2, yf_fn=lambda t: _serie(["2026-06-30"]),
                     av_fn=lambda t: _serie(_FUNDOS, fonte="alpha_vantage"))
    fundos = [t for t, l in col["porEmpresa"].items() if len(l) >= cx.PROFUNDIDADE_MINIMA]
    assert len(fundos) == 2


def test_o_teto_e_configuravel_por_ambiente():
    """O operador pode abrir a torneira num dia sabidamente livre."""
    import pathlib
    fonte = pathlib.Path(cx.__file__).read_text(encoding="utf-8")
    assert "CAPEX_MAX_APROFUNDAMENTOS" in fonte
