"""Snapshot standalone do detector de "catalisador de squeeze + reversão
técnica" (tools.py::check_squeeze_setup) -- pensado pro poller de background
em alert-checker.ts, que envia e-mail em dois níveis:

- "near": falta só 1 ou 2 dos 4 requisitos pro setup completo (2+ sinais de
  risco de squeeze perigosos E 2+ confirmações de reversão técnica) --
  avisa o usuário do que ainda falta, pra ele saber o que acompanhar.
- "confirmed": squeeze_setup_detected=true (os 4 requisitos batidos E
  earnings não iminente -- mesmo gate extra que borrow_fee_cheap: earnings
  em 0-14 dias nunca deixa o total_missing chegar a 0, mesmo com os 4
  requisitos técnicos batidos, porque o resultado pode gapear o papel pra
  qualquer lado antes do squeeze se confirmar de verdade).

Mesmo motivo/padrão de import de get_bounce_alerts.py: roda como
`python -m agent.get_squeeze_alerts` (import absoluto via pacote) porque
tools.py faz imports relativos (`from . import market_alerts as _ma` etc.)
que só resolvem nesse contexto de pacote.

check_squeeze_setup já é cacheado (30min, ver @cached em tools.py) --
rodar esse script a cada poll de 5min não gera chamada de rede extra
dentro da janela de cache.

Input (stdin JSON): {"tickers": ["NVDA", ...]}  (default: config.TICKERS)
Output (stdout JSON): {"alerts": [...]}  -- só tickers no nível "near" ou
"confirmed" (nível "none", faltando 3+ requisitos, não é retornado -- não
vale a pena notificar algo tão longe do setup completo).
"""
import sys
import json

from agent import config
from agent.bounded_parallel import bounded_parallel_map, budget_from_deadline, exit_now
from agent.tools import check_squeeze_setup

# Timeout do lado Node (alert-checker.ts::fetchSqueezeAlerts) é 120s -- mais
# generoso que os demais checkers (check_squeeze_setup faz várias chamadas
# de rede por ticker: yfinance, iBorrowDesk, FINRA, Unusual Whales opcional).
# Fallback quando o processo roda sem AGENT_DEADLINE_TS no env (execução
# manual do script). Com a variável definida, o orçamento real vem do
# deadline do chamador via budget_from_deadline() -- constante fixa aqui
# não conseguia cobrir o custo de import (~8s de pandas/numpy/yfinance),
# que sai da mesma folga. Ver bounded_parallel.py.
BUDGET_S = 100

# Rótulos legíveis pros 4 sinais de risco de squeeze (mesma ordem/definição
# de check_squeeze_setup: "alto" exige 2+ perigosos entre esses 4, com o
# gate extra de borrow_fee_cheap tratado à parte abaixo). Cada tupla é
# (campo do "perigoso" já calculado por check_squeeze_setup, campo do
# threshold, template do rótulo) -- lê o booleano pronto em vez de
# recalcular `valor >= threshold` aqui (os nomes de campo de threshold em
# squeeze_risk não seguem um padrão único -- short_pct_danger_threshold,
# não short_pct_of_float_danger_threshold -- então recalcular local já foi
# fonte de bug; ler o campo "*_dangerous" que a própria check_squeeze_setup
# expõe evita duplicar a lógica de gate, incluindo o de iliquidez).
_RISK_SIGNALS = [
    ("short_dangerous", "short_pct_danger_threshold", "short > {threshold:.0f}% do float"),
    ("dtc_dangerous", "days_to_cover_danger_threshold", "days-to-cover >= {threshold:.0f} dias"),
    ("borrow_fee_dangerous", "borrow_fee_danger_threshold", "taxa de aluguel >= {threshold:.0f}%/ano"),
    ("short_volume_dangerous", "short_volume_danger_threshold", "volume vendido a descoberto >= {threshold:.0f}% (FINRA)"),
]

# Rótulos legíveis pras 4 confirmações de reversão técnica -- classificadas
# por palavra-chave no texto que check_squeeze_setup já monta em
# 'reversal_confirmations' (a função só lista as que bateram, nunca as que
# faltam, então inferimos o "kind" de cada uma pra saber o que sobra).
_CONFIRM_KIND_LABELS = {
    "candle": "candle de reversão bullish (ou Doji de indecisão)",
    "divergência": "divergência bullish de RSI",
    "volume": "volume de pânico perto de um fundo",
    "suporte": "toque no suporte de 50/200 pregões",
}


