"""
A forma 3 feita de forma defensável: o regime de capex como HIPÓTESE medida,
com critério declarado antes -- não como chave ligada em produção.

O que estes testes protegem:

1. O regime diário é ancorado na data de DIVULGAÇÃO. Condicionar a partir do
   fim do trimestre seria look-ahead -- o mesmo vício que o backtest carregou
   até 20/08/2026, e o mais fácil de reintroduzir sem perceber.
2. O critério de aprovação existe e MORDE: amostra pequena reprova mesmo com
   p-valor bonito. Sem isso, o experimento vira a máquina de justificar a
   conclusão que já se queria.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_capex_regime.py -v
"""
import pathlib
import sys

import numpy as np
import pandas as pd

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "agent" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import capex_regime_teste as crt  # noqa: E402


def _trimestre(disp, var_qoq, completo=True):
    return {"trimestre": "2026Q1", "disponivelEm": disp, "variacaoQoQPct": var_qoq,
            "completo": completo, "totalUsd": 1e11}


def _indice(n=60, inicio="2026-01-05"):
    return pd.bdate_range(inicio, periods=n)


# ── look-ahead: a borda que decide o experimento ─────────────────────────────

def test_regime_so_vale_a_partir_da_divulgacao():
    """O capex do trimestre encerrado em 31/03 só existe para o mercado em
    meados de maio. Antes disso, o pregão não tem regime -- e NÃO herda o
    primeiro, que seria inventar passado."""
    idx = _indice(n=120, inicio="2026-01-05")
    regime = crt.serie_de_regime([_trimestre("2026-05-15", 16.0)], idx)
    antes = regime[idx < pd.Timestamp("2026-05-15")]
    depois = regime[idx >= pd.Timestamp("2026-05-15")]
    assert antes.isna().all(), "pregão anterior à divulgação não pode ter regime"
    assert (depois == "acelerando").all()

def test_regime_mais_recente_substitui_o_anterior():
    idx = _indice(n=200, inicio="2026-01-05")
    regime = crt.serie_de_regime(
        [_trimestre("2026-02-10", 16.0), _trimestre("2026-05-15", -20.0)], idx)
    assert regime[pd.Timestamp("2026-03-02")] == "acelerando"
    assert regime[idx[-1]] == "desacelerando"

def test_trimestre_incompleto_nao_vira_regime():
    idx = _indice()
    regime = crt.serie_de_regime([_trimestre("2026-01-10", 16.0, completo=False)], idx)
    assert regime.isna().all()

def test_sem_trimestre_devolve_serie_vazia_sem_estourar():
    assert crt.serie_de_regime([], _indice()).isna().all()

def test_faixa_estavel_nao_e_acelerando():
    idx = _indice()
    regime = crt.serie_de_regime([_trimestre("2026-01-05", 1.0)], idx)
    assert (regime.dropna() == "estável").all()


# ── medição ──────────────────────────────────────────────────────────────────

def test_medir_separa_os_dois_lados():
    idx = _indice(n=200)
    ret = pd.Series(0.001, index=idx)
    regime = pd.Series("resto", index=idx, dtype=object)
    regime.iloc[100:] = "acelerando"
    ret.iloc[100:] = 0.01
    res = crt.medir(ret, regime)
    assert res["pregoesAcelerando"] == 100 and res["pregoesResto"] == 100
    assert res["diferencaPP"] > 0

def test_medir_declara_quando_um_lado_fica_vazio():
    idx = _indice(n=50)
    ret = pd.Series(0.001, index=idx)
    regime = pd.Series("acelerando", index=idx, dtype=object)
    assert "erro" in crt.medir(ret, regime)


# ── o critério morde ─────────────────────────────────────────────────────────

def test_amostra_pequena_reprova_mesmo_com_p_valor_otimo():
    """O caso que este experimento existe para não deixar passar: dois
    trimestres de cada lado, diferença enorme, p minúsculo -- e ainda assim
    não é evidência. Capex dá ~4 pontos por ano."""
    resultado = {"pregoesAcelerando": 500, "pregoesResto": 500, "diferencaPP": 0.5,
                 "pValor": 0.0001}
    passou, motivos = crt.avaliar(resultado, {"acelerando": 2, "desacelerando": 2})
    assert passou is False
    assert any("trimestres insuficiente" in m for m in motivos)

