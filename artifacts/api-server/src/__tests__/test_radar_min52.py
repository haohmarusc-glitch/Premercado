"""
Radar IA: preço/52 semanas vivos, dados manuais com carimbo, sanidade.

Auditoria 17/08/2026. O radar servia um snapshot manual de 12-14/08 como se
fosse dado de hoje, e isso produziu duas classes de erro:

  ERRO DE FORMA   PDD com preco 84,5 ABAIXO da própria min52 87,11 --
                  impossível por construção. Ninguém percebeu porque nada
                  conferia, e número impossível com cara de calculado não
                  levanta suspeita.
  ERRO DE VALOR   a min52 real do PDD era 71,94, não 87,11.

Agora: preço e faixa de 52 semanas vêm vivos do market_data_provider; EVR e
move implícito (coleta humana no OptionSlam, sem API) moram em
dados/radar_overrides.json com `coletado_em` obrigatório; e validar_min52
recusa o dado vivo quando ele sai impossível.

A regra que amarra tudo: degradação NUNCA é silenciosa. Ticker que não
atualizou mantém o snapshot, mas aparece em fontesDegradadas.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_radar_min52.py -v
"""
from datetime import date

import pandas as pd
import pytest

from agent import radar_ia_2026 as radar


# ── validação de sanidade ───────────────────────────────────────────────────

def test_preco_abaixo_da_minima_e_impossivel():
    """O caso exato da auditoria."""
    avisos = radar.validar_min52({"preco": 84.5, "min52": 87.11, "max52": 150.0})
    assert len(avisos) == 1
    assert "abaixo da mínima" in avisos[0]


def test_preco_acima_da_maxima_e_impossivel():
    avisos = radar.validar_min52({"preco": 200.0, "min52": 80.0, "max52": 150.0})
    assert any("acima da máxima" in a for a in avisos)


def test_minima_maior_que_maxima_e_impossivel():
    avisos = radar.validar_min52({"preco": 100.0, "min52": 150.0, "max52": 80.0})
    assert any("maior que a máxima" in a for a in avisos)


def test_linha_coerente_nao_gera_aviso():
    assert radar.validar_min52({"preco": 100.0, "min52": 71.94, "max52": 150.0}) == []


def test_campo_ausente_nao_inventa_aviso():
    """Dado parcial não é dado inconsistente — só falta informação."""
    assert radar.validar_min52({"preco": 100.0}) == []
    assert radar.validar_min52({}) == []


# ── classificação de status ─────────────────────────────────────────────────

def test_status_segue_a_distancia_da_minima():
    assert radar._status_min52(105.0, 100.0) == "dentro"       # +5%
    assert radar._status_min52(110.0, 100.0) == "dentro"       # +10%, na borda
    assert radar._status_min52(115.0, 100.0) == "borderline"   # +15%
    assert radar._status_min52(121.0, 100.0) == "fora"         # +21%


# ── busca viva ──────────────────────────────────────────────────────────────

def _serie(low: float, high: float, close: float) -> pd.DataFrame:
    idx = pd.date_range("2025-08-18", periods=252, freq="B")
    meio = (low + high) / 2
    df = pd.DataFrame(
        {"Open": meio, "High": meio, "Low": meio, "Close": meio, "Volume": 1e6},
        index=idx,
    )
    df.iloc[0, df.columns.get_loc("Low")] = low     # mínima em algum ponto do ano
    df.iloc[1, df.columns.get_loc("High")] = high
    df.iloc[-1, df.columns.get_loc("Close")] = close
    return df


class _Res:
    def __init__(self, df, source="yfinance", is_stale=False):
        self.df, self.source, self.is_stale = df, source, is_stale

    @property
    def ok(self):
        return self.df is not None and not self.df.empty


