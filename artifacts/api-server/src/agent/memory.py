"""
Acesso à memória dos dias anteriores (observações do DB) e bloco de
contexto rico (carteira + alertas + cenário) injetado no prompt do chat.
"""
from __future__ import annotations

import datetime
import os
from typing import Iterable

from . import config
from .http_retry import SESSION


def _internal_headers() -> dict:
    """Retorna os headers de autenticação interna."""
    key = os.environ.get("OPERATOR_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _api_url() -> str:
    return os.environ.get("INTERNAL_API_URL", "http://localhost:5000")


def _fmt_money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_qty(value) -> str:
    try:
        q = float(value)
        if abs(q - round(q)) < 1e-6:
            return str(int(round(q)))
        return f"{q:.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def _is_active_position(quantity) -> bool:
    try:
        return float(quantity) > 0.00001
    except (TypeError, ValueError):
        return False


def recent_context(
    days: int = 7,
    portfolio_only: bool = False,
    portfolio_tickers: Iterable[str] | None = None,
    limit: int = 60,
) -> str:
    """
    Recupera as observações dos últimos N dias da API interna e formata
    como texto para injetar no system prompt.

    portfolio_only / portfolio_tickers
        Quando True (ou quando a lista é passada), filtra só tickers da
        carteira ativa. Isso evita que observações de cobertura geral
        (cestas/setores) poluam o chat e sejam tratadas como posição.

    Consolidação
        No máximo 1 linha por (ticker, data) — a mais recente. Várias
        save_observation no mesmo dia deixavam o prompt inchado e
        contraditório; agora fica uma só.
    """
    try:
        r = SESSION.get(
            f"{_api_url()}/api/observations/internal",
            params={"limit": limit},
            headers=_internal_headers(),
            timeout=5,
        )
        r.raise_for_status()
        observations = r.json()
        if not observations:
            return "(nenhuma observação anterior registrada)"

        if portfolio_tickers is not None:
            allowed = {t.strip().upper() for t in portfolio_tickers if t and t.strip()}
        elif portfolio_only:
            allowed = {t.upper() for t in config.PORTFOLIO_TICKERS}
        else:
            allowed = None

        cutoff = datetime.date.today() - datetime.timedelta(days=days)
        by_key: dict[tuple[str, str], dict] = {}
        for obs in observations:
            ticker = str(obs.get("ticker") or "").upper()
            if not ticker:
                continue
            if allowed is not None and ticker not in allowed:
                continue
            try:
                obs_date = datetime.date.fromisoformat(obs["date"])
            except Exception:
                continue
            if obs_date < cutoff:
                continue
            key = (ticker, obs["date"])
            if key not in by_key:
                by_key[key] = obs

        if not by_key:
            scope = "da carteira " if allowed is not None else ""
            return f"(nenhuma observação {scope}nos últimos {days} dias)"

        ordered = sorted(
            by_key.values(),
            key=lambda o: (o.get("date") or "", o.get("ticker") or ""),
            reverse=True,
        )

        lines = []
        for obs in ordered:
            price_str = ""
            raw_price = obs.get("priceAtObservation")
            if raw_price is not None:
                try:
                    price_str = f" | Preço: ${float(raw_price):.2f}"
                except (TypeError, ValueError):
                    pass
            sentiment = str(obs.get("sentiment") or "?").upper()
            summary = str(obs.get("summary") or "").strip()
            lines.append(
                f"[{obs['date']}] {obs['ticker']} ({sentiment}){price_str}: {summary}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"(erro ao recuperar memória: {e})"


def _portfolio_lines() -> list[str]:
    """Posições ativas da carteira (qty + custo) via API interna."""
    try:
        r = SESSION.get(
            f"{_api_url()}/api/portfolio",
            headers=_internal_headers(),
            timeout=5,
        )
        r.raise_for_status()
        positions = r.json()
    except Exception as e:
        return [f"(erro ao ler carteira: {e})"]

    if not isinstance(positions, list):
        return ["(resposta inesperada da API de carteira)"]

    active = [p for p in positions if _is_active_position(p.get("quantity"))]
    if not active:
        if config.PORTFOLIO_TICKERS:
            return [
                "Tickers da carteira (sem detalhe de qty no banco): "
                + ", ".join(config.PORTFOLIO_TICKERS)
            ]
        return ["nenhuma posição aberta"]

    lines = []
    total_invested = 0.0
    for p in active:
        ticker = str(p.get("ticker") or "?").upper()
        qty = _fmt_qty(p.get("quantity"))
        avg = _fmt_money(p.get("avgCost"))
        invested = p.get("investedAmount")
        try:
            total_invested += float(invested)
            inv_str = _fmt_money(invested)
        except (TypeError, ValueError):
            inv_str = "—"
        etf = " [ETF]" if p.get("isEtf") else ""
        sim = " [sim]" if p.get("isSimulated") else ""
        lines.append(f"{ticker}{etf}{sim} | {qty} sh | avg {avg} | investido {inv_str}")

    lines.append(f"Total investido (posições ativas): {_fmt_money(total_invested)}")
    return lines


def _alerts_lines() -> list[str]:
    """Alertas habilitados."""
    try:
        r = SESSION.get(
            f"{_api_url()}/api/alerts",
            headers=_internal_headers(),
            timeout=5,
        )
        r.raise_for_status()
        alerts = r.json()
    except Exception as e:
        return [f"(erro ao ler alertas: {e})"]

    if not isinstance(alerts, list) or not alerts:
        return ["nenhum alerta cadastrado"]

    enabled = [a for a in alerts if a.get("enabled", True)]
    if not enabled:
        return ["nenhum alerta ativo (todos desabilitados)"]

    lines = []
    for a in enabled[:20]:
        symbol = str(a.get("symbol") or "?").upper()
        condition = a.get("condition") or "?"
        indicator = a.get("indicator") or "price"
        parts = [f"{symbol}", f"{indicator}", f"{condition}"]
        if a.get("thresholdPct") is not None:
            try:
                parts.append(f"{float(a['thresholdPct']):+g}%")
            except (TypeError, ValueError):
                parts.append(f"pct={a['thresholdPct']}")
        if a.get("thresholdPrice") is not None:
            parts.append(f"@ {_fmt_money(a['thresholdPrice'])}")
        if a.get("thresholdValue") is not None and a.get("thresholdPct") is None:
            parts.append(f"val={a['thresholdValue']}")
        aid = a.get("id")
        lines.append(f"#{aid} " + " ".join(parts))
    if len(enabled) > 20:
        lines.append(f"… +{len(enabled) - 20} alertas")
    return lines


def _scenario_lines() -> list[str]:
    """Status resumido do Painel de Cenários (fail-open)."""
    try:
        from .tools import get_scenario_status

        status = get_scenario_status()
    except Exception as e:
        return [f"(erro ao ler cenário: {e})"]

    if not isinstance(status, dict):
        return ["(status de cenário indisponível)"]
    if status.get("error"):
        return [f"(erro: {status['error']})"]
    if not status.get("configured"):
        return [status.get("note") or "cenário não configurado"]

    lines = [
        f"data-alvo {status.get('dataAlvo')} | limiar {status.get('thresholdPct')}%",
    ]
    if status.get("pEmpateAtualPct") is not None:
        lines.append(
            f"P(empatar) atual {status['pEmpateAtualPct']}% | "
            f"dias restantes {status.get('diasRestantes', '—')}"
        )
    if status.get("pctDiasConfirmados") is not None:
        lines.append(
            f"termômetro: {status['pctDiasConfirmados']}% dos "
            f"{status.get('diasAcompanhados', '?')} dias acima do limiar "
            f"(alto = confirmação sustentada, não instabilidade)"
        )
    if status.get("valorTotalHoje") is not None and status.get("custoTotal") is not None:
        lines.append(
            f"valor hoje {_fmt_money(status['valorTotalHoje'])} / "
            f"custo {_fmt_money(status['custoTotal'])}"
        )
    if status.get("cicloResolvido"):
        bateu = "BATEU" if status.get("cicloBateu") else "não bateu"
        lines.append(f"ciclo já resolvido: {bateu}")
    return lines


def rich_context_block() -> str:
    """
    Bloco volátil para o system prompt do chat: carteira + alertas + cenário.
    Fail-open em cada seção — uma falha não derruba as outras.
    """
    sections = [
        ("CARTEIRA (posições abertas)", _portfolio_lines()),
        ("ALERTAS ATIVOS", _alerts_lines()),
        ("CENÁRIO", _scenario_lines()),
    ]
    parts = []
    for title, lines in sections:
        body = "\n".join(f"  {ln}" for ln in lines)
        parts.append(f"{title}:\n{body}")
    return "\n\n".join(parts)
