"""Snapshot standalone do detector de "catalisador de squeeze + reversão
técnica" (tools.py::check_squeeze_setup) -- pensado pro poller de background
em alert-checker.ts, que envia e-mail em dois níveis:

- "near": falta só 1 ou 2 dos 4 requisitos pro setup completo (2+ sinais de
  risco de squeeze perigosos E 2+ confirmações de reversão técnica) --
  avisa o usuário do que ainda falta, pra ele saber o que acompanhar.
- "confirmed": squeeze_setup_detected=true (os 4 requisitos batidos).

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
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import config
from agent.tools import check_squeeze_setup

# Rótulos legíveis pros 4 sinais de risco de squeeze (mesma ordem/definição
# de check_squeeze_setup: "alto" exige 2+ perigosos entre esses 4). Cada
# tupla é (campo do valor, campo do threshold, template do rótulo) -- os
# nomes dos campos de threshold em squeeze_risk NÃO seguem um padrão único
# (short_pct_danger_threshold, não short_pct_of_float_danger_threshold;
# short_volume_danger_threshold, não short_volume_ratio_danger_threshold),
# por isso o mapeamento é explícito em vez de `f"{campo}_danger_threshold"`.
_RISK_SIGNALS = [
    ("short_pct_of_float", "short_pct_danger_threshold", "short > {threshold:.0f}% do float"),
    ("days_to_cover", "days_to_cover_danger_threshold", "days-to-cover >= {threshold:.0f} dias"),
    ("borrow_fee", "borrow_fee_danger_threshold", "taxa de aluguel >= {threshold:.0f}%/ano"),
    ("short_volume_ratio", "short_volume_danger_threshold", "volume vendido a descoberto >= {threshold:.0f}% (FINRA)"),
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
    n_dangerous = 0
    missing_risk_labels = []
    present_risk_labels = []
    for value_key, threshold_key, label_tpl in _RISK_SIGNALS:
        value = risk.get(value_key)
        threshold = risk.get(threshold_key)
        is_dangerous = value is not None and threshold is not None and value >= threshold
        label = label_tpl.format(threshold=threshold) if threshold is not None else value_key
        if is_dangerous:
            n_dangerous += 1
            present_risk_labels.append(label)
        else:
            missing_risk_labels.append(label)
    risk_missing = max(0, 2 - n_dangerous)

    confirmations = result["reversal_confirmations"]
    present_kinds = {k for c in confirmations if (k := _classify_confirm_kind(c))}
    missing_confirm_labels = [label for kind, label in _CONFIRM_KIND_LABELS.items() if kind not in present_kinds]
    confirm_count = len(confirmations)
    confirm_missing = max(0, 2 - confirm_count)

    total_missing = risk_missing + confirm_missing
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
        "totalMissing": total_missing,
    }


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS

    alerts = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_progress_for, t): t for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            try:
                progress = future.result()
                if progress is not None:
                    alerts.append(progress)
            except Exception as e:
                print(f"[get_squeeze_alerts] {t}: {e}", file=sys.stderr)

    order = {"confirmed": 0, "near": 1}
    alerts.sort(key=lambda a: order[a["tier"]])

    print(json.dumps({"alerts": alerts}, ensure_ascii=False))