@pytest.fixture
def snapshot_original(monkeypatch):
    """MIN52 é módulo-global e a função o sobrescreve — sem isolar, um teste
    contamina o seguinte."""
    original = {k: dict(v) for k, v in radar.MIN52.items()}
    yield original
    radar.MIN52.clear()
    radar.MIN52.update(original)


def test_sobrescreve_o_snapshot_com_valores_vivos(monkeypatch, snapshot_original):
    monkeypatch.setattr(
        radar.market_data_provider, "get_daily_history",
        lambda t, p, **k: _Res(_serie(low=71.94, high=155.67, close=84.5)),
    )
    out = radar.atualizar_min52_vivo(["PDD"])

    assert out["atualizados"] == ["PDD"]
    assert out["fontesDegradadas"] == {}
    assert radar.MIN52["PDD"]["min52"] == pytest.approx(71.94)
    assert radar.MIN52["PDD"]["max52"] == pytest.approx(155.67)
    # E o dado que era impossível deixa de ser: 84,5 acima de 71,94.
    assert radar.validar_min52(radar.MIN52["PDD"]) == []


def test_fonte_fora_do_ar_mantem_o_snapshot_e_anuncia(monkeypatch, snapshot_original):
    """O ponto da tarefa: degradar em silêncio para o valor velho é o bug."""
    monkeypatch.setattr(
        radar.market_data_provider, "get_daily_history",
        lambda t, p, **k: _Res(None),
    )
    out = radar.atualizar_min52_vivo(["PDD"])

    assert out["atualizados"] == []
    assert "PDD" in out["fontesDegradadas"]
    assert radar.MIN52["PDD"] == snapshot_original["PDD"]  # intacto


def test_excecao_da_fonte_nao_derruba_o_radar(monkeypatch, snapshot_original):
    def explode(*a, **k):
        raise RuntimeError("rede fora")

    monkeypatch.setattr(radar.market_data_provider, "get_daily_history", explode)
    out = radar.atualizar_min52_vivo(["PDD"])

    assert out["fontesDegradadas"]["PDD"].startswith("erro:")
    assert radar.MIN52["PDD"] == snapshot_original["PDD"]


def test_dado_vivo_inconsistente_nao_sobrescreve(monkeypatch, snapshot_original):
    """Vivo e impossível é PIOR que velho e reconhecidamente velho — o
    snapshot ao menos se sabe antigo."""
    monkeypatch.setattr(
        radar.market_data_provider, "get_daily_history",
        lambda t, p, **k: _Res(_serie(low=90.0, high=150.0, close=84.5)),  # close < low
    )
    out = radar.atualizar_min52_vivo(["PDD"])

    assert out["atualizados"] == []
    assert any("abaixo da mínima" in a for a in out["avisos"])
    assert "PDD" in out["fontesDegradadas"]
    assert radar.MIN52["PDD"] == snapshot_original["PDD"]


def test_cache_vencido_atualiza_mas_marca_degradado(monkeypatch, snapshot_original):
    monkeypatch.setattr(
        radar.market_data_provider, "get_daily_history",
        lambda t, p, **k: _Res(_serie(71.94, 155.67, 84.5), source="yfinance_stale", is_stale=True),
    )
    out = radar.atualizar_min52_vivo(["PDD"])

    assert out["atualizados"] == ["PDD"]          # o dado serve
    assert out["fontesDegradadas"]["PDD"] == "yfinance_stale"  # mas vem marcado


# ── carimbo dos dados manuais ───────────────────────────────────────────────

def test_overrides_trazem_carimbo_e_fonte():
    assert radar.OVERRIDES_COLETADO_EM, "coletado_em é obrigatório"
    assert radar.OVERRIDES_FONTE
    assert "PDD" in radar.REACAO_EARNINGS


def test_idade_dos_overrides_e_contada_em_dias():
    assert radar.idade_overrides_dias(ref=date(2026, 8, 20)) == 6
    assert radar.idade_overrides_dias(ref=date(2026, 8, 14)) == 0
