"""Estudo de Entrada e Saída — probabilidade de UM ticker bater um preço-alvo
até uma data-alvo, com referência de suporte (mínima/média de baixa dos
últimos 12 meses e 6 meses) pra entrada.

Reaproveita get_scenario_params, get_earnings, earnings_reaction_analysis
e get_news_feed. Não dispara ordem.
"""
import sys
import json
import math
from statistics import NormalDist

from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import pandas as pd
import yfinance as yf

from agent.bounded_parallel import bounded_parallel_map, budget_from_deadline, exit_now
from agent.security import sanitize_ticker
from agent import market_data_provider

from agent.brt import today_brt
from agent.get_scenario_params import compute as compute_scenario_params
from agent.get_earnings import get_earnings
from agent.earnings_reaction_analysis import analyze_ticker as analyze_earnings_reaction
from agent.earnings_entry_regime import anexar_setups
from agent.get_news_feed import for_ticker as news_for_ticker, _company_names, translate_all
from agent import json_seguro

_probe_imports()

BENCHMARK = "SMH"
DEFAULT_VOL = 0.5
BUDGET_S = 45
SIX_MONTHS_TRADING_DAYS = 126
ENTRY_PULLBACK_DAYS = 30
ENTRY_PULLBACK_PROB = 0.30


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _low_stats(series: "pd.Series") -> tuple[float | None, float | None]:
    clean = series.dropna()
    if clean.empty:
        return None, None
    return round(float(clean.mean()), 4), round(float(clean.min()), 4)


def _entry_pullback_price(current_price: float, vol_annual: float | None) -> float | None:
    if not current_price or current_price <= 0 or not vol_annual or vol_annual <= 0:
        return None
    t_curto = ENTRY_PULLBACK_DAYS / 365
    z = NormalDist().inv_cdf(ENTRY_PULLBACK_PROB)
    return round(current_price * math.exp(z * vol_annual * math.sqrt(t_curto)), 4)


