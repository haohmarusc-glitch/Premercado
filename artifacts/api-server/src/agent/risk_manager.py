"""Risk management calculator — standalone subprocess (imports only sibling security.py)."""
import sys, json
import numpy as np
import yfinance as yf
import pandas as pd
from security import sanitize_ticker
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro

try:  # import duplo: spawn por caminho e também como membro do pacote
    from agent import market_data_provider
except ImportError:
    import market_data_provider

def position_size(account_size: float, risk_pct: float, entry: float, stop: float) -> dict:
    if entry <= 0 or stop <= 0 or entry == stop:
        return {"error": "entry and stop must be positive and different"}
    risk_amount = account_size * (risk_pct / 100)
    risk_per_share = abs(entry - stop)
    shares = risk_amount / risk_per_share
    position_value = shares * entry
    return {
        "shares": round(shares, 4),
        "positionValue": round(position_value, 2),
        "riskAmount": round(risk_amount, 2),
        "riskPerShare": round(risk_per_share, 4),
        "accountPct": round(position_value / account_size * 100, 2),
    }

def risk_reward(entry: float, stop: float, target: float) -> dict:
    if entry <= 0 or stop <= 0 or target <= 0:
        return {"error": "all prices must be positive"}
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return {"error": "entry and stop cannot be equal"}
    ratio = reward / risk
    return {
        "risk": round(risk, 4),
        "reward": round(reward, 4),
        "ratio": round(ratio, 2),
        "favorable": ratio >= 2.0,
    }

def stop_distance(ticker: str, period: str = "3mo", atr_multiplier: float = 2.0) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
        # Cadeia sem fonte externa: a série é ajustada, e um split na janela
        # viraria degrau -- um ATR calculado sobre o degrau sugeriria um stop
        # absurdo. O cache vencido serve (foi gravado do yfinance, ajustado).
        resultado = market_data_provider.get_daily_history(
            ticker, period, auto_adjust=True, permitir_externa=False
        )
        df = resultado.df if resultado.ok else pd.DataFrame()
        if df.empty or len(df) < 15:
            return {"error": "Insufficient data"}
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        current_price = float(close.iloc[-1])
        stop = current_price - atr_multiplier * atr
        return {
            "ticker": ticker,
            "currentPrice": round(current_price, 2),
            "atr14": round(float(atr), 4),
            "atrMultiplier": atr_multiplier,
            "suggestedStop": round(float(stop), 2),
            "stopDistancePct": round(atr_multiplier * float(atr) / current_price * 100, 2),
        }
    except Exception as e:
        return {"error": str(e)}

def portfolio_exposure(positions: list) -> dict:
    """Concentração da carteira POR POSIÇÃO. Não por setor -- e isso importa.

    `sector_map` existia aqui declarado e nunca preenchido, sobra de uma
    intenção que nunca foi implementada. O lint o encontrou, e ele apontava
    para um problema maior que ele mesmo: numa carteira de NVDA, MRVL, AMD,
    ARM, SMCI e TSM, esta função devolve `concentrationRisk: "LOW"` sempre que
    nenhum papel isolado passa de 15% -- enquanto a carteira inteira é
    semicondutor. O número está certo para a pergunta que ele responde, e a
    pergunta não é a que o nome sugere.

    Implementar concentração setorial de verdade precisa de um mapa
    ticker->setor COMPLETO. O que existe (`SECTOR_GROUPS`, em
    sector_contagion.py) foi montado para contágio entre pares e cobre uma
    parte dos papéis; usá-lo aqui produziria "concentração baixa" para o que
    ele não classifica, que é o mesmo defeito com outra roupa.

    Então, por ora, a saída DECLARA a base: quem lê "LOW" vê ao lado que isso
    é por posição, não por setor. Ver a nota de `baseDaConcentracao`.
    """
    total_invested = sum(float(p.get("investedAmount", 0)) for p in positions)
    ticker_pcts = []

    for p in positions:
        ticker = p.get("ticker", "")
        invested = float(p.get("investedAmount", 0))
        pct = (invested / total_invested * 100) if total_invested > 0 else 0
        ticker_pcts.append({
            "ticker": ticker,
            "investedAmount": invested,
            "pct": round(pct, 2),
        })

    max_single = max((t["pct"] for t in ticker_pcts), default=0)
    concentration_risk = "HIGH" if max_single > 30 else "MEDIUM" if max_single > 15 else "LOW"

    return {
        "totalPositions": len(positions),
        "totalInvested": round(total_invested, 2),
        "tickers": ticker_pcts,
        "maxSinglePositionPct": round(max_single, 2),
        "concentrationRisk": concentration_risk,
        # A base viaja junto com o veredito -- mesma razão do `smaOrigem` no
        # snapshot: sem ela, "LOW" se lê como "a carteira está diversificada",
        # que é uma afirmação que esta função não faz.
        "baseDaConcentracao": "posicao_individual",
        "concentracaoSetorialAvaliada": False,
    }

