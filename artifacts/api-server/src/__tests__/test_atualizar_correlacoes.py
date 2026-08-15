"""
Testes de atualizar_correlacoes.py -- refresh das correlações do Radar IA
calculado localmente do yfinance + overlay carregado por radar_ia_2026.py
no import.

Sem rede: yf.download é mockado com séries sintéticas de retorno conhecido
(perfeitamente correlacionadas, anticorrelacionadas, independentes), e o
ciclo overlay gravar->carregar usa tmp_path + RADAR_CORR_OVERLAY.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_atualizar_correlacoes.py -v
"""
import importlib.util
import os

import numpy as np
import pandas as pd
import pytest

# Import de PACOTE (conftest.py já põe src/ no sys.path). NÃO inserir
# src/agent/ no path: existe um agent.py DENTRO de agent/, então com o
# diretório no path o nome `agent` passa a resolver pro módulo em vez do
# pacote -- e qualquer teste que faça `from agent.x import ...` depois deste
# quebra. Isso já passou despercebido porque a suíte inteira só falhava em
# certas ordens de coleta (outro arquivo importava agent.* antes e deixava o
# pacote certo em sys.modules).
from agent import atualizar_correlacoes as ac

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")


def _fechamentos(series: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(series.values())))
    idx = pd.date_range(end=pd.Timestamp("2026-08-14"), periods=n, freq="B")
    return pd.DataFrame(series, index=idx)


# ── universo ───────────────────────────────────────────────────────────────

def test_universo_cobre_tudo_que_o_snapshot_ja_descreve():
    u = ac.universo()
    assert "MU" in u and "SNDK" in u and "NVDA" in u
    assert "EWY" in u          # proxy Samsung, só aparece nos pares
    assert u == sorted(set(u))  # ordenado e sem duplicata


# ── correlacoes_de (matemática sobre RETORNOS, não nível) ─────────────────

def test_series_que_sobem_juntas_no_mesmo_dia_tem_corr_alta():
    base = [100.0]
    for i in range(90):
        base.append(base[-1] * (1.01 if i % 2 == 0 else 0.995))
    # B = mesma variação diária de A, em outro patamar de preço
    b = [50.0]
    for i in range(90):
        b.append(b[-1] * (1.01 if i % 2 == 0 else 0.995))
    pares = ac.correlacoes_de(_fechamentos({"AAA": base, "BBB": b}), min_pregoes=30)
    assert pares[("AAA", "BBB")] == pytest.approx(1.0, abs=0.01)


def test_series_anticorrelacionadas():
    a = [100.0]
    b = [100.0]
    for i in range(90):
        sobe = i % 2 == 0
        a.append(a[-1] * (1.01 if sobe else 0.99))
        b.append(b[-1] * (0.99 if sobe else 1.01))
    pares = ac.correlacoes_de(_fechamentos({"AAA": b, "BBB": a}), min_pregoes=30)
    assert pares[("AAA", "BBB")] < -0.9


def test_tendencia_comum_nao_infla_correlacao_de_retorno():
    """Duas séries em alta forte mas com movimento diário independente: se o
    cálculo fosse sobre NÍVEL de preço a correlação sairia ~1; sobre retorno
    tem que ficar perto de zero. É a razão de existir do pct_change()."""
    rng = np.random.default_rng(42)
    n = 200
    a = list(100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.01, n)))
    b = list(100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.01, n)))
    pares = ac.correlacoes_de(_fechamentos({"AAA": a, "BBB": b}), min_pregoes=30)
    assert abs(pares[("AAA", "BBB")]) < 0.25


def test_par_com_poucos_pregoes_em_comum_fica_de_fora():
    a = [100.0 + i for i in range(90)]
    b = [None] * 80 + [50.0 + i for i in range(10)]  # só 10 pregões
    df = _fechamentos({"AAA": a, "BBB": [float("nan") if v is None else v for v in b]})
    assert ac.correlacoes_de(df, min_pregoes=60) == {}


def test_um_ticker_so_nao_produz_par():
    assert ac.correlacoes_de(_fechamentos({"AAA": [1.0, 2.0, 3.0]})) == {}


def test_chaves_sempre_ordenadas():
    a = [100.0 + i for i in range(90)]
    pares = ac.correlacoes_de(_fechamentos({"ZZZ": a, "AAA": a}), min_pregoes=30)
    assert list(pares) == [("AAA", "ZZZ")]


# ── baixar_fechamentos (yf.download mockado) ──────────────────────────────

