"""
Testes do modo --json de atualizar_correlacoes.py -- o ciclo completo que o
checker semanal (lib/radar-correlacoes-checker.ts) consome.

Sem rede: baixar_fechamentos é mockado. O foco é o CONTRATO com o lado Node
(nunca levantar exceção, sempre devolver ok:true/false, destacar os pares
que cruzaram 0.70) -- se este contrato quebrar, o checker some em silêncio.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_atualizar_correlacoes_json.py -v
"""
import pandas as pd
import pytest

from agent import atualizar_correlacoes as ac


def _fech_sinteticos() -> pd.DataFrame:
    """Duas séries com co-movimento diário perfeito -> correlação 1.0."""
    n = 120
    idx = pd.date_range(end=pd.Timestamp("2026-08-15"), periods=n, freq="B")
    base = [100.0]
    for i in range(n - 1):
        base.append(base[-1] * (1.01 if i % 2 == 0 else 0.995))
    return pd.DataFrame({"MU": base, "SNDK": [v / 2 for v in base]}, index=idx)


def test_json_devolve_ok_e_grava_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "overlay.json"))
    monkeypatch.setattr(ac, "baixar_fechamentos", lambda *a, **k: _fech_sinteticos())
    out = ac.atualizar_e_gravar()
    assert out["ok"] is True
    assert out["pares"] == 1
    assert (tmp_path / "overlay.json").exists()


def test_json_destaca_par_que_cruzou_o_limiar(tmp_path, monkeypatch):
    """MU-SNDK é 0.82 no snapshot; virar 1.0 não cruza, mas cair abaixo de
    0.70 sim -- e é isso que muda dedup/contágio/concentração."""
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "overlay.json"))
    n = 120
    idx = pd.date_range(end=pd.Timestamp("2026-08-15"), periods=n, freq="B")
    a, b = [100.0], [100.0]
    for i in range(n - 1):
        sobe = i % 2 == 0
        a.append(a[-1] * (1.01 if sobe else 0.99))
        b.append(b[-1] * (0.99 if sobe else 1.01))  # anticorrelacionada
    monkeypatch.setattr(ac, "baixar_fechamentos",
                        lambda *x, **k: pd.DataFrame({"MU": a, "SNDK": b}, index=idx))
    out = ac.atualizar_e_gravar()
    assert out["ok"] is True
    assert out["cruzaram_070"]
    cruzou = out["cruzaram_070"][0]
    assert sorted(cruzou["par"]) == ["MU", "SNDK"]
    assert cruzou["de"] == pytest.approx(0.82)
    assert cruzou["para"] < 0.70


def test_json_sem_historico_devolve_erro_sem_gravar(tmp_path, monkeypatch):
    destino = tmp_path / "overlay.json"
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(destino))
    monkeypatch.setattr(ac, "baixar_fechamentos", lambda *a, **k: pd.DataFrame())
    out = ac.atualizar_e_gravar()
    assert out["ok"] is False and "histórico" in out["erro"]
    assert not destino.exists()  # overlay anterior preservado


def test_json_nunca_levanta_excecao(monkeypatch):
    """Contrato com o checker: exceção vira {ok: false}, nunca sobe -- um
    refresh que falhou não pode derrubar o ciclo de background."""
    def explode(*a, **k):
        raise RuntimeError("yfinance pegou fogo")
    monkeypatch.setattr(ac, "baixar_fechamentos", explode)
    out = ac.atualizar_e_gravar()
    assert out["ok"] is False
    assert "RuntimeError" in out["erro"]


def test_json_reporta_tickers_sem_historico(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_CORR_OVERLAY", str(tmp_path / "overlay.json"))
    monkeypatch.setattr(ac, "baixar_fechamentos", lambda *a, **k: _fech_sinteticos())
    out = ac.atualizar_e_gravar()
    # o universo tem dezenas de tickers; o mock só devolveu 2
    assert "NVDA" in out["sem_historico"]
