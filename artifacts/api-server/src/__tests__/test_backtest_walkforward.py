"""
Testes do walk-forward / out-of-sample em backtest.py.

O que está sendo protegido aqui não é "o backtest roda", e sim a propriedade
que dá sentido ao modo: o parâmetro é escolhido numa janela e o resultado é
medido em OUTRA, que o otimizador nunca viu. Um teste que só conferisse
"devolve números" passaria mesmo se a implementação medisse in-sample por
engano -- exatamente o defeito que este modo existe pra corrigir.

Sem rede: _fetch_warmed_close é mockado com séries sintéticas.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_backtest_walkforward.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import backtest as bt


def _serie(n: int, seed: int = 0, drift: float = 0.0003, vol: float = 0.02) -> pd.Series:
    rng = np.random.default_rng(seed)
    precos = 100 * np.cumprod(1 + drift + rng.normal(0, vol, n))
    idx = pd.date_range(end=pd.Timestamp("2026-08-14"), periods=n, freq="B")
    return pd.Series(precos, index=idx)


# ── janelas ────────────────────────────────────────────────────────────────

def test_janelas_nao_sobrepoem_o_teste():
    """Teste sobreposto contaria o mesmo pregão mais de uma vez no agregado
    e inflaria a confiança no resultado."""
    janelas = bt._janelas_walk_forward(500, treino=252, teste=63)
    testes = [(ini, fim) for _, _, ini, fim in janelas]
    for (ini_a, fim_a), (ini_b, _) in zip(testes, testes[1:]):
        assert fim_a <= ini_b, "janelas de teste se sobrepõem"


def test_treino_sempre_vem_antes_do_teste():
    """A propriedade central: nenhum pregão do teste pode estar no treino."""
    for ini_tr, fim_tr, ini_te, fim_te in bt._janelas_walk_forward(800, 252, 63):
        assert fim_tr <= ini_te
        assert ini_tr < fim_tr < fim_te


def test_periodo_curto_nao_gera_janela():
    assert bt._janelas_walk_forward(200, treino=252, teste=63) == []


def test_janela_exata_gera_um_fold():
    assert len(bt._janelas_walk_forward(315, treino=252, teste=63)) == 1


# ── grade de parâmetros ────────────────────────────────────────────────────

def test_grade_rsi_descarta_combinacao_sem_sentido():
    combos = bt._combos_de_params("rsi")
    assert combos
    for c in combos:
        assert c["rsi_overbought"] > c["rsi_oversold"]


def test_estrategia_sem_parametro_devolve_combinacao_vazia():
    """ma_cross não tem parâmetro exposto -- vira medição out-of-sample pura,
    sem fingir que houve otimização."""
    assert bt._combos_de_params("ma_cross") == [{}]


def test_objetivo_ignora_janela_sem_negocio():
    """Sem trade não há o que otimizar; tratar como 0 faria a busca escolher
    um parâmetro qualquer por empate."""
    assert bt._metrica_objetivo({"totalTrades": 0, "sharpe": 1.5}, "sharpe") is None
    assert bt._metrica_objetivo({"error": "x"}, "sharpe") is None
    assert bt._metrica_objetivo({"totalTrades": 3, "sharpe": 1.5}, "sharpe") == 1.5


# ── run_walk_forward (integração, com fetch mockado) ──────────────────────

def test_periodo_curto_devolve_erro_explicativo(monkeypatch):
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(120), None))
    out = bt.run_walk_forward("XXX", "2026-01-01", "2026-08-14")
    assert "error" in out and "curto demais" in out["error"]


def test_walk_forward_mede_no_periodo_que_nao_otimizou(monkeypatch):
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=7), None))
    out = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14",
                              treino_pregoes=252, teste_pregoes=63)
    assert "error" not in out
    folds = [f for f in out["folds"] if "inSample" in f]
    assert folds, "nenhum fold produziu resultado"
    for f in folds:
        # A garantia que importa: a janela de teste começa DEPOIS do fim da
        # janela em que o parâmetro foi escolhido.
        assert f["testeInicio"] > f["treinoFim"]


def test_resumo_reporta_degradacao(monkeypatch):
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=11), None))
    out = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14")
    r = out["resumo"]
    assert r["nFolds"] > 0
    if r["retornoMedioInSample"] is not None and r["retornoMedioOutOfSample"] is not None:
        assert r["degradacao"] == pytest.approx(
            r["retornoMedioInSample"] - r["retornoMedioOutOfSample"], abs=0.02)


def test_resumo_mede_estabilidade_do_parametro(monkeypatch):
    """Parâmetro diferente a cada janela indica busca perseguindo ruído --
    vale mais que qualquer retorno bonito, então tem que ser reportado."""
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=3), None))
    out = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14")
    r = out["resumo"]
    assert "parametrosDistintosEscolhidos" in r
    assert r["parametroEstavel"] == (r["parametrosDistintosEscolhidos"] == 1)


def test_resumo_confronta_buy_and_hold_nas_mesmas_janelas(monkeypatch):
    """Retorno positivo pode ser só o mercado subindo -- sem o confronto na
    MESMA janela de teste, o número engana."""
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=5), None))
    r = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14")["resumo"]
    assert "buyAndHoldMedioOutOfSample" in r
    assert "foldsQueVenceramBuyHold" in r
    assert r["foldsQueVenceramBuyHold"] <= r["nFolds"]


def test_estrategia_sem_parametro_roda_sem_otimizar(monkeypatch):
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=9), None))
    out = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14", strategy="ma_cross")
    assert out["combinacoesTestadas"] == 1
    assert out["resumo"]["parametroEstavel"] is True


def test_fold_sem_sinal_no_treino_e_registrado_nao_escondido(monkeypatch):
    """Se nenhuma combinação negociou no treino, o fold precisa aparecer
    marcado -- escolher um parâmetro à toa e chamar de otimizado seria pior
    que não ter o fold."""
    monkeypatch.setattr(bt, "_fetch_warmed_close", lambda *a, **k: (_serie(700, seed=13), None))
    # RSI impossível de disparar: nunca há trade no treino.
    monkeypatch.setattr(bt, "_RSI_OVERSOLD_GRID", (0.5,))
    monkeypatch.setattr(bt, "_RSI_OVERBOUGHT_GRID", (99.9,))
    out = bt.run_walk_forward("XXX", "2024-01-01", "2026-08-14")
    assert all(f.get("semSinalNoTreino") for f in out["folds"])
    assert out["resumo"]["nFolds"] == 0
    assert out["resumo"]["foldsSemSinalNoTreino"] > 0
