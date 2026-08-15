"""
Testes da integração do Radar IA 2026 em market_alerts.py:
check_earnings_contagion (contágio antecipatório de earnings sobre o
portfólio) e check_sinais_correlacionados (dedup de sinal por cluster).

Sem rede: os dois checks só leem o snapshot embutido de radar_ia_2026.py,
então os testes fixam a data de referência (semana de earnings do snapshot)
e montam Alerts sintéticos -- nenhuma chamada yfinance envolvida.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_market_alerts_radar.py -v
"""
import pytest
from datetime import date

from agent.market_alerts import (
    Alert,
    Category,
    Severity,
    check_earnings_contagion,
    check_sinais_correlacionados,
)


# ── check_earnings_contagion ───────────────────────────────────────────────

def test_contagio_nvda_avisa_posicoes_expostas_na_vespera():
    # 25/08 (ref) -> NVDA reporta 26/08, dentro da janela de 2 dias. Pode
    # haver MAIS eventos na janela (ex.: MRVL 27/08) -- filtra pelo evento.
    alerts = check_earnings_contagion(["SMCI", "AVGO"], today=date(2026, 8, 25))
    do_nvda = [a for a in alerts if "NVDA reporta" in a.detail]
    por_ticker = {a.ticker: a for a in do_nvda}
    assert por_ticker["SMCI"].value == 0.51
    assert por_ticker["AVGO"].value == 0.48


def test_contagio_pula_o_proprio_ticker_do_evento():
    # NVDA no portfólio no dia do earnings dela: check_earnings_proximity já
    # cobre earnings da própria posição -- contágio não deve duplicar.
    alerts = check_earnings_contagion(["NVDA"], today=date(2026, 8, 25))
    assert all(a.ticker != "NVDA" or "NVDA reporta" not in a.detail
               for a in alerts)
    assert not any(a.value == 1.0 for a in alerts)


def test_contagio_sem_earnings_na_janela_fica_vazio():
    # Dia sem nenhum earnings do dataset em [ref, ref+2d].
    assert check_earnings_contagion(["SMCI"], today=date(2027, 6, 1)) == []


def test_contagio_severidade_por_nivel():
    # MU exposto a SNDK (corr 0.82, ALTO -> ATENCAO); SNDK reporta?
    # No dataset SNDK não tem earnings -- usa LRCX 21/10 (corr 0.80 com MU).
    alerts = check_earnings_contagion(["MU"], today=date(2026, 10, 20))
    mu = [a for a in alerts if a.ticker == "MU" and "LRCX" in a.detail]
    assert mu and mu[0].severity == Severity.ATENCAO


# ── check_sinais_correlacionados ───────────────────────────────────────────

def _alerta(ticker: str, severity: Severity) -> Alert:
    return Alert(ticker=ticker, category=Category.EMPRESA, severity=severity,
                 title="t", detail="d")


def test_dedup_aponta_par_do_mesmo_cluster():
    alerts = [_alerta("MU", Severity.CRITICO), _alerta("SNDK", Severity.ATENCAO)]
    extras = check_sinais_correlacionados(alerts)
    assert len(extras) == 1
    assert extras[0].severity == Severity.INFO
    assert "MU" in extras[0].detail and "SNDK" in extras[0].detail
    assert extras[0].value == 0.82


def test_dedup_ignora_alertas_info_e_indices():
    # INFO não conta como sinal relevante; ^IXIC é índice, não posição.
    alerts = [_alerta("MU", Severity.INFO), _alerta("SNDK", Severity.CRITICO),
              _alerta("^IXIC", Severity.CRITICO)]
    assert check_sinais_correlacionados(alerts) == []


def test_dedup_par_de_baixa_correlacao_nao_dispara():
    alerts = [_alerta("MU", Severity.CRITICO), _alerta("CEG", Severity.CRITICO)]
    assert check_sinais_correlacionados(alerts) == []


# ── check_overnight_asia ───────────────────────────────────────────────────

def test_overnight_asia_traduz_queda_da_asia_em_alerta(monkeypatch):
    import agent.market_alerts as ma
    # ^KS11 (proxy EWY) caiu 3% no fechamento coreano; demais indisponíveis.
    monkeypatch.setattr(ma, "_day_change_pct",
                        lambda t: -3.0 if t == "^KS11" else None)
    alerts = ma.check_overnight_asia(["MU"])
    assert len(alerts) == 1
    assert alerts[0].ticker == "MU"
    assert alerts[0].severity == Severity.ATENCAO       # -2.43% -> FORTE
    assert "pressão" in alerts[0].title or "pressao" in alerts[0].title
    assert alerts[0].value == pytest.approx(-2.43, abs=0.01)


def test_overnight_asia_alta_vira_suporte(monkeypatch):
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "_day_change_pct",
                        lambda t: 3.0 if t == "^KS11" else None)
    alerts = ma.check_overnight_asia(["MU"])
    assert "suporte" in alerts[0].title


def test_overnight_asia_ignora_impacto_leve(monkeypatch):
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "_day_change_pct",
                        lambda t: -0.2 if t == "^KS11" else None)
    assert ma.check_overnight_asia(["MU"]) == []


def test_overnight_asia_sem_cotacao_nao_explode(monkeypatch):
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "_day_change_pct", lambda t: None)
    assert ma.check_overnight_asia(["MU"]) == []