def correlation(tickers: list, period: str = "6mo") -> dict:
    """Correlacao de Pearson entre os retornos diarios dos tickers informados.
    Objetivo: expor concentracao de risco "escondida" -- posicoes dolarizadas
    de forma diversificada podem estar todas apostando na mesma coisa se os
    retornos sao altamente correlacionados (comum numa cesta de
    semicondutores/IA)."""
    try:
        clean = []
        seen = set()
        for t in tickers:
            try:
                s = sanitize_ticker(t)
            except ValueError:
                continue
            if s not in seen:
                seen.add(s)
                clean.append(s)
        if len(clean) < 2:
            return {"error": "Precisa de pelo menos 2 tickers válidos"}

        lote = market_data_provider.get_daily_closes_batch(
            clean, period, auto_adjust=True, permitir_externa=False
        )
        if not lote.ok:
            return {"error": "Dados insuficientes"}
        closes = lote.closes

        returns = closes.pct_change().dropna(how="all")
        available = [t for t in clean if t in returns.columns and returns[t].notna().sum() >= 20]
        if len(available) < 2:
            return {"error": "Dados insuficientes para calcular correlação"}

        corr = returns[available].corr(min_periods=20)

        matrix = [[
            round(float(corr.loc[a, b]), 3) if not pd.isna(corr.loc[a, b]) else None
            for b in available
        ] for a in available]

        pairs = []
        for i, a in enumerate(available):
            for b in available[i + 1:]:
                v = corr.loc[a, b]
                if pd.isna(v):
                    continue
                pairs.append({"a": a, "b": b, "correlation": round(float(v), 3)})
        pairs.sort(key=lambda p: -abs(p["correlation"]))

        high = [p for p in pairs if abs(p["correlation"]) >= 0.8]

        skipped = [t for t in clean if t not in available]
        degradadas = {t: f for t, f in lote.degradadas.items() if t in available}
        return {
            "tickers": available,
            "matrix": matrix,
            "pairs": pairs,
            "highCorrelationPairs": high,
            "skipped": skipped,
            # Só aparece quando alguma série veio degradada -- uma correlação
            # calculada sobre cache de ontem continua útil, mas tem que dizer.
            **({"fontesDegradadas": degradadas} if degradadas else {}),
        }
    except Exception as e:
        return {"error": str(e)}

