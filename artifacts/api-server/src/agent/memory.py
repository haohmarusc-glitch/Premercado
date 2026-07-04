"""
Acesso à memória dos dias anteriores (observações do DB).
"""
import datetime
import os

import requests


def _internal_headers() -> dict:
    """Retorna os headers de autenticação interna."""
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


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
            headers=_internal_headers(),
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
            # priceAtObservation vem da API interna sem coerção (coluna numeric
            # do Postgres chega como string) — converte com segurança em vez de
            # deixar o :.2f estourar TypeError e derrubar TODA a memória (não
            # só a observação com o valor problemático).
            price_str = ""
            raw_price = obs.get("priceAtObservation")
            if raw_price is not None:
                try:
                    price_str = f" | Preço: ${float(raw_price):.2f}"
                except (TypeError, ValueError):
                    pass
            lines.append(
                f"[{obs['date']}] {obs['ticker']} ({obs['sentiment'].upper()}){price_str}: {obs['summary']}"
            )

        return "\n".join(lines) if lines else "(nenhuma observação nos últimos 7 dias)"
    except Exception as e:
        return f"(erro ao recuperar memória: {e})"
