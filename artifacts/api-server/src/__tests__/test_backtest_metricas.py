"""
As métricas de auditoria do backtest (20/08/2026): profit factor, expectancy,
payoff, Sortino, Calmar e o IC de 95% por bootstrap.

O que elas existem para impedir: totalReturn/winRate sozinhos não separam
sorte de edge -- um retorno bonito pode ser um único trade gigante, e um win
rate de 60% em 8 trades não sustenta afirmação nenhuma. Cada teste aqui fixa
a borda em que a métrica mentiria se implementada com pressa (divisão por
zero virando infinito, IC de amostra minúscula, bootstrap não-reproduzível).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_metricas.py -v
"""
import pandas as pd
import pytest

from agent import backtest as bt


def _trades(*pnls):
    return [{"pnl": p} for p in pnls]


# ── _metricas_de_trades ──────────────────────────────────────────────────────

def test_profit_factor_expectancy_e_payoff():
    m = bt._metricas_de_trades(_trades(10.0, 5.0, -5.0))
    assert m["profitFactor"] == pytest.approx(3.0)     # 15 de ganho / 5 de perda
    assert m["expectancy"] == pytest.approx(10 / 3, abs=0.01)
    assert m["payoff"] == pytest.approx(1.5)           # ganho médio 7.5 / perda média 5

def test_sem_perdas_nao_vira_infinito():
    """A borda que mais engana: 100% de acerto em amostra pequena produziria
    profit factor infinito -- lido como excelência exatamente quando menos
    se sabe. None, com o bootstrap avisando da amostra, é a leitura honesta."""
    m = bt._metricas_de_trades(_trades(4.0, 2.0))
    assert m["profitFactor"] is None
    assert m["payoff"] is None
    assert m["expectancy"] == pytest.approx(3.0)

def test_sem_trades_devolve_none_em_tudo():
    m = bt._metricas_de_trades([])
    assert m == {"profitFactor": None, "expectancy": None, "payoff": None}


# ── _bootstrap_dos_trades ────────────────────────────────────────────────────

def test_amostra_pequena_devolve_aviso_nao_intervalo():
    out = bt._bootstrap_dos_trades([5.0] * 9)
    assert "aviso" in out and "9 trades" in out["aviso"]
    assert "compostoIc95" not in out

def test_bootstrap_e_reproduzivel():
    """Semente fixa é requisito de auditoria: o MESMO histórico tem que
    produzir o MESMO intervalo em qualquer máquina, senão a discussão vira
    sobre o gerador de números e não sobre a estratégia."""
    pnls = [3.0, -2.0, 5.0, -1.0, 2.0, -3.0, 4.0, 1.0, -2.5, 6.0, -0.5, 2.5]
    assert bt._bootstrap_dos_trades(pnls) == bt._bootstrap_dos_trades(pnls)

def test_ic_contem_a_estimativa_pontual():
    pnls = [3.0, -2.0, 5.0, -1.0, 2.0, -3.0, 4.0, 1.0, -2.5, 6.0, -0.5, 2.5]
    out = bt._bootstrap_dos_trades(pnls)
    composto = 1.0
    for p in pnls:
        composto *= 1 + p / 100
    composto = (composto - 1) * 100
    lo, hi = out["compostoIc95"]
    assert lo <= composto <= hi
    assert lo < hi  # intervalo degenerado indicaria bug de reamostragem
    wl, wh = out["winRateIc95"]
    assert 0 <= wl <= wh <= 100
    assert out["nTrades"] == 12

def test_trades_identicos_produzem_ic_degenerado_e_correto():
    """Todos os trades iguais: qualquer reamostra é igual à original -- o IC
    colapsa no ponto. É o único caso em que lo == hi é correto."""
    out = bt._bootstrap_dos_trades([2.0] * 15)
    composto = (1.02 ** 15 - 1) * 100
    assert out["compostoIc95"][0] == pytest.approx(composto, abs=0.01)
    assert out["compostoIc95"][0] == out["compostoIc95"][1]
    assert out["winRateIc95"] == [100.0, 100.0]


# ── _bootstrap_das_contribuicoes (a variante de carteira) ────────────────────

def test_contribuicoes_somam_em_vez_de_compor():
    """O defeito que a primeira rodada real expôs (20/08): compor pnls
    inteiros de trades PARALELOS com ~1/n do capital cada deu IC de 302% a
    50.073% numa carteira de +34%. Contribuições em pontos do capital são
    aditivas: reamostrar e SOMAR responde na escala do resultado."""
    contribs = [2.0] * 15  # 15 trades, 2pp cada -> carteira fez +30pp
    out = bt._bootstrap_das_contribuicoes(contribs)
    # Degenerado e correto: todos iguais, o IC colapsa exatamente na soma.
    assert out["contribuicaoIc95"] == [30.0, 30.0]
    assert "compostoIc95" not in out

def test_ic_das_contribuicoes_contem_o_total_e_e_reproduzivel():
    contribs = [9.1, 8.1, 4.2, 3.8, 3.4, 3.0, 1.9, 1.3, 0.9, 0.8, 0.7, -0.2, -0.4, -0.7, -1.6]
    out = bt._bootstrap_das_contribuicoes(contribs)
    lo, hi = out["contribuicaoIc95"]
    assert lo <= sum(contribs) <= hi
    assert lo < hi
    assert out == bt._bootstrap_das_contribuicoes(contribs)

def test_contribuicoes_com_amostra_pequena_avisam():
    assert "aviso" in bt._bootstrap_das_contribuicoes([1.0] * 9)


# ── integração via _simulate ─────────────────────────────────────────────────

def _ohlc_flat(n=30, preco=100.0):
    idx = pd.bdate_range("2026-01-05", periods=n)
    s = pd.Series([preco] * n, index=idx)
    return pd.DataFrame({"open": s, "high": s, "low": s, "close": s})

def _sem_sinal(df):
    return pd.Series(False, index=df.index), pd.Series(False, index=df.index)

def test_simulate_expoe_as_metricas_no_json():
    df = _ohlc_flat()
    buy, sell = _sem_sinal(df)
    res = bt._simulate("TST", "manual", "2026-01-05", "2026-02-13", df, buy, sell,
                       1.0, 0.0, 0.0, None, None)
    # Sem nenhum trade e sem variação: tudo declara ausência, nada inventa.
    assert res["profitFactor"] is None
    assert res["expectancy"] is None
    assert res["sortino"] is None      # nenhum dia negativo
    assert res["calmar"] is None       # drawdown zero
    assert "aviso" in res["bootstrap"]

def test_calmar_negativo_quando_a_estrategia_perde():
    """Estratégia que só perde: CAGR negativo sobre drawdown real -- o Calmar
    tem que carregar o sinal, não o módulo."""
    idx = pd.bdate_range("2026-01-05", periods=30)
    fech = pd.Series([100.0] * 30, index=idx)
    fech.iloc[2:] = 80.0   # compra a 100 (open D+1), o resto do período a 80
    df = pd.DataFrame({"open": fech, "high": fech, "low": fech, "close": fech})
    df.iloc[1] = 100.0     # o open de entrada
    buy = pd.Series(False, index=idx); buy.iloc[0] = True
    sell = pd.Series(False, index=idx)
    res = bt._simulate("TST", "manual", "2026-01-05", "2026-02-13", df, buy, sell,
                       1.0, 0.0, 0.0, None, None)
    assert res["maxDrawdown"] < 0
    assert res["calmar"] is not None and res["calmar"] < 0
