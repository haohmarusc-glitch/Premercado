"""
portfolio.py
------------
Monitoramento da carteira do agente.

Cobre:
    1. Alertas de ALTA   : +10 / +15 / +20 / +30 / +40 / +50% vs. preço médio
    2. Alertas de BAIXA  : -10 / -15 / -20 / -30% vs. preço médio
    3. Alerta de 30 DIAS : quando uma compra (lote) completa N dias (marco de reavaliação)
    4. Sinais de NOTÍCIA : possível compra / possível venda por ticker (via Claude)
    5. Sugestão de novos tickers adjacentes à carteira (por camada do setor)

Princípio de dedupe:
    Cada alerta tem uma "key" única. Você passa o conjunto de keys já disparadas
    (vindo da sua persistência JSON / Supabase) e o módulo devolve só o que é NOVO.
    Assim um gatilho de +10% não fica reaparecendo a cada execução.

Depende de: yfinance
Opcional   : sector_contagion.py (reaproveita os grupos por camada)
Aviso      : sinais são informativos para revisão, NÃO recomendação de investimento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1) Carteira — lotes preservam a data de cada compra (pro alerta de 30 dias)
# ---------------------------------------------------------------------------
PORTFOLIO: dict[str, dict] = {
    "GOOGL": {
        "shares": 0.81693,
        "avg_price": 367.22,
        "lots": [{"date": "2026-06-02", "usd": 300.00}],
    },
    "TSLA": {
        "shares": 0.53411,
        "avg_price": 374.45,
        "lots": [{"date": "2026-03-20", "usd": 200.00}],
    },
    "MU": {
        "shares": 0.46091,
        "avg_price": 865.65,
        "lots": [
            {"date": "2026-06-02", "usd": 140.00},
            {"date": "2026-05-14", "usd": 258.99},
        ],
    },
    "NVDA": {
        "shares": 5.37435,
        "avg_price": 208.21,
        "lots": [
            {"date": "2026-05-27", "usd": 139.00},
            {"date": "2026-05-21", "usd": 70.00},
            {"date": "2026-05-20", "usd": 140.00},
            {"date": "2026-05-18", "usd": 470.00},
            {"date": "2026-03-20", "usd": 300.00},
        ],
    },
    "ARM": {
        "shares": 0.87559,
        "avg_price": 399.73,
        "lots": [],  # sem histórico de compra fornecido — preencha quando tiver
    },
    "INTC": {
        "shares": 3.35583,
        "avg_price": 104.29,
        "lots": [],  # idem
    },
}

# ---------------------------------------------------------------------------
# 2) Configuração de gatilhos
# ---------------------------------------------------------------------------
GAIN_THRESHOLDS = [10, 15, 20, 30, 40, 50]   # % de alta vs. preço médio
LOSS_THRESHOLDS = [10, 15, 20, 30]           # % de baixa vs. preço médio
HOLDING_DAYS_MILESTONES = [30]               # marcos de dias desde a compra

# Mapa de adjacência por camada (pra sugerir novos tickers).
# Se você tiver o sector_contagion.py, pode importar SECTOR_GROUPS de lá.
SECTOR_ADJACENCY: dict[str, list[str]] = {
    "MU":    ["SNDK", "WDC"],            # memória/armazenamento
    "NVDA":  ["AMD", "AVGO", "MRVL"],    # GPU / aceleradores / custom silicon
    "ARM":   ["AVGO", "MRVL"],           # design / IP
    "INTC":  ["TSM", "AMD"],             # foundry / x86
    "GOOGL": ["AVGO", "MRVL"],           # TPU / custom silicon parceiros
    "TSLA":  ["NVDA"],                   # compute pra autonomia
}


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------
@dataclass
class Alert:
    key: str                 # identificador único pra dedupe
    type: str                # gain | loss | holding_days | news_signal
    ticker: str
    message: str
    data: dict = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "type": self.type,
            "ticker": self.ticker,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Preço atual
# ---------------------------------------------------------------------------
def get_current_price(ticker: str) -> Optional[float]:
    """Preço mais recente via yfinance. Retorna None se indisponível."""
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            # fallback intradiário
            hist = yf.Ticker(ticker).history(period="5d", interval="5m")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("Preço indisponível para %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# 1+2) Alertas de alta/baixa vs. preço médio
# ---------------------------------------------------------------------------
def check_price_alerts(fired_keys: Optional[set[str]] = None) -> list[Alert]:
    """
    Dispara quando o preço cruza um limiar de alta ou baixa vs. preço médio.
    `fired_keys` evita re-disparo (passe o set salvo na sua persistência).
    """
    fired_keys = fired_keys or set()
    alerts: list[Alert] = []

    for ticker, pos in PORTFOLIO.items():
        price = get_current_price(ticker)
        if price is None:
            continue
        avg = pos["avg_price"]
        change_pct = (price - avg) / avg * 100.0

        if change_pct >= 0:
            for thr in GAIN_THRESHOLDS:
                key = f"gain:{ticker}:{thr}"
                if change_pct >= thr and key not in fired_keys:
                    alerts.append(Alert(
                        key=key, type="gain", ticker=ticker,
                        message=(f"[ALTA] {ticker} +{change_pct:.1f}% vs. médio "
                                 f"(cruzou +{thr}%). Atual ${price:.2f} / médio ${avg:.2f}."),
                        data={"change_pct": round(change_pct, 2),
                              "threshold": thr, "price": price, "avg_price": avg},
                    ))
        else:
            for thr in LOSS_THRESHOLDS:
                key = f"loss:{ticker}:{thr}"
                if change_pct <= -thr and key not in fired_keys:
                    alerts.append(Alert(
                        key=key, type="loss", ticker=ticker,
                        message=(f"[BAIXA] {ticker} {change_pct:.1f}% vs. médio "
                                 f"(cruzou -{thr}%). Atual ${price:.2f} / médio ${avg:.2f}."),
                        data={"change_pct": round(change_pct, 2),
                              "threshold": thr, "price": price, "avg_price": avg},
                    ))
    return alerts


# ---------------------------------------------------------------------------
# 3) Alerta de dias desde a compra (marco de reavaliação)
# ---------------------------------------------------------------------------
def check_holding_alerts(
    milestones: Optional[list[int]] = None,
    fired_keys: Optional[set[str]] = None,
) -> list[Alert]:
    """Dispara quando um lote completa um marco de dias (default: 30)."""
    milestones = milestones or HOLDING_DAYS_MILESTONES
    fired_keys = fired_keys or set()
    today = datetime.now(timezone.utc).date()
    alerts: list[Alert] = []

    for ticker, pos in PORTFOLIO.items():
        for lot in pos.get("lots", []):
            try:
                buy_date = datetime.strptime(lot["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            days_held = (today - buy_date).days
            for m in milestones:
                key = f"holding:{ticker}:{lot['date']}:{m}"
                if days_held >= m and key not in fired_keys:
                    alerts.append(Alert(
                        key=key, type="holding_days", ticker=ticker,
                        message=(f"[{m} DIAS] Lote de {ticker} comprado em "
                                 f"{lot['date']} (US${lot['usd']:.2f}) completou "
                                 f"{days_held} dias — marco de reavaliação."),
                        data={"buy_date": lot["date"], "usd": lot["usd"],
                              "days_held": days_held, "milestone": m},
                    ))
    return alerts


# ---------------------------------------------------------------------------
# 4) Sinais de notícia: possível compra / possível venda (via Claude)
# ---------------------------------------------------------------------------
def check_news_signals(
    news_by_ticker: dict[str, list[str]],
    classify_fn: Callable[[str, list[str]], dict],
    fired_keys: Optional[set[str]] = None,
) -> list[Alert]:
    """
    Recebe notícias já coletadas (seu pipeline feedparser/EDGAR) e uma função
    `classify_fn(ticker, headlines) -> {"signal": "buy"|"sell"|"neutral",
                                        "reason": str, "confidence": float}`
    que você implementa chamando o SEU client da Anthropic (com o timeout que
    a gente corrigiu). Mantém a chamada de IA fora deste módulo pra não duplicar
    config de API.

    Só emite alerta quando o sinal é buy ou sell (neutral é ignorado).
    """
    fired_keys = fired_keys or set()
    alerts: list[Alert] = []

    for ticker, headlines in news_by_ticker.items():
        if ticker not in PORTFOLIO or not headlines:
            continue
        try:
            result = classify_fn(ticker, headlines)
        except Exception as exc:
            logger.warning("Falha ao classificar notícias de %s: %s", ticker, exc)
            continue

        signal = result.get("signal", "neutral")
        if signal not in ("buy", "sell"):
            continue

        # key inclui a data -> permite um sinal por ticker por dia
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"news:{ticker}:{signal}:{day}"
        if key in fired_keys:
            continue

        rotulo = "POSSÍVEL COMPRA" if signal == "buy" else "POSSÍVEL VENDA"
        alerts.append(Alert(
            key=key, type="news_signal", ticker=ticker,
            message=(f"[{rotulo}] {ticker}: {result.get('reason', 'sinal de notícia')} "
                     f"(confiança {result.get('confidence', 0):.0%}). "
                     f"Sinal informativo — revise antes de agir."),
            data={"signal": signal, **result},
        ))
    return alerts


# ---------------------------------------------------------------------------
# 5) Sugestão de novos tickers adjacentes (não-detidos)
# ---------------------------------------------------------------------------
def suggest_new_tickers() -> list[dict]:
    """
    Cruza a carteira com o mapa de adjacência e retorna candidatos da mesma
    camada que você ainda NÃO tem. Útil pra alimentar uma watchlist.
    """
    held = set(PORTFOLIO.keys())
    suggestions: dict[str, list[str]] = {}

    for ticker in held:
        for candidate in SECTOR_ADJACENCY.get(ticker, []):
            if candidate not in held:
                suggestions.setdefault(candidate, []).append(ticker)

    return [
        {"ticker": c, "rationale": f"adjacente a {', '.join(sorted(src))} na carteira"}
        for c, src in sorted(suggestions.items())
    ]


# ---------------------------------------------------------------------------
# Orquestração — junta tudo
# ---------------------------------------------------------------------------
def run_all(
    fired_keys: Optional[set[str]] = None,
    news_by_ticker: Optional[dict[str, list[str]]] = None,
    classify_fn: Optional[Callable] = None,
) -> list[Alert]:
    """Roda todos os checks e devolve a lista consolidada de alertas novos."""
    fired_keys = fired_keys or set()
    alerts: list[Alert] = []
    alerts += check_price_alerts(fired_keys)
    alerts += check_holding_alerts(fired_keys=fired_keys)
    if news_by_ticker and classify_fn:
        alerts += check_news_signals(news_by_ticker, classify_fn, fired_keys)
    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    novos = run_all()
    if not novos:
        print("Nenhum alerta novo de preço/dias.")
    for a in novos:
        print(a.message)
        # salvar_alerta(a.to_dict())            # sua persistência
        # fired_keys.add(a.key)                 # marque como disparado

    print("\nSugestões de novos tickers:")
    for s in suggest_new_tickers():
        print(f"  {s['ticker']} — {s['rationale']}")
