"""Testes puros do classificador de entrada pós-earnings.

Os pares (run-up, gap D+1, flags) reproduzem os 8 prints da AVGO
usados para calibrar os cortes — sem rede.

Rodar (da raiz do repo):
  pytest artifacts/api-server/src/__tests__/test_earnings_entry_regime.py -v
"""
import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from agent.earnings_entry_regime import (  # noqa: E402
    classify_earnings_setup,
)


def test_dez24_new_ceiling_google_tpu():
    out = classify_earnings_setup(
        4.0,
        24.5,
        {
            "ai_guide_vs_implied": "above",
            "new_customer_or_tam": True,
            "long_range_raised": True,
        },
    )
    assert out["regime"] == "NEW_CEILING"
    assert out["entry_blocked"] is False
    assert out["size_relativo"] == 1.0
    assert out["acao"] == "long_d1"


def test_set25_new_ceiling_openai_100bi():
    out = classify_earnings_setup(
        2.0,
        9.6,
        {
            "new_customer_or_tam": True,
            "long_range_raised": True,
            "ai_guide_vs_implied": "above",
        },
    )
    assert out["regime"] == "NEW_CEILING"


def test_dez25_hot_deception_nao_compra_dip():
    out = classify_earnings_setup(
        14.0,
        -11.4,
        {
            "ai_guide_vs_implied": "below",
            "long_range_raised": False,
            "margin_guide_down": True,
        },
    )
    assert out["regime"] == "HOT_DECEPTION"
    assert out["entry_blocked"] is True
    assert out["size_relativo"] == 0.0
    assert out["acao"] == "bloquear_d1"


def test_jun26_hot_deception_2027_flat():
    out = classify_earnings_setup(
        11.0,
        -10.5,
        {
            "ai_guide_vs_implied": "below",
            "long_range_raised": False,
            "backlog_disappointing": True,
        },
    )
    assert out["regime"] == "HOT_DECEPTION"
    assert out["entry_blocked"] is True


def test_mar25_already_discounted_compra_bounce():
    out = classify_earnings_setup(
        -10.0,
        13.0,
        {"ai_guide_vs_implied": "in_line"},
    )
    assert out["regime"] == "ALREADY_DISCOUNTED"
    assert out["entry_blocked"] is False
    assert out["size_relativo"] == 0.75
    assert out["acao"] == "long_bounce_d1"


def test_jun25_ok_beat_nao_entra():
    out = classify_earnings_setup(
        3.0,
        3.0,
        {"ai_guide_vs_implied": "in_line"},
    )
    assert out["regime"] == "OK_BEAT"
    assert out["entry_blocked"] is True
    assert out["acao"] == "nao_entrar"


def test_set24_light_miss_espera():
    out = classify_earnings_setup(
        1.0,
        -6.5,
        {
            "ai_guide_vs_implied": "below",
            "long_range_raised": False,
            "margin_guide_down": False,
            "backlog_disappointing": False,
        },
    )
    assert out["regime"] == "LIGHT_MISS_GUIDE"
    assert out["acao"] == "esperar_d2_d3"
    assert out["entry_blocked"] is True


def test_crash_sem_flag_qualitativa_nao_vira_hot_deception():
    """Sem deception no call, um gap de -7% com run-up quente não
    pode ser tratado como o caso dez/25 — falta o teto que não subiu."""
    out = classify_earnings_setup(12.0, -7.0, {"ai_guide_vs_implied": "in_line"})
    assert out["regime"] != "HOT_DECEPTION"


def test_none_nao_quebra():
    out = classify_earnings_setup(None, None, None)
    assert out["regime"] == "OK_BEAT"
    assert out["runup_pct"] is None
    assert out["gap_d1_pct"] is None
