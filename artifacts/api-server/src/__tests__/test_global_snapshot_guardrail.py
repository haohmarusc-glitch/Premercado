"""
Testes do guardrail do snapshot global (market_alerts.py::_day_change_detail
e get_global_market_snapshot).

Motivação: em 02/08 o snapshot reportou KOSPI +17,9% num pregão -- impossível
num índice amplo (circuit breaker da KRX interrompe bem antes) e o número foi
parar no relatório como se fosse fato. `_day_change_pct` pega os dois últimos
closes disponíveis de period="5d" sem olhar data, então um buraco no histórico
faz a "variação do dia" atravessar sessões em silêncio.

O guardrail não substitui o número (não temos como saber o certo) -- ele marca
`suspect` com o motivo, pra não ser citado como fato. Mesma postura do
fail-open das fontes de notícia: degradar com aviso, não sumir com o dado.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_global_snapshot_guardrail.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import pandas as pd
import pytest

from agent import market_alerts as ma


@pytest.fixture(autouse=True)
def _limpa_cache():
    ma._HIST_CACHE.clear()
    yield
    ma._HIST_CACHE.clear()


def _df(closes: list[float], datas: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(datas))


def _mock_history(monkeypatch, df):
    monkeypatch.setattr(ma, "_history", lambda ticker, period="1y": df)


def test_pregoes_vizinhos_sem_suspeita(monkeypatch):
    _mock_history(monkeypatch, _df([100.0, 101.5], ["2026-07-31", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["changePct"] == 1.5
    assert d["suspect"] is False
    assert d["asOf"] == "2026-08-01"
    assert d["prevBarDate"] == "2026-07-31"
    assert d["sessionGapDays"] == 1


def test_fim_de_semana_nao_e_suspeito(monkeypatch):
    """Sexta -> segunda são 3 dias e continuam sendo pregões vizinhos."""
    _mock_history(monkeypatch, _df([100.0, 101.0], ["2026-07-31", "2026-08-03"]))
    d = ma._day_change_detail("^KS11")
    assert d["sessionGapDays"] == 3
    assert d["suspect"] is False


def test_gap_longo_marca_suspeito(monkeypatch):
    """Buraco no histórico: a comparação atravessa sessões não vizinhas."""
    _mock_history(monkeypatch, _df([100.0, 104.0], ["2026-07-20", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["suspect"] is True
    assert "12 dias" in d["suspectReason"]


def test_movimento_implausivel_marca_suspeito(monkeypatch):
    """O caso KOSPI +17,9%: barras vizinhas, mas o número é impossível."""
    _mock_history(monkeypatch, _df([100.0, 117.9], ["2026-07-31", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["changePct"] == 17.9
    assert d["suspect"] is True
    assert "excede o maximo plausivel" in d["suspectReason"]


def test_queda_implausivel_tambem_marca(monkeypatch):
    """O teto é sobre o módulo -- queda de -17,9% é igualmente impossível."""
    _mock_history(monkeypatch, _df([100.0, 82.1], ["2026-07-31", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["suspect"] is True


def test_movimento_no_limite_nao_marca(monkeypatch):
    """8% exatos não disparam; o gate é estritamente acima do limite."""
    _mock_history(monkeypatch, _df([100.0, 108.0], ["2026-07-31", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["changePct"] == 8.0
    assert d["suspect"] is False


def test_dois_motivos_aparecem_juntos(monkeypatch):
    _mock_history(monkeypatch, _df([100.0, 120.0], ["2026-07-10", "2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["suspectReason"].count(";") == 1


def test_historico_insuficiente(monkeypatch):
    _mock_history(monkeypatch, _df([100.0], ["2026-08-01"]))
    d = ma._day_change_detail("^KS11")
    assert d["changePct"] is None
    assert d["suspect"] is False


def test_sem_historico(monkeypatch):
    monkeypatch.setattr(ma, "_history", lambda ticker, period="1y": None)
    d = ma._day_change_detail("^KS11")
    assert d["changePct"] is None


def test_snapshot_agrega_aviso_dos_suspeitos(monkeypatch):
    _mock_history(monkeypatch, _df([100.0, 117.9], ["2026-07-31", "2026-08-01"]))
    snap = ma.get_global_market_snapshot()
    assert "warning" in snap
    assert "NAO cite" in snap["warning"]
    assert all(i["suspect"] for i in snap["items"])


def test_snapshot_sem_suspeitos_nao_tem_warning(monkeypatch):
    _mock_history(monkeypatch, _df([100.0, 100.5], ["2026-07-31", "2026-08-01"]))
    snap = ma.get_global_market_snapshot()
    assert "warning" not in snap
    assert len(snap["items"]) == len(ma.GLOBAL_MARKETS)


def test_day_change_pct_original_intacta(monkeypatch):
    """_day_change_pct tem 8 chamadores (inclusive ação individual, onde +20%
    num dia de earnings é real) -- ela não pode ganhar teto nenhum."""
    _mock_history(monkeypatch, _df([100.0, 125.0], ["2026-07-31", "2026-08-01"]))
    assert ma._day_change_pct("SMCI") == 25.0