def test_diferenca_irrelevante_reprova_mesmo_significativa():
    """Significância estatística não é relevância operacional: 0,001pp/dia
    não paga corretagem nem slippage."""
    resultado = {"pregoesAcelerando": 500, "pregoesResto": 500, "diferencaPP": 0.001,
                 "pValor": 0.0001}
    passou, motivos = crt.avaliar(resultado, {"acelerando": 8, "desacelerando": 8})
    assert passou is False
    assert any("abaixo do mínimo operacional" in m for m in motivos)

def test_p_valor_alto_reprova():
    resultado = {"pregoesAcelerando": 500, "pregoesResto": 500, "diferencaPP": 0.5,
                 "pValor": 0.4}
    passou, motivos = crt.avaliar(resultado, {"acelerando": 8, "desacelerando": 8})
    assert passou is False and any("p-valor" in m for m in motivos)

def test_tudo_no_lugar_passa():
    """O critério não pode ser impossível de satisfazer -- senão o
    experimento é teatro."""
    resultado = {"pregoesAcelerando": 500, "pregoesResto": 500, "diferencaPP": 0.5,
                 "pValor": 0.001}
    passou, motivos = crt.avaliar(resultado, {"acelerando": 8, "desacelerando": 8})
    assert passou is True and motivos == []

def test_criterio_esta_declarado_como_constante_no_topo():
    """Amarra por leitura de fonte: critério calculado a partir do resultado
    é critério ajustado ao resultado."""
    fonte = (_SCRIPTS / "capex_regime_teste.py").read_text(encoding="utf-8")
    cabeca = fonte[:fonte.index("def serie_de_regime")]
    for const in ("MIN_TRIMESTRES_POR_REGIME", "MIN_PREGOES_POR_REGIME",
                  "ALFA", "MIN_DIFERENCA_DIARIA_PP"):
        assert f"{const} = " in cabeca, f"{const} tem que ser declarado antes das funções"


# ── o experimento não pode degradar o dado que ele mede ──────────────────────
#
# Na primeira semana este script chamava `montar()` direto: cinco chamadas de
# Alpha Vantage por rodada, num orçamento de 15/dia dividido com earnings e
# notícias. Rodar o experimento esgotava a cota e fazia a COLETA seguinte vir
# rasa -- justamente a profundidade de que o experimento depende.

def test_por_padrao_le_o_overlay_e_nao_coleta():
    chamou = {"montar": 0, "overlay": 0}

    def _montar():
        chamou["montar"] += 1
        return {"trimestres": [_trimestre("2026-02-10", 10.0)]}

    def _overlay(caminho=None):
        chamou["overlay"] += 1
        return {"trimestres": [_trimestre("2026-02-10", 10.0)], "coletadoEm": "2026-08-25"}

    dados = crt.carregar_capex(False, overlay=_overlay, montar=_montar)
    assert chamou == {"montar": 0, "overlay": 1}
    assert dados["coletadoEm"] == "2026-08-25"


def test_com_flag_explicita_coleta():
    chamou = {"montar": 0}

    def _montar():
        chamou["montar"] += 1
        return {"trimestres": []}

    crt.carregar_capex(True, overlay=lambda *a: None, montar=_montar)
    assert chamou["montar"] == 1


def test_sem_overlay_devolve_none_em_vez_de_cair_na_rede():
    def _montar():
        raise AssertionError("não pode coletar sem pedido explícito")

    assert crt.carregar_capex(False, overlay=lambda *a: None, montar=_montar) is None
    assert crt.carregar_capex(False, overlay=lambda *a: {"trimestres": []},
                              montar=_montar) is None


def test_main_sem_overlay_sai_com_erro_e_ensina_o_comando(monkeypatch, capsys):
    monkeypatch.setattr(crt.cap, "ler_overlay", lambda *a, **k: None)
    assert crt.main([]) == 2
    err = capsys.readouterr().err
    assert "sem overlay de capex" in err and "agent.capex_hyperscalers" in err


def test_o_script_nao_importa_montar_no_topo():
    """Amarra por leitura de fonte: reintroduzir `from capex_hyperscalers
    import montar` é exatamente como a cota foi parar em zero."""
    fonte = (_SCRIPTS / "capex_regime_teste.py").read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if l.startswith(("import ", "from "))]
    assert not any("import montar" in l for l in codigo), codigo