def portfolio_risk_metrics(positions: list, period: str = "1y", risk_free_rate: float = 0.045) -> dict:
    """Sharpe ratio, max drawdown e VaR histórico (95%) da carteira, ponderada
    pelo valor investido de cada posição -- não pelo peso igual entre ativos.

    Sharpe = retorno excedente anualizado / volatilidade anualizada (retorno
    médio diário × 252 − risk_free_rate, sobre desvio-padrão diário × √252).
    Compara o retorno da carteira ajustado ao risco contra simplesmente
    segurar T-bills (risk_free_rate, default 4.5% -- ajuste se a Selic/Fed
    funds mudar bastante desde então).

    Max drawdown = maior queda percentual do pico ao vale na série de valor
    da carteira no período -- "quanto você já teria perdido do topo, no pior
    momento", diferente da perda atual vs. custo (métrica de Cenários).

    VaR 95% histórico (1 dia) = 5º percentil da distribuição de retornos
    diários da carteira, em % e em dólar sobre o total investido -- "em 95%
    dos dias, a perda de 1 dia não deveria passar disso" (método histórico,
    sem assumir distribuição normal -- mais robusto a caudas gordas que o
    VaR paramétrico)."""
    try:
        total_invested = sum(float(p.get("investedAmount", 0)) for p in positions)
        if total_invested <= 0:
            return {"error": "Carteira sem valor investido"}

        clean: list[tuple[str, float]] = []
        seen = set()
        for p in positions:
            try:
                t = sanitize_ticker(p.get("ticker", ""))
            except ValueError:
                continue
            if t in seen:
                continue
            invested = float(p.get("investedAmount", 0))
            if invested <= 0:
                continue
            seen.add(t)
            clean.append((t, invested))
        if len(clean) < 1:
            return {"error": "Nenhuma posição válida"}

        tickers = [t for t, _ in clean]
        lote = market_data_provider.get_daily_closes_batch(
            tickers, period, auto_adjust=True, permitir_externa=False
        )
        if not lote.ok:
            return {"error": "Dados insuficientes para calcular métricas de risco"}
        closes = lote.closes

        returns = closes.pct_change().dropna(how="all")
        available = [(t, w) for t, w in clean if t in returns.columns and returns[t].notna().sum() >= 20]
        if not available:
            return {"error": "Dados insuficientes para calcular métricas de risco"}

        avail_tickers = [t for t, _ in available]
        weight_sum = sum(w for _, w in available)
        weights = pd.Series({t: w / weight_sum for t, w in available})

        # Só os dias em que TODOS os ativos disponíveis têm retorno (dropna
        # "any") -- um dia com dado faltando pra 1 ativo distorceria o
        # retorno agregado daquele dia se contasse só os demais.
        aligned = returns[avail_tickers].dropna(how="any")
        if len(aligned) < 20:
            return {"error": "Histórico comum insuficiente entre os ativos (menos de 20 dias)"}

        portfolio_returns = aligned @ weights

        mean_daily = float(portfolio_returns.mean())
        std_daily = float(portfolio_returns.std())
        sharpe = None
        if std_daily > 0:
            excess_annual = mean_daily * 252 - risk_free_rate
            # float() explícito -- numpy.sqrt/round devolvem np.float64, que
            # json.dumps não sabe serializar (TypeError silencioso na resposta).
            sharpe = round(float(excess_annual / (std_daily * float(np.sqrt(252)))), 3)

        equity_curve = (1 + portfolio_returns).cumprod()
        running_peak = equity_curve.cummax()
        drawdown_series = (equity_curve - running_peak) / running_peak
        max_drawdown_pct = round(float(drawdown_series.min()) * 100, 2)

        var_95_pct = round(float(np.percentile(portfolio_returns, 5)) * 100, 2)
        var_95_usd = round(abs(var_95_pct) / 100 * total_invested, 2)

        skipped = [t for t in tickers if t not in avail_tickers]
        return {
            "tickers": avail_tickers,
            "period": period,
            "daysUsed": len(aligned),
            "totalInvested": round(total_invested, 2),
            "riskFreeRate": risk_free_rate,
            "sharpeRatio": sharpe,
            "maxDrawdownPct": max_drawdown_pct,
            "var95Pct": var_95_pct,
            "var95Usd": var_95_usd,
            "annualizedVolatilityPct": round(std_daily * float(np.sqrt(252)) * 100, 2),
            "skipped": skipped,
        }
    except Exception as e:
        return {"error": str(e)}


