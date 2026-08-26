"""
Testes do run-up pré-earnings em earnings_reaction_analysis.py -- o padrão
"bom não é bom o suficiente" (visto em produção ago/2026: SKHY com lucro
recorde caiu ~9% chegando esticada; DELL sem euforia prévia saltou +32%).

Testa as funções puras (_runup_pct e _runup_summary) com DataFrames
sintéticos -- analyze_ticker inteiro depende de yfinance e fica de fora,
mesma linha dos outros testes do repo (validar a matemática, não a rede).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_earnings_reaction_runup.py -v
"""
import os
import sys

import pandas as pd
import pytest

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent.earnings_reaction_analysis import (  # noqa: E402
    RUNUP_PREGOES,
    _runup_pct,
    _runup_summary,
)


def _hist_com_precos(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp("2026-08-12"), periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


# ── _runup_pct ──────────────────────────────────────────────────────────────

def test_runup_pct_mede_do_fechamento_21_pregoes_antes_ate_a_vespera():
    # 100 constante até subir pra 120 na véspera do balanço (posição final).
    closes = [100.0] * (RUNUP_PREGOES + 1) + [120.0]
    hist = _hist_com_precos(closes)
    pos = len(closes)  # balanço seria o pregão seguinte ao último
    assert _runup_pct(hist, pos) == pytest.approx(20.0)


def test_runup_pct_none_quando_historico_nao_alcanca():
    hist = _hist_com_precos([100.0] * 10)  # menos que RUNUP_PREGOES+1
    assert _runup_pct(hist, len(hist)) is None


# ── _runup_summary ──────────────────────────────────────────────────────────

def _df_reacoes(pares: list[tuple[float, float]]) -> pd.DataFrame:
    """pares = [(runup_pct, close_pct da reação), ...]"""
    return pd.DataFrame([{"runup_pct": r, "close_pct": c} for r, c in pares])


HIST_NEUTRO = _hist_com_precos([100.0] * (RUNUP_PREGOES + 5))


def test_runup_summary_conta_esticados_que_cairam_e_descontados_que_subiram():
    df = _df_reacoes([
        (15.0, -8.0),   # esticado -> caiu
        (22.0, -3.0),   # esticado -> caiu
        (12.0, 4.0),    # esticado -> subiu (contra o padrão)
        (-15.0, 9.0),   # descontado -> subiu
        (-12.0, -1.0),  # descontado -> caiu (contra o padrão)
        (5.0, 2.0),     # neutro (não entra em nenhum bucket)
        (-4.0, 3.0),    # NEUTRO: caiu pouco. Antes do corte simétrico isto
                        # contava como "descontado", e era o que inflava o
                        # bucket com papéis apenas planos (25/08/2026).
    ])
    out = _runup_summary(df, HIST_NEUTRO)
    assert out["esticado_n"] == 3
    assert out["esticado_caiu_n"] == 2
    assert out["descontado_n"] == 2
    assert out["descontado_subiu_n"] == 1
    assert out["esticado_reacao_media"] == pytest.approx((-8.0 - 3.0 + 4.0) / 3, abs=0.01)
    # 7 pares -> correlação sai (e o padrão acima é negativo)
    assert out["corr_runup_reacao"] is not None
    assert out["corr_runup_reacao"] < 0


def test_runup_summary_amostra_pequena_nao_inventa_correlacao():
    out = _runup_summary(_df_reacoes([(15.0, -8.0), (-5.0, 9.0), (3.0, 1.0)]), HIST_NEUTRO)
    # 3 pares: buckets saem (contagem é auditável), correlação não (ruído puro)
    assert out["esticado_n"] == 1
    assert out["corr_runup_reacao"] is None


def test_runup_summary_estado_atual_esticado():
    # Sobe de 100 pra 115 nos últimos RUNUP_PREGOES pregões -> +15% >= corte.
    closes = [100.0] * 10 + [100.0] + [115.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["estado_atual"] == "esticado"
    assert out["runup_atual_pct"] == pytest.approx(15.0)


def test_runup_summary_estado_atual_descontado():
    # Cai de 100 pra 88 -> -12%, além do corte de dois dígitos.
    closes = [100.0] * 10 + [100.0] + [88.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["estado_atual"] == "descontado"


def test_queda_de_um_digito_e_neutro_e_nao_descontado():
    """O corte é SIMÉTRICO: -8% é plano, não descontado.

    Era `<= 0`, e por isso qualquer papel que não tivesse subido virava
    "descontado". Não é preciosismo de rótulo -- é o que sustentava a
    conclusão: em 25/08/2026 o AVGO saiu como "descontado em -6,91%" e essa
    linha virou a recomendação principal de quem leu o relatório."""
    closes = [100.0] * 10 + [100.0] + [92.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["runup_atual_pct"] == pytest.approx(-8.0)
    assert out["estado_atual"] == "neutro"


def test_papel_parado_nao_e_descontado():
    """O caso extremo da regra antiga: run-up de exatamente 0% era rotulado
    'descontado' por `<= 0`."""
    closes = [100.0] * (10 + 1 + RUNUP_PREGOES)
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes))
    assert out["runup_atual_pct"] == pytest.approx(0.0)
    assert out["estado_atual"] == "neutro"


def test_runup_summary_sem_eventos_com_runup_so_devolve_estado_atual():
    df = pd.DataFrame([{"close_pct": -3.0}])  # evento antigo sem coluna runup_pct
    out = _runup_summary(df, HIST_NEUTRO)
    assert out["n_com_runup"] == 0
    assert "esticado_n" not in out


# ── janela de run-up contaminada pelo próprio earnings ──────────────────────
#
# A janela de RUNUP_PREGOES termina no ÚLTIMO fechamento, então logo depois de
# um balanço ela engole o pregão de reação. Visto em produção (NBIS,
# 17/08/2026): balanço em 12/08 com +34,14% e, três pregões depois, "run-up
# atual +61,66% (esticado)" -- que é a reação já ocorrida, não a antecipação
# que o indicador mede. Ex-evento o run-up era ~+20,5%.

def _hist_com_earnings_no_fim(salto: float, depois: float, planos: float = 100.0):
    """Histórico plano em `planos`, um pregão de earnings multiplicando por
    `salto`, e mais 3 pregões em `depois`. O earnings cai dentro da janela."""
    closes = [planos] * (RUNUP_PREGOES + 5) + [planos * salto] + [depois] * 3
    hist = _hist_com_precos(closes)
    return hist, len(closes) - 4  # posição do pregão de earnings


def test_runup_atual_sinaliza_quando_a_janela_engole_o_earnings():
    hist, pos_earnings = _hist_com_earnings_no_fim(salto=1.3414, depois=161.66)
    out = _runup_summary(_df_reacoes([]), hist, pos_earnings)

    assert out["janela_contem_earnings"] is True
    assert out["pregoes_desde_earnings"] == 3
    # Bruto continua sendo reportado (é o número que a janela realmente deu)…
    assert out["runup_atual_pct"] == pytest.approx(61.66, abs=0.01)
    # …mas o ex-evento remove SÓ o retorno do pregão do balanço, por composição.
    assert out["runup_atual_ex_evento_pct"] == pytest.approx(20.52, abs=0.05)


def test_estado_atual_sai_do_runup_limpo_nao_do_bruto():
    """O caso que mais importa: sem o balanço, o papel NÃO está esticado.
    Antes da correção o bruto (+34,14%) mandava e saía 'esticado'."""
    hist, pos_earnings = _hist_com_earnings_no_fim(salto=1.3414, depois=134.14)
    out = _runup_summary(_df_reacoes([]), hist, pos_earnings)

    assert out["runup_atual_pct"] == pytest.approx(34.14, abs=0.01)
    assert out["runup_atual_ex_evento_pct"] == pytest.approx(0.0, abs=0.01)
    # Ex-evento o papel está PARADO. A intenção sempre foi "não é esticado";
    # com o corte simétrico o rótulo certo é neutro (antes saía "descontado",
    # porque a regra tratava qualquer não-subida como desconto).
    assert out["estado_atual"] == "neutro"


def test_earnings_fora_da_janela_mantem_o_comportamento_antigo():
    # Sobe 15% nos últimos RUNUP_PREGOES pregões; o earnings é bem antigo.
    closes = [100.0] * 10 + [100.0] + [115.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes), ultimo_earnings_pos=2)

    assert out["janela_contem_earnings"] is False
    assert "runup_atual_ex_evento_pct" not in out
    assert "pregoes_desde_earnings" not in out
    assert out["estado_atual"] == "esticado"
    assert out["runup_atual_pct"] == pytest.approx(15.0)


def test_sem_earnings_conhecido_nao_quebra():
    """Ticker sem earnings casado com pregão: o argumento chega None e o
    cálculo tem que seguir igual ao de antes."""
    closes = [100.0] * 10 + [100.0] + [115.0] * RUNUP_PREGOES
    out = _runup_summary(_df_reacoes([]), _hist_com_precos(closes), None)
    assert out["janela_contem_earnings"] is False
    assert out["estado_atual"] == "esticado"