def test_baixar_fechamentos_extrai_close_do_multiindex(monkeypatch):
    idx = pd.date_range(end=pd.Timestamp("2026-08-14"), periods=5, freq="B")
    cols = pd.MultiIndex.from_product([["Close", "Volume"], ["MU", "NVDA"]])
    dados = pd.DataFrame(1.0, index=idx, columns=cols)
    monkeypatch.setattr(ac.yf, "download", lambda *a, **k: dados)
    fech = ac.baixar_fechamentos(["MU", "NVDA"])
    assert list(fech.columns) == ["MU", "NVDA"]


def test_baixar_fechamentos_vazio_nao_explode(monkeypatch):
    monkeypatch.setattr(ac.yf, "download", lambda *a, **k: pd.DataFrame())
    assert ac.baixar_fechamentos(["MU"]).empty


def test_baixar_fechamentos_descarta_coluna_toda_nan(monkeypatch):
    idx = pd.date_range(end=pd.Timestamp("2026-08-14"), periods=5, freq="B")
    cols = pd.MultiIndex.from_product([["Close"], ["MU", "DELISTADA"]])
    dados = pd.DataFrame({("Close", "MU"): [1.0] * 5,
                          ("Close", "DELISTADA"): [float("nan")] * 5}, index=idx)
    dados.columns = cols
    monkeypatch.setattr(ac.yf, "download", lambda *a, **k: dados)
    assert list(ac.baixar_fechamentos(["MU", "DELISTADA"]).columns) == ["MU"]


# ── resumo de mudanças ────────────────────────────────────────────────────

def test_resumo_detecta_cruzamento_do_limiar_070():
    # MU-SNDK era 0.82 no snapshot; cair pra 0.55 desfaz o "mesmo trade".
    res = ac.resumo_mudancas({("MU", "SNDK"): 0.55})
    assert res["cruzaram_070"] and res["cruzaram_070"][0][0] == ("MU", "SNDK")
    assert res["grandes"]


def test_resumo_marca_par_novo():
    res = ac.resumo_mudancas({("AAA", "ZZZ"): 0.5})
    assert ("AAA", "ZZZ") in res["novos"]


# ── ciclo overlay: gravar -> radar carrega no import ───────────────────────

def _carregar_radar_fresco(nome: str):
    """Instância NOVA de radar_ia_2026 (sem tocar a já importada por outros
    testes) pra exercitar o overlay do import."""
    spec = importlib.util.spec_from_file_location(
        nome, os.path.join(_AGENT_DIR, "radar_ia_2026.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_overlay_gravado_e_aplicado_no_import(tmp_path, monkeypatch):
    destino = str(tmp_path / "radar_correlacoes.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)

    # MU-SNDK era 0.82 no snapshot; overlay diz 0.61 e traz um par novo.
    ac.gravar_overlay({("MU", "SNDK"): 0.61, ("AAPL", "NVDA"): 0.33}, path=destino)

    radar = _carregar_radar_fresco("radar_overlay_test")
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.61)
    assert radar.correlacao("AAPL", "NVDA") == pytest.approx(0.33)
    # pares não cobertos pelo overlay continuam com o valor embutido
    assert radar.correlacao("MU", "LRCX") == pytest.approx(0.80)
    # e a janela exposta avança pra do overlay
    assert radar.CORRELACOES_JANELA_FIM > radar.HOJE_SNAPSHOT.isoformat()


def test_overlay_invalido_nao_derruba_o_modulo(tmp_path, monkeypatch):
    destino = str(tmp_path / "quebrado.json")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("{json quebrado")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)

    radar = _carregar_radar_fresco("radar_overlay_quebrado_test")
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.82)
    assert radar.CORRELACOES_JANELA_FIM == radar.HOJE_SNAPSHOT.isoformat()


def test_overlay_ausente_e_silencioso(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "nao_existe.json"))
    radar = _carregar_radar_fresco("radar_overlay_ausente_test")
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.82)


# ── vol medida do overlay chega até o stop/sizing ─────────────────────────