def _classify_confirm_kind(text: str) -> str | None:
    low = text.lower()
    if "candle" in low or "doji" in low:
        return "candle"
    if "diverg" in low:
        return "divergência"
    if "volume" in low:
        return "volume"
    if "suporte" in low:
        return "suporte"
    return None


def _progress_for(ticker: str) -> dict | None:
    try:
        result = check_squeeze_setup(ticker)
    except Exception as e:
        print(f"[get_squeeze_alerts] {ticker}: {e}", file=sys.stderr)
        return None
    if result.get("error"):
        return None

    risk = result["squeeze_risk"]
    missing_risk_labels = []
    present_risk_labels = []
    for dangerous_key, threshold_key, label_tpl in _RISK_SIGNALS:
        threshold = risk.get(threshold_key)
        label = label_tpl.format(threshold=threshold) if threshold is not None else dangerous_key
        if risk.get(dangerous_key):
            present_risk_labels.append(label)
        else:
            missing_risk_labels.append(label)
    n_dangerous = risk["n_dangerous"]
    risk_missing = max(0, 2 - n_dangerous)
    # Aluguel barato/disponível invalida a mecânica de squeeze mesmo com 2+
    # sinais perigosos (mesmo gate de check_squeeze_setup -- ver
    # borrow_fee_cheap) -- nunca deixa risk_missing chegar a 0 nesse caso,
    # senão o setup seria contado como "confirmado" sem pressão de cobertura
    # real por trás (caso real: SMCI com short interest alto mas aluguel a
    # 0,41%/ano).
    if risk.get("borrow_fee_cheap"):
        risk_missing = max(risk_missing, 1)
        missing_risk_labels.append(
            f"aluguel a {risk['borrow_fee']:.2f}%/ano é barato/disponível — "
            f"invalida a mecânica de squeeze mesmo com outros sinais perigosos"
        )

    confirmations = result["reversal_confirmations"]
    present_kinds = {k for c in confirmations if (k := _classify_confirm_kind(c))}
    missing_confirm_labels = [label for kind, label in _CONFIRM_KIND_LABELS.items() if kind not in present_kinds]
    confirm_count = len(confirmations)
    confirm_missing = max(0, 2 - confirm_count)

    # Earnings iminente (0-14 dias) nunca deixa o setup contar como
    # "confirmado" (mesmo gate de check_squeeze_setup -- ver
    # earnings_imminent): um resultado por vir pode gapear o papel pra
    # qualquer lado antes do squeeze técnico se confirmar de verdade.
    earnings = result.get("earnings") or {}
    event_missing = 0
    missing_event_labels = []
    if earnings.get("earnings_imminent"):
        event_missing = 1
        missing_event_labels.append(
            f"earnings em {earnings['days_until_earnings']} dia(s) "
            f"({earnings['next_earnings_date']}) — resultado pode gapear o papel pra "
            f"qualquer lado antes do squeeze se confirmar"
        )

    total_missing = risk_missing + confirm_missing + event_missing
    if total_missing == 0:
        tier = "confirmed"
    elif total_missing <= 2:
        tier = "near"
    else:
        return None  # longe demais do setup completo, não vale notificar

    return {
        "ticker": ticker,
        "price": result["price"],
        "tier": tier,
        "riskLevel": risk["level"],
        "nDangerous": n_dangerous,
        "riskMissing": risk_missing,
        "presentRiskSignals": present_risk_labels,
        "missingRiskSignals": missing_risk_labels,
        "confirmCount": confirm_count,
        "confirmMissing": confirm_missing,
        "presentConfirmSignals": confirmations,
        "missingConfirmSignals": missing_confirm_labels,
        "excludedEarningsReactionSignals": result.get("reversal_confirmations_excluded_earnings") or [],
        "earningsImminent": bool(earnings.get("earnings_imminent")),
        "missingEventSignals": missing_event_labels,
        "totalMissing": total_missing,
    }


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS

    results = bounded_parallel_map(
        _progress_for,
        tickers,
        budget_s=budget_from_deadline(BUDGET_S, label="get_squeeze_alerts"),
        label="get_squeeze_alerts",
    )
    alerts = [a for a in results if a is not None]

    order = {"confirmed": 0, "near": 1}
    alerts.sort(key=lambda a: order[a["tier"]])

    exit_now(json.dumps({"alerts": alerts}, ensure_ascii=False) + "\n")
