"""
agent_scan.py
-------------
Orquestrador: roda a varredura COMPLETA numa passada só.

    1. Alertas da carteira  (alta/baixa vs. preço médio + revisão de 30 dias)
    2. Contágio setorial    (líder + vizinhos por camada)
    3. (opcional) Contexto de notícias p/ a Claude classificar

Pensado pra rodar uma vez por ciclo no cron do GitHub Actions.

Estado (o que já foi disparado / revisado) é persistido em JSON por padrão.
Para migrar pro Supabase, basta reimplementar load_state / save_state.

Uso:
    python agent_scan.py
Saída:
    - imprime/loga os alertas
    - código de saída 0 sempre que rodar limpo (mesmo sem alertas)
    - código 1 se algo quebrar (pro Actions marcar o job como falho)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from portfolio import (
    check_pl_alerts,
    check_holding_review,
    build_news_review_context,
    portfolio_summary,
)
from sector_contagion import detect_contagion

logger = logging.getLogger("agent_scan")

STATE_PATH = Path(os.environ.get("AGENT_STATE_PATH", "agent_state.json"))


# ---------------------------------------------------------------------------
# Persistência de estado (JSON por padrão; troque por Supabase quando migrar)
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Estado corrompido, recomeçando do zero: %s", exc)
    return {"fired_state": {}, "reviewed": []}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Falha ao salvar estado: %s", exc)


# ---------------------------------------------------------------------------
# Varredura completa
# ---------------------------------------------------------------------------
def run_full_scan(
    news_by_ticker: dict | None = None,
    contagion_period: str = "5d",
    contagion_interval: str = "1d",
) -> dict:
    """
    Roda carteira + contágio (+ contexto de notícias se fornecido).
    Retorna um dict pronto pra logar/salvar/notificar.
    """
    state = load_state()
    all_alerts: list[dict] = []

    # 1) Carteira: alta/baixa
    pl_alerts, state["fired_state"] = check_pl_alerts(
        fired_state=state.get("fired_state", {})
    )
    all_alerts += [a.to_dict() for a in pl_alerts]

    # 2) Carteira: revisão de 30 dias (reviewed vira set internamente)
    review_alerts, reviewed_set = check_holding_review(
        reviewed=set(state.get("reviewed", []))
    )
    state["reviewed"] = sorted(reviewed_set)
    all_alerts += [a.to_dict() for a in review_alerts]

    # 3) Contágio setorial
    contagion = detect_contagion(
        period=contagion_period, interval=contagion_interval
    )
    all_alerts += [c.to_dict() for c in contagion]

    # 4) Contexto de notícias p/ a Claude (opcional)
    news_contexts = []
    if news_by_ticker:
        news_contexts = build_news_review_context(news_by_ticker)

    save_state(state)

    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "alerts": all_alerts,
        "alert_count": len(all_alerts),
        "news_review": news_contexts,   # passe ao seu loop agêntico
        "summary": portfolio_summary(),
    }


# ---------------------------------------------------------------------------
# Entry point para o cron do GitHub Actions
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        # Em produção: monte news_by_ticker com feedparser antes de chamar.
        result = run_full_scan(news_by_ticker=None)

        logger.info("Varredura concluída: %d alerta(s).", result["alert_count"])
        for alert in result["alerts"]:
            logger.info("ALERTA %s", json.dumps(alert, ensure_ascii=False))
            # salvar_alerta(alert)  /  notificar(alert)

        # Resumo de P&L da carteira
        s = result["summary"]
        logger.info(
            "Carteira: investido US$ %.2f | posição US$ %.2f | P&L US$ %.2f",
            s["total_invested"], s["total_position"], s["total_pl"],
        )
        return 0
    except Exception:
        logger.exception("Varredura falhou")
        return 1   # marca o job do Actions como falho


if __name__ == "__main__":
    sys.exit(main())