def test_vol_do_overlay_substitui_a_coleta_manual(tmp_path, monkeypatch):
    """O ponto da feature: vol medida errada no snapshot contaminava stop e
    contribuição de risco. Com o overlay, o valor medido prevalece e o
    ticker deixa de ser marcado como estimativa."""
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    # NVDA no snapshot: 1.50%/sem (10.8% a.a., implausível). Medido: 6.0%.
    ac.gravar_overlay({("MU", "SNDK"): 0.8}, path=destino, vols={"NVDA": 6.0})

    radar = _carregar_radar_fresco("radar_vol_overlay_test")
    assert radar.TEMA_IA["NVDA"]["vol_sem"] == pytest.approx(6.0)
    assert radar.TEMA_IA["NVDA"]["est"] is False
    assert radar.VOL_MEDIDA_APLICADA == 1


def test_vol_do_overlay_ignora_ticker_fora_do_tema(tmp_path, monkeypatch):
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    ac.gravar_overlay({("MU", "SNDK"): 0.8}, path=destino, vols={"EWY": 3.0, "MU": 8.0})
    radar = _carregar_radar_fresco("radar_vol_fora_tema_test")
    assert "EWY" not in radar.TEMA_IA
    assert radar.VOL_MEDIDA_APLICADA == 1  # só MU entrou


def test_vol_invalida_no_overlay_mantem_a_manual(tmp_path, monkeypatch):
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    ac.gravar_overlay({("MU", "SNDK"): 0.8}, path=destino,
                      vols={"MU": 0.0, "NVDA": -1.0})
    radar = _carregar_radar_fresco("radar_vol_invalida_test")
    assert radar.TEMA_IA["MU"]["vol_sem"] == pytest.approx(7.89)   # manual
    assert radar.TEMA_IA["NVDA"]["vol_sem"] == pytest.approx(1.50)  # manual
    assert radar.VOL_MEDIDA_APLICADA == 0


def test_overlay_sem_vol_nao_mexe_no_tema(tmp_path, monkeypatch):
    """Overlay antigo (gravado antes desta feature) não tem a chave -- não
    pode quebrar nem zerar a vol existente."""
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    ac.gravar_overlay({("MU", "SNDK"): 0.61}, path=destino)  # vols=None
    radar = _carregar_radar_fresco("radar_sem_vol_test")
    assert radar.TEMA_IA["MU"]["vol_sem"] == pytest.approx(7.89)
    assert radar.VOL_MEDIDA_APLICADA == 0
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.61)  # correlação ok


def test_diagnostico_de_divergencia_nao_cega_no_segundo_refresh(tmp_path, monkeypatch):
    """O defeito que isto trava: divergencias_de_vol comparava contra
    TEMA_IA, que o overlay sobrescreve no import -- do segundo refresh em
    diante a comparação virava "medição nova vs medição anterior" (razão ~1)
    e o erro da coleta ORIGINAL sumia do relatório para sempre."""
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    # 1º refresh: NVDA medido em 5.5 contra 1.50 manual (o caso real, x3.65).
    ac.gravar_overlay({("MU", "SNDK"): 0.8}, path=destino, vols={"NVDA": 5.5})

    radar = _carregar_radar_fresco("radar_divergencia_test")
    assert radar.TEMA_IA["NVDA"]["vol_sem"] == pytest.approx(5.5)
    # o valor manual original fica preservado, não é perdido na sobrescrita
    assert radar.TEMA_IA["NVDA"]["vol_sem_snapshot"] == pytest.approx(1.50)

    # 2º refresh sobre o módulo JÁ com overlay aplicado: a divergência
    # continua sendo reportada contra o manual, não contra a medição de antes
    monkeypatch.setattr(ac, "TEMA_IA", radar.TEMA_IA)
    divs = ac.divergencias_de_vol({"NVDA": 5.6})
    assert divs and divs[0]["ticker"] == "NVDA"
    assert divs[0]["manual"] == pytest.approx(1.50)
    assert divs[0]["razao"] > 3.0


def test_valor_manual_preservado_nao_e_sobrescrito_na_segunda_aplicacao(tmp_path, monkeypatch):
    """Guardar o original só na primeira vez -- senão a segunda aplicação
    salvaria a medição da semana passada como se fosse o manual."""
    destino = str(tmp_path / "overlay.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)
    ac.gravar_overlay({("MU", "SNDK"): 0.8}, path=destino, vols={"NVDA": 5.5})
    radar = _carregar_radar_fresco("radar_preserva_1")
    radar._aplicar_vol_medida({"vol_semanal": {"NVDA": 6.2}})  # 2ª aplicação
    assert radar.TEMA_IA["NVDA"]["vol_sem"] == pytest.approx(6.2)
    assert radar.TEMA_IA["NVDA"]["vol_sem_snapshot"] == pytest.approx(1.50)
