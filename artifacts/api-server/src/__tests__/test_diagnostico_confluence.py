"""
A aritmética do diagnóstico do ConfluenceEngine, testada sem rede.

O script roda na VPS (este sandbox e o dev local nem sempre alcançam o
yfinance), então o que dá para fixar aqui é o que NÃO depende de dado vivo: a
decomposição de trades, a atribuição diária e — a borda que mais importa — a
semântica de a qual pregão pertence cada retorno. Errar essa borda desloca a
atribuição em um dia, e em papel que gapa 10% no earnings um dia é a análise
inteira.

Import: o teste insere scripts/ no sys.path e importa o módulo direto. O
import dispara o sys.path.insert do próprio script (agent/ entra no path), o
que normalmente quebraria `from agent.x import y` de qualquer teste coletado
depois — mas o conftest já fixa o PACOTE agent em sys.modules justamente para
esta classe de poluição (ver o comentário lá).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_diagnostico_confluence.py -v
"""
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "agent" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import diagnostico_confluence as diag  # noqa: E402


def _trade(entry, exit=None, direction=1, pnl=None):
    t = {"entry_date": entry, "entry_price": 100.0, "direction": direction, "size_frac": 0.06}
    if exit is not None:
        t.update({"exit_date": exit, "exit_price": 100.0, "pnl_pct": pnl})
    return t


# ── decompor_trades ──────────────────────────────────────────────────────────

def test_composicao_separa_long_de_short():
    """A pergunta central do diagnóstico: num bull market, quanto do estrago
    é o short? Agregado positivo pode esconder short destruindo metade."""
    trades = [
        _trade("2026-01-05", "2026-01-12", 1, 0.10),   # long +10%
        _trade("2026-02-02", "2026-02-09", 1, 0.10),   # long +10%
        _trade("2026-03-02", "2026-03-09", -1, -0.10),  # short -10%
    ]
    d = diag.decompor_trades(trades)
    assert d["n_long"] == 2 and d["n_short"] == 1
    assert d["retorno_cru_long_pct"] == pytest.approx(21.0)      # 1.1*1.1
    assert d["retorno_cru_short_pct"] == pytest.approx(-10.0)
    assert d["retorno_cru_pct"] == pytest.approx(8.9)            # 1.1*1.1*0.9
    assert d["win_rate_long"] == 1.0
    assert d["win_rate_short"] == 0.0


def test_trade_aberto_fica_de_fora_da_conta():
    """Trade sem exit não tem pnl -- entrar na composição seria inventar
    resultado para uma posição que ainda não fechou."""
    d = diag.decompor_trades([_trade("2026-01-05", "2026-01-08", 1, 0.05),
                              _trade("2026-06-01")])
    assert d["n_trades"] == 1


def test_whipsaw_e_medido_pela_duracao():
    trades = [
        _trade("2026-01-05", "2026-01-06", 1, -0.01),   # 1 dia
        _trade("2026-01-08", "2026-01-09", 1, -0.01),   # 1 dia
        _trade("2026-02-02", "2026-02-20", 1, 0.15),    # 18 dias
    ]
    d = diag.decompor_trades(trades)
    assert d["pct_trades_curtos"] == pytest.approx(100 * 2 / 3)
    assert d["duracao_mediana_dias"] == 1


def test_sem_trades_nao_estoura():
    d = diag.decompor_trades([])
    assert d["n_trades"] == 0
    assert d["retorno_cru_pct"] == 0.0
    assert d["win_rate_long"] is None


# ── serie_de_posicao: a borda que decide a análise ───────────────────────────

def _indice(n=10, inicio="2026-01-05"):
    return pd.bdate_range(inicio, periods=n)


def test_dia_da_entrada_nao_recebe_o_retorno():
    """A entrada é na ABERTURA do dia (run_backtest executa em D+1 desde
    20/08/2026), mas o retorno close-a-close daquele pregão inclui o gap
    noturno ANTERIOR à posição. Atribuí-lo inteiro à estratégia daria crédito
    por um gap que ela não capturou -- a exclusão é a borda conservadora."""
    idx = _indice()
    pos = diag.serie_de_posicao(idx, [_trade(str(idx[2])[:10], str(idx[5])[:10], 1, 0.05)])
    assert pos[idx[2]] == 0     # dia da entrada: fora
    assert pos[idx[3]] == 1     # primeiro retorno capturado
    assert pos[idx[5]] == 1     # dia da saída: o retorno até o fechamento é da posição
    assert pos[idx[6]] == 0


def test_trade_aberto_vai_ate_o_fim_da_serie():
    idx = _indice()
    pos = diag.serie_de_posicao(idx, [_trade(str(idx[7])[:10], direction=-1)])
    assert pos[idx[7]] == 0
    assert pos[idx[8]] == -1 and pos[idx[9]] == -1


# ── atribuicao_diaria ────────────────────────────────────────────────────────

def test_rali_fora_da_posicao_aparece_na_conta_de_fora():
    """O caso que o diagnóstico existe para pegar: o ativo dobra num período
    em que a estratégia estava FORA. O composto dos dias fora tem que carregar
    o rali; o dos dias comprados, ficar no zero."""
    idx = _indice(6)
    close = pd.Series([100, 100, 100, 200, 200, 200], index=idx, dtype=float)
    df = pd.DataFrame({"close": close})
    pos = pd.Series([0, 1, 0, 0, 1, 1], index=idx)  # fora no dia do +100%
    a = diag.atribuicao_diaria(df, pos)
    assert a["ativo_enquanto_fora_pct"] == pytest.approx(100.0)
    assert a["ativo_enquanto_comprado_pct"] == pytest.approx(0.0)
    assert a["melhores_dias"][0]["data"] == str(idx[3])[:10]
    assert a["melhores_dias"][0]["posicao"] == 0


def test_percentuais_de_exposicao_fecham_100():
    idx = _indice(21)
    df = pd.DataFrame({"close": pd.Series(np.linspace(100, 120, 21), index=idx)})
    pos = pd.Series(([1] * 7) + ([0] * 7) + ([-1] * 7), index=idx)
    a = diag.atribuicao_diaria(df, pos)
    total = a["pct_dias_comprado"] + a["pct_dias_vendido"] + a["pct_dias_fora"]
    assert total == pytest.approx(100.0)


# ── equity_hipotetica: sizing isolado do sinal ───────────────────────────────

def test_frac_1_reproduz_o_retorno_cru():
    trades = [_trade("2026-01-05", "2026-01-12", 1, 0.10),
              _trade("2026-02-02", "2026-02-09", 1, -0.05)]
    assert diag.equity_hipotetica(trades, 1.0) == pytest.approx((1.10 * 0.95 - 1) * 100)


def test_frac_pequena_esmaga_o_mesmo_sinal():
    """O mecanismo do headline: os MESMOS trades, 6% do capital. +10% de
    trade vira +0,6% de equity -- a distância entre 'sinal ruim' e 'sizing
    minúsculo' está inteira nesta função."""
    trades = [_trade("2026-01-05", "2026-01-12", 1, 0.10)]
    assert diag.equity_hipotetica(trades, 0.06) == pytest.approx(0.6)
    assert diag.equity_hipotetica(trades, 0.0) == 0.0