def intraday_beta(
    base_ticker: str,
    hedge_ticker: str,
    target_capital: float,
    interval: str = "5m",
    window: int = 24,
    period: str = "1d",
    winsorize_std: float = 3.0,
) -> dict:
    """Beta intraday deslizante (rolling) de hedge_ticker em relação a
    base_ticker, e alocação sugerida em hedge_ticker pra igualar a exposição
    de volatilidade de target_capital investido em base_ticker.

    window=24 (padrão) em candles de 5min = ~2h de histórico -- testado com
    dados sintéticos: janela de 12 (1h, valor do protótipo original) tem
    desvio-padrão do beta estimado ~3-4x maior que janela de 24-36 (muito
    ruidoso pra calibrar financeiro de posição real). Ver PR pra números.

    winsorize_std: retornos de candle além de N desvios-padrão do próprio dia
    são "achatados" pro limite antes de calcular covariância/variância --
    um único candle de desequilíbrio de ordem (comum nos primeiros dias de
    um IPO) senão contamina as próximas `window` janelas deslizantes.
    """
    try:
        base_ticker = sanitize_ticker(base_ticker)
        hedge_ticker = sanitize_ticker(hedge_ticker)
        if target_capital <= 0:
            return {"error": "targetCapital deve ser positivo"}

        data = yf.download([base_ticker, hedge_ticker], period=period, interval=interval,
                            auto_adjust=True, progress=False)
        closes = data["Close"] if "Close" in data else data
        if not hasattr(closes, "columns") or base_ticker not in closes.columns or hedge_ticker not in closes.columns:
            return {"error": "Dados insuficientes ou ticker(s) sem candles no período/intervalo pedido"}

        returns = closes[[base_ticker, hedge_ticker]].pct_change().dropna(how="any")
        if len(returns) < window:
            return {
                "error": f"Dados insuficientes: {len(returns)} candles disponíveis, "
                         f"janela pede {window} (comum antes da abertura ou nos primeiros candles do pregão)"
            }

        # Winsorize por ticker usando o desvio-padrão DO PRÓPRIO DIA (não
        # rolling) -- simples e suficiente pra achatar 1-2 outliers isolados
        # sem exigir uma segunda janela deslizante só pra isso.
        clipped = returns.copy()
        for col in (base_ticker, hedge_ticker):
            std = clipped[col].std()
            mean = clipped[col].mean()
            if std > 0:
                clipped[col] = clipped[col].clip(mean - winsorize_std * std, mean + winsorize_std * std)

        rolling_cov = clipped[hedge_ticker].rolling(window=window).cov(clipped[base_ticker])
        rolling_var = clipped[base_ticker].rolling(window=window).var()
        rolling_beta = (rolling_cov / rolling_var).dropna()

        if rolling_beta.empty:
            return {"error": "Não foi possível calcular beta (variância zero ou dados insuficientes)"}

        beta = float(rolling_beta.iloc[-1])
        warning = None
        if len(returns) < window * 2:
            warning = (f"Só {len(returns)} candles disponíveis (menos que 2x a janela) -- "
                       f"beta ainda pouco estável, tratar como indicativo, não confiável pra hedge real")
        if beta == 0:
            return {"error": "Beta calculado é zero -- não dá pra sugerir alocação (divisão por zero)"}

        suggested_hedge_capital = target_capital / beta
        return {
            "baseTicker": base_ticker,
            "hedgeTicker": hedge_ticker,
            "beta": round(beta, 4),
            "windowUsed": window,
            "barsAvailable": len(returns),
            "targetCapital": target_capital,
            "suggestedHedgeCapital": round(suggested_hedge_capital, 2),
            "asOf": str(returns.index[-1]),
            "warning": warning,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    action = args.get("action")
    if action == "position_size":
        result = position_size(
            float(args["accountSize"]), float(args["riskPct"]),
            float(args["entry"]), float(args["stop"]),
        )
    elif action == "risk_reward":
        result = risk_reward(float(args["entry"]), float(args["stop"]), float(args["target"]))
    elif action == "stop_distance":
        result = stop_distance(
            args["ticker"],
            args.get("period", "3mo"),
            float(args.get("atrMultiplier", 2.0)),
        )
    elif action == "portfolio_exposure":
        result = portfolio_exposure(args.get("positions", []))
    elif action == "correlation":
        result = correlation(args.get("tickers", []), args.get("period", "6mo"))
    elif action == "portfolio_risk_metrics":
        result = portfolio_risk_metrics(
            args.get("positions", []),
            args.get("period", "1y"),
            float(args.get("riskFreeRate", 0.045)),
        )
    elif action == "intraday_beta":
        result = intraday_beta(
            args["baseTicker"], args["hedgeTicker"], float(args["targetCapital"]),
            args.get("interval", "5m"), int(args.get("window", 24)),
            args.get("period", "1d"), float(args.get("winsorizeStd", 3.0)),
        )
    else:
        result = {"error": f"Unknown action: {action}"}
    print(json_seguro.dumps(result))
