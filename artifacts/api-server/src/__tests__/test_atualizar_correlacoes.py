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
import sys

import numpy as np
import pandas as pd
import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import atualizar_correlacoes as ac  # noqa: E402


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
