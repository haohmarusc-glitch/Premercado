import sys, json
import numpy as np

try:  # import duplo: spawn por caminho (checker diário) e como membro do pacote
    from agent import market_data_provider
except ImportError:
    import market_data_provider

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

# Momentum do benchmark (não por ticker) -- retorno dos últimos MOMENTUM_
# LOOKBACK_DAYS pregões, anualizado por (252/lookback), reaproveitando o
# mesmo histórico já baixado pra vol/beta (sem chamada de rede extra).
# Alimenta só uma SUGESTÃO no slider "Movimento do setor" de /cenarios
# (scenario-params-checker.ts grava em sector_momentum) -- é extrapolação de
# momentum passado, não previsão; o usuário sempre pode sobrescrever.
MOMENTUM_LOOKBACK_DAYS = 90


def _sector_momentum(closes, benchmark: str) -> dict | None:
    if benchmark not in closes.columns:
        return None
    bench_close = closes[benchmark].dropna()
    if len(bench_close) <= MOMENTUM_LOOKBACK_DAYS:
        return None
    window = bench_close.iloc[-MOMENTUM_LOOKBACK_DAYS:]
    total_return = float(window.iloc[-1] / window.iloc[0] - 1)
    annual_pct = total_return * (252 / MOMENTUM_LOOKBACK_DAYS) * 100
    return {"benchmark": benchmark, "momentumAnnualPct": round(annual_pct, 2), "lookbackDays": MOMENTUM_LOOKBACK_DAYS}


def compute(tickers: list[str], benchmark: str) -> dict:
    result: dict = {}
    if not tickers:
        return {"params": result, "sectorMomentum": None}

    all_symbols = list(dict.fromkeys(tickers + [benchmark]))
    # Lote com fallback POR TICKER (get_daily_closes_batch): rede saudável
    # continua sendo UM yf.download; numa queda do Yahoo cada símbolo desce a
    # cadeia individual e vem do cache. permitir_externa=False porque a série
    # é ajustada -- vol e beta calculados sobre o degrau de um split sairiam
    # absurdos, e são exatamente os números que alimentam o Painel de
    # Cenários e o Estudo de Entrada/Saída.
    lote = market_data_provider.get_daily_closes_batch(
        all_symbols, PERIOD, auto_adjust=True, permitir_externa=False
    )
    if not lote.ok:
        err = "; ".join(lote.warnings) or "Sem dados de preço"
        return {"params": {t: {"error": err} for t in tickers}, "sectorMomentum": None}
    closes = lote.closes
    returns = closes.pct_change().dropna(how="all")

    if benchmark not in returns.columns or returns[benchmark].notna().sum() < MIN_DAYS:
        return {
            "params": {t: {"error": f"Sem histórico suficiente do benchmark {benchmark}"} for t in tickers},
            "sectorMomentum": None,
        }

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

    saida = {"params": result, "sectorMomentum": _sector_momentum(closes, benchmark)}
    # Só aparece quando alguma série veio degradada. Vol/beta sobre cache de
    # ontem continuam úteis (mudam devagar), mas quem persiste o resultado
    # (scenario-params-checker) e quem o consome no estudo tem que poder saber.
    degradadas = lote.degradadas
    if degradadas:
        saida["fontesDegradadas"] = degradadas
    return saida


if __name__ == "__main__":
    tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()] if len(sys.argv) > 1 else []
    benchmark = sys.argv[2].strip().upper() if len(sys.argv) > 2 else "SMH"
    print(json.dumps(compute(tickers, benchmark)))
