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
import pytest

from agent import capex_hyperscalers as cx


def _linha(trimestre_fim, capex, fonte="yfinance"):
    return {"trimestre": cx.trimestre_calendario(trimestre_fim), "fimFiscal": trimestre_fim,
            "capexUsd": float(capex), "disponivelEm": cx.disponivel_em(trimestre_fim),
            "fonte": fonte}


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
    """A cota da AV é de 15/dia e disputada com earnings e notícias: quando o
    yfinance já traz histórico suficiente, não se gasta chamada."""
    fundo = [_linha(f"202{a}-{m}-28", 30e9) for a in range(3, 6) for m in ("03", "06", "09", "12")]
    chamou_av = []
    cx.coletar(["MSFT"], yf_fn=lambda t: fundo, av_fn=lambda t: chamou_av.append(t) or [])
    assert chamou_av == []


def test_yfinance_raso_e_complementado_pela_alpha_vantage(capsys):
    """O incidente da primeira rodada real (25/08/2026): o yfinance devolve
    ~4-5 trimestres, e com isso a variação a/a some e o experimento de regime
    fica sem lado de contraste. A AV tem 81 trimestres -- ela entra pela
    PROFUNDIDADE, não só quando a primária falha."""
    raso = [_linha("2026-03-31", 35e9), _linha("2026-06-30", 40e9)]
    profundo = [_linha(f"202{a}-{m}-28", 20e9) for a in range(3, 6) for m in ("03", "06", "09", "12")]
    col = cx.coletar(["MSFT"], yf_fn=lambda t: raso, av_fn=lambda t: profundo)
    linhas = col["porEmpresa"]["MSFT"]
    assert len(linhas) > len(raso), "a série tem que ficar mais funda"
    assert "só 2 trimestres no yfinance" in capsys.readouterr().err


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


def test_historico_ainda_raso_e_declarado(capsys):
    col = cx.coletar(["MSFT"], yf_fn=lambda t: [_linha("2026-06-30", 35e9)],
                     av_fn=lambda t: [])
    assert col["rasos"] == ["MSFT"]
    assert "histórico ainda raso" in capsys.readouterr().err

def test_cai_para_alpha_vantage_quando_o_yfinance_vem_vazio(capsys):
    col = cx.coletar(["MSFT"], yf_fn=lambda t: [],
                     av_fn=lambda t: [_linha("2026-06-30", 35e9, fonte="alpha_vantage")])
    assert col["porEmpresa"]["MSFT"][0]["fonte"] == "alpha_vantage"
    assert "sem capex no yfinance" in capsys.readouterr().err

def test_ticker_sem_dado_nas_duas_fontes_e_declarado(capsys):
    col = cx.coletar(["XYZ"], yf_fn=lambda t: [], av_fn=lambda t: [])
    assert col["falhas"] == ["XYZ"] and not col["porEmpresa"]
    assert "SEM DADO nas duas fontes" in capsys.readouterr().err

def test_excecao_numa_fonte_nao_derruba_a_coleta(capsys):
    def _explode(t):
        raise RuntimeError("rede fora")
    col = cx.coletar(["MSFT"], yf_fn=_explode,
                     av_fn=lambda t: [_linha("2026-06-30", 35e9)])
    assert "MSFT" in col["porEmpresa"]
    assert "yfinance falhou" in capsys.readouterr().err

def test_montar_declara_fontes_e_cobertura():
    d = cx.montar(["MSFT", "AMZN"],
                  yf_fn=lambda t: [_linha("2026-06-30", 35e9)], av_fn=lambda t: [])
    assert d["empresasPedidas"] == 2 and d["empresasComDado"] == 2
    assert d["fontes"] == ["yfinance"]


# ── overlay ──────────────────────────────────────────────────────────────────

def test_overlay_faz_ida_e_volta(tmp_path):
    caminho = str(tmp_path / "capex.json")
    d = cx.montar(["MSFT"], yf_fn=lambda t: [_linha("2026-06-30", 35e9)], av_fn=lambda t: [])
    assert cx.gravar_overlay(d, caminho) is True
    assert cx.ler_overlay(caminho)["resumo"]["totalUsdBi"] == d["resumo"]["totalUsdBi"]

def test_overlay_ausente_devolve_none_sem_estourar(tmp_path):
    assert cx.ler_overlay(str(tmp_path / "nao_existe.json")) is None

def test_overlay_corrompido_avisa_e_degrada(tmp_path, capsys):
    caminho = str(tmp_path / "capex.json")
    open(caminho, "w").write("{quebrado")
    assert cx.ler_overlay(caminho) is None
    assert "overlay ilegível" in capsys.readouterr().err
