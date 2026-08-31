"""Classificador de regime de entrada pós-earnings.

Função pura: run-up + gap D+1 + flags qualitativos do call -> regime,
ação sugerida e size relativo. Não dispara ordem.

Calibrado nos 8 prints da AVGO (set/24–jun/26). O preço sozinho não
separa um crash que devolve (teto de IA subiu) de um crash que gruda
(guide de IA abaixo do implícito / 2027 flat / margem ou backlog
fracos). Por isso as flags qualitativas entram no classificador.

Consumidores previstos (ainda não ligados neste commit):
  - earnings_reaction_analysis.analyze_ticker
  - entry_exit_study (bloquear pullback em HOT_DECEPTION)
  - reason_code do Veredito (EARNINGS_REGIME_*)
"""

from __future__ import annotations

from typing import Literal

# Cortes alinhados a RUNUP_ESTICADO_PCT em earnings_reaction_analysis.py
# (10%) e ao miolo "sem drama" da tabela AVGO (~±6% no D+1).
RUNUP_HOT_PCT = 10.0
RUNUP_COLD_PCT = -8.0
GAP_CRASH_PCT = -6.0
GAP_SPIKE_PCT = 6.0
HOLD_PREGOES = 10
FADE_CHECK_PREGOES = 5

Regime = Literal[
    "NEW_CEILING",
    "HOT_DECEPTION",
    "ALREADY_DISCOUNTED",
    "OK_BEAT",
    "LIGHT_MISS_GUIDE",
]

SIZE_RELATIVO: dict[str, float] = {
    "NEW_CEILING": 1.0,
    "ALREADY_DISCOUNTED": 0.75,
    "HOT_DECEPTION": 0.0,
    "OK_BEAT": 0.0,
    "LIGHT_MISS_GUIDE": 0.0,
}

ACAO: dict[str, str] = {
    "NEW_CEILING": "long_d1",
    "ALREADY_DISCOUNTED": "long_bounce_d1",
    "HOT_DECEPTION": "bloquear_d1",
    "OK_BEAT": "nao_entrar",
    "LIGHT_MISS_GUIDE": "esperar_d2_d3",
}


def _truthy(valor: object) -> bool:
    return bool(valor) is True


def classify_earnings_setup(
    runup_pct: float | None,
    gap_d1_pct: float | None,
    qualitative: dict | None = None,
) -> dict:
    """Classifica o setup de entrada.

    qualitative (todos opcionais, default False / "in_line"):
      ai_guide_vs_implied: "above" | "in_line" | "below"
      long_range_raised: bool     # teto de longo prazo subiu (ex. 2027)
      new_customer_or_tam: bool
      margin_guide_down: bool
      backlog_disappointing: bool
    """
    q = qualitative or {}
    guide = q.get("ai_guide_vs_implied") or "in_line"
    ceiling_up = (
        _truthy(q.get("new_customer_or_tam"))
        or _truthy(q.get("long_range_raised"))
        or guide == "above"
    )
    deception = (
        guide == "below"
        or (
            not _truthy(q.get("long_range_raised"))
            and (
                _truthy(q.get("margin_guide_down"))
                or _truthy(q.get("backlog_disappointing"))
            )
        )
    )

    runup = float(runup_pct) if runup_pct is not None else 0.0
    gap = float(gap_d1_pct) if gap_d1_pct is not None else 0.0

    if gap >= GAP_SPIKE_PCT and ceiling_up:
        regime: Regime = "NEW_CEILING"
    elif runup >= RUNUP_HOT_PCT and gap <= GAP_CRASH_PCT and deception:
        regime = "HOT_DECEPTION"
    elif runup <= RUNUP_COLD_PCT and guide != "below":
        regime = "ALREADY_DISCOUNTED"
    elif abs(gap) < abs(GAP_SPIKE_PCT) and not ceiling_up and not deception:
        regime = "OK_BEAT"
    else:
        regime = "LIGHT_MISS_GUIDE"

    size = SIZE_RELATIVO[regime]
    return {
        "regime": regime,
        "acao": ACAO[regime],
        "size_relativo": size,
        "entry_blocked": size <= 0,
        "hold_pregoes": HOLD_PREGOES,
        "fade_check_pregoes": FADE_CHECK_PREGOES,
        "ceiling_up": ceiling_up,
        "deception": deception,
        "runup_pct": runup if runup_pct is not None else None,
        "gap_d1_pct": gap if gap_d1_pct is not None else None,
    }
