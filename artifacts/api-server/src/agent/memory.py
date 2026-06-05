"""
Acesso à memória dos dias anteriores (observações do DB).
"""
import datetime
import os

import requests


def recent_context(days: int = 7) -> str:
    """
    Recupera as observações dos últimos N dias da API interna
    e formata como texto para injetar no system prompt.
    """
    try:
        api_url = os.environ.get("INTERNAL_API_URL", "http://localhost:5000")
        r = requests.get(
            f"{api_url}/api/observations/internal",
            params={"limit": 30},
            timeout=5,
        )
        r.raise_for_status()
        observations = r.json()
        if not observations:
            return "(nenhuma observação anterior registrada)"

        cutoff = datetime.date.today() - datetime.timedelta(days=days)
        lines = []
        for obs in observations:
            try:
                obs_date = datetime.date.fromisoformat(obs["date"])
            except Exception:
                continue
            if obs_date < cutoff:
                continue
            price_str = f" | Preço: ${obs['priceAtObservation']:.2f}" if obs.get("priceAtObservation") else ""
            lines.append(
                f"[{obs['date']}] {obs['ticker']} ({obs['sentiment'].upper()}){price_str}: {obs['summary']}"
            )

        return "\n".join(lines) if lines else "(nenhuma observação nos últimos 7 dias)"
    except Exception as e:
        return f"(erro ao recuperar memória: {e})"
