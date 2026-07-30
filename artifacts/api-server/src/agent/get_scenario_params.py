import sys, json
import numpy as np
import yfinance as yf

# Recalcula vol_annual/beta_sector de scenario_params (Painel de Cenários) a
# partir do histórico real de preços, em vez dos valores fixos digitados à
# mão na migração 0022 -- ver scenario-params-checker.ts, que roda este
# script uma vez por dia e faz upsert do resultado.
#
# vol_annual = desvio-padrão dos retornos log diários dos últimos 12 meses,
# anualizado por raiz(252) -- mesma fórmula de portfolio_risk_metrics em
# risk_manager.py (annualizedVolatilityPct), só que por ticker isolado.
# beta_sector = cov(retornos do ticker, retornos do benchmark) / var(retornos
# do benchmark) no mesmo período -- beta clássico de regressão linear
# simples contra um índice/ETF setorial (default SMH, o mais usado no resto
# do agente pra semicondutores -- ver BELLWETHERS em market_alerts.py).
MIN_DAYS = 30
PERIOD = "1y"


def compute(tickers: list[str], benchmark: str) -> dict:
    result: dict = {}
    if not tickers:
        return result

    all_symbols = list(dict.fromkeys(tickers + [benchmark]))
    try:
        data = yf.download(all_symbols, period=PERIOD, interval="1d", auto_adjust=True, progress=False)
        closes = data["Close"] if "Close" in data else data
        # yf.download com 1 único símbolo devolve Series em vez de DataFrame
        # de 1 coluna -- normaliza pra sempre poder indexar por nome (mesmo
        # ajuste de portfolio_risk_metrics em risk_manager.py).
        if not hasattr(closes, "columns"):
            closes = closes.to_frame(name=all_symbols[0])
        returns = closes.pct_change().dropna(how="all")
    except Exception as e:
        err = str(e)
        return {t: {"error": err} for t in tickers}

    if benchmark not in returns.columns or returns[benchmark].notna().sum() < MIN_DAYS:
        return {t: {"error": f"Sem histórico suficiente do benchmark {benchmark}"} for t in tickers}

    for t in tickers:
        if t not in returns.columns:
            result[t] = {"error": "Sem dados de preço"}
            continue
        try:
            if t == benchmark:
                aligned = returns[[t]].dropna(how="any")
                bench_col = aligned[t]
            else:
                aligned = returns[[t, benchmark]].dropna(how="any")
                bench_col = aligned[benchmark]
            if len(aligned) < MIN_DAYS:
                result[t] = {"error": "Histórico insuficiente"}
                continue

            std_daily = float(aligned[t].std())
            vol_annual = std_daily * float(np.sqrt(252))

            if t == benchmark:
                beta = 1.0
            else:
                bench_var = float(bench_col.var())
                beta = float(aligned[t].cov(bench_col) / bench_var) if bench_var > 0 else 1.0

            result[t] = {
                "volAnnual": round(vol_annual, 4),
                "betaSector": round(beta, 4),
                "daysUsed": len(aligned),
            }
        except Exception as e:
            result[t] = {"error": str(e)}

    return result


if __name__ == "__main__":
    tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()] if len(sys.argv) > 1 else []
    benchmark = sys.argv[2].strip().upper() if len(sys.argv) > 2 else "SMH"
    print(json.dumps(compute(tickers, benchmark)))
