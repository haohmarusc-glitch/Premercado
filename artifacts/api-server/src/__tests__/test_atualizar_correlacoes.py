"""
Testes de atualizar_correlacoes.py -- refresh das correlações do Radar IA
via Alpha Vantage ANALYTICS_FIXED_WINDOW + overlay carregado por
radar_ia_2026.py no import.

Sem rede: o parser é testado com respostas sintéticas (matriz triangular e
completa, envelopes diferentes), a busca com SESSION mockada, e o ciclo
overlay gravar->carregar com arquivo em tmp_path + RADAR_CORR_OVERLAY.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_atualizar_correlacoes.py -v
"""
import importlib.util
import json
import os
import sys

import pytest

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import atualizar_correlacoes as ac  # noqa: E402


# ── extrair_correlacoes ────────────────────────────────────────────────────

RESPOSTA_TRIANGULAR = {
    "meta_data": {"symbols": "NVDA,MU,SMCI"},
    "payload": {
        "RETURNED_DATA": [{
            "CALCULATION": "CORRELATION",
            "CORRELATION": {
                "index": ["NVDA", "MU", "SMCI"],
                "correlation": [
                    [1.0],
                    [0.44, 1.0],
                    [0.51, 0.43, 1.0],
                ],
            },
        }],
    },
}


def test_extrai_matriz_triangular():
    pares = ac.extrair_correlacoes(RESPOSTA_TRIANGULAR)
    assert pares[("MU", "NVDA")] == pytest.approx(0.44)
    assert pares[("NVDA", "SMCI")] == pytest.approx(0.51)
    assert pares[("MU", "SMCI")] == pytest.approx(0.43)
    # diagonal (1.0 consigo mesmo) nunca vira par
    assert all(a != b for a, b in pares)


def test_extrai_matriz_completa_e_envelope_diferente():
    # Envelope raso (sem payload/RETURNED_DATA) e matriz n x n completa --
    # o parser acha o bloco por forma, não por caminho fixo.
    resposta = {
        "index": ["MU", "SNDK"],
        "correlation": [[1.0, 0.85], [0.85, 1.0]],
    }
    pares = ac.extrair_correlacoes(resposta)
    assert pares == {("MU", "SNDK"): pytest.approx(0.85)}


def test_resposta_sem_matriz_devolve_vazio():
    assert ac.extrair_correlacoes({"Note": "rate limit"}) == {}


# ── buscar_lote (SESSION mockada) ──────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_buscar_lote_detecta_aviso_de_rate_limit(monkeypatch):
    # Alpha Vantage devolve 200 com "Note" quando estoura o limite -- tem
    # que virar erro, não overlay vazio gravado como se fosse dado.
    monkeypatch.setattr(ac.SESSION, "get",
                        lambda *a, **k: _FakeResp({"Note": "API call frequency exceeded"}))
    with pytest.raises(RuntimeError, match="Alpha Vantage"):
        ac.buscar_lote(["NVDA", "MU"], "chave")


def test_atualizar_acumula_parcial_quando_um_lote_falha(monkeypatch):
    chamadas = []

    def fake_get(url, params=None, timeout=None):
        chamadas.append(params["SYMBOLS"])
        if "SMCI" in params["SYMBOLS"]:
            return _FakeResp({"Error Message": "invalid symbol"})
        return _FakeResp(RESPOSTA_TRIANGULAR)

    monkeypatch.setattr(ac.SESSION, "get", fake_get)
    pares, erros = ac.atualizar("chave", [["NVDA", "MU"], ["SMCI", "X"]], pausa_s=0)
    assert len(pares) == 3          # o lote bom entrou
    assert len(erros) == 1          # o ruim foi reportado, não engolido
    assert len(chamadas) == 2


# ── ciclo overlay: gravar -> radar carrega no import ───────────────────────

def _carregar_radar_fresco(nome: str):
    """Carrega uma instância NOVA de radar_ia_2026 (sem tocar a já importada
    pelos outros testes) pra exercitar o overlay do import."""
    spec = importlib.util.spec_from_file_location(
        nome, os.path.join(_AGENT_DIR, "radar_ia_2026.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_overlay_gravado_e_aplicado_no_import(tmp_path, monkeypatch):
    destino = str(tmp_path / "radar_correlacoes.json")
    monkeypatch.setenv("RADAR_CORR_OVERLAY", destino)

    # MU-SNDK era 0.82 no snapshot; overlay diz 0.61 e traz um par novo.
    ac.gravar_overlay({("MU", "SNDK"): 0.61, ("AAPL", "NVDA"): 0.33}, destino)

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
    # snapshot embutido intacto
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.82)
    assert radar.CORRELACOES_JANELA_FIM == radar.HOJE_SNAPSHOT.isoformat()


def test_overlay_ausente_e_silencioso(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "nao_existe.json"))
    radar = _carregar_radar_fresco("radar_overlay_ausente_test")
    assert radar.correlacao("MU", "SNDK") == pytest.approx(0.82)