def _study_for(spec: dict) -> dict:
    raw_ticker = spec.get("ticker", "")
    try:
        ticker = sanitize_ticker(raw_ticker)
    except ValueError as e:
        return {"ticker": raw_ticker, "error": str(e)}

    target_price = spec.get("targetPrice")
    target_date = spec.get("targetDate")
    if not target_price or target_price <= 0:
        return {"ticker": ticker, "error": "targetPrice inválido"}
    if not target_date:
        return {"ticker": ticker, "error": "targetDate obrigatória"}

    today = today_brt()
    try:
        target_dt = pd.Timestamp(target_date).date()
    except Exception:
        return {"ticker": ticker, "error": f"targetDate inválida: {target_date}"}
    days_until = (target_dt - today).days
    if days_until < 1:
        return {"ticker": ticker, "error": "targetDate precisa ser no futuro"}

    try:
        resultado_hist = market_data_provider.get_daily_history(
            ticker, "1y", auto_adjust=False
        )
        if not resultado_hist.ok:
            return {"ticker": ticker, "error": "sem histórico de preço"}
        hist = resultado_hist.df
        current_price = float(yf.Ticker(ticker).fast_info.last_price)
    except Exception as e:
        return {"ticker": ticker, "error": f"falha ao buscar preço/histórico: {type(e).__name__}: {e}"}

    avg_low_1y, min_low_1y = _low_stats(hist["Low"])
    avg_low_6m, min_low_6m = _low_stats(hist["Low"].tail(SIX_MONTHS_TRADING_DAYS))

    scenario_out = compute_scenario_params([ticker], BENCHMARK)
    params = scenario_out.get("params", {}).get(ticker, {})
    vol_annual = params.get("volAnnual") or DEFAULT_VOL
    beta_sector = params.get("betaSector")
    entry_pullback_price = _entry_pullback_price(current_price, vol_annual)
    sector_momentum = scenario_out.get("sectorMomentum") or {}
    momentum_annual_pct = sector_momentum.get("momentumAnnualPct")

    earnings_date = None
    try:
        earnings_info = get_earnings([ticker])
        if earnings_info:
            earnings_date = earnings_info[0].get("earningsDate")
    except Exception as e:
        print(f"[entry_exit_study] earnings de {ticker}: {e}", file=sys.stderr)

    jump_std_pct = None
    earnings_setup = None
    try:
        reaction = anexar_setups(analyze_earnings_reaction(ticker))
        jump_std_pct = (reaction.get("summary") or {}).get("close_pct_std")
        earnings_setup = (reaction.get("summary") or {}).get("ultimo_setup")
    except Exception as e:
        print(f"[entry_exit_study] reação de earnings de {ticker}: {e}", file=sys.stderr)

    T = days_until / 365
    vol_eff = vol_annual
    if jump_std_pct and earnings_date:
        try:
            evento = pd.Timestamp(earnings_date).date()
            if today <= evento <= target_dt:
                jump_var = (jump_std_pct / 100) ** 2
                vol_eff = math.sqrt(vol_annual ** 2 + jump_var / T)
        except Exception:
            pass

    sd = vol_eff * math.sqrt(T)
    prob_reach_target = None
    if current_price > 0 and sd > 0:
        prob_reach_target = round(1 - _phi(math.log(target_price / current_price) / sd), 4)

    prob_reach_target_momentum = None
    if (
        prob_reach_target is not None
        and momentum_annual_pct is not None
        and beta_sector is not None
    ):
        setor_pct = momentum_annual_pct * T
        central = max(0.0, 1 + (beta_sector * setor_pct) / 100)
        drift = math.log(max(central, 1e-6))
        prob_reach_target_momentum = round(
            1 - _phi((math.log(target_price / current_price) - drift) / sd), 4
        )

    news_items = []
    try:
        names = _company_names([ticker])
        news_items = news_for_ticker(ticker, 5, [ticker], names).get("news", [])
    except Exception as e:
        print(f"[entry_exit_study] news de {ticker}: {e}", file=sys.stderr)

    return {
        "ticker": ticker,
        "targetPrice": target_price,
        "targetDate": target_date,
        "currentPrice": round(current_price, 4),
        "avgLow1y": avg_low_1y,
        "minLow1y": min_low_1y,
        "avgLow6m": avg_low_6m,
        "minLow6m": min_low_6m,
        "entryPullbackPrice": entry_pullback_price,
        "volAnnual": vol_annual,
        "betaSector": beta_sector,
        "earningsDate": earnings_date,
        "daysUntilTarget": days_until,
        "probReachTarget": prob_reach_target,
        "probReachTargetMomentum": prob_reach_target_momentum,
        "momentumAnnualPct": momentum_annual_pct,
        "news": news_items,
        **({"earningsSetup": earnings_setup} if earnings_setup else {}),
        **({"entryBlockedByEarnings": True}
           if (earnings_setup or {}).get("entry_blocked") else {}),
        **({"fonteHistorico": resultado_hist.source}
           if resultado_hist.source not in ("yfinance", "yfinance_cache") else {}),
    }


def _traduzir_noticias(results: list) -> None:
    refs = []
    texts = []
    for r in results:
        for n in r.get("news") or []:
            for campo in ("title", "summary"):
                if n.get(campo):
                    refs.append((n, campo))
                    texts.append(n[campo])
    if not texts:
        return
    try:
        translated = translate_all(texts)
        if len(translated) == len(texts):
            for (n, campo), tr in zip(refs, translated):
                n[campo] = tr
    except Exception as e:
        print(f"[entry_exit_study] tradução falhou, mantendo originais: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}
    studies = args.get("studies") or []
    results = bounded_parallel_map(
        _study_for,
        studies,
        budget_s=budget_from_deadline(BUDGET_S, label="entry_exit_study"),
        label="entry_exit_study",
    )
    _traduzir_noticias(results)
    exit_now(json_seguro.dumps({"results": results}, ensure_ascii=False) + "\n")
