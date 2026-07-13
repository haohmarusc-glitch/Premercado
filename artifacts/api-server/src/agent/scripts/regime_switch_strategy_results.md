# Backtest: filtro de regime (SMA100 e SMA200) liga/desliga a estratégia de sinal europeu

Testa se um filtro de tendência simples (Close do Nasdaq vs. sua própria SMA100 e SMA200) detecta automaticamente o mesmo regime que rotulamos na mão em backtest_global_signal_strategy.py -- e se o híbrido resultante (buy&hold passivo em alta, sinal europeu fora de alta) preserva o edge da correção sem perder tanto no rali. Compara SMA100 (já testada em PR #57, rápida/ruidosa) com SMA200 (mais lenta/pegajosa) lado a lado, pra ver se a janela mais lenta reduz as trocas falsas de regime dentro de um rali sustentado. Retorno/Sharpe reportados BRUTO (sem custo) e LÍQUIDO (custo igual a backtest.py, cobrado a cada troca de posição, inclusive a troca causada pelo próprio filtro de regime).

## recente_2y (supercycle HBM/IA)

Alvo: Nasdaq Composite (^IXIC). Período de teste: 2024-07-15 a 2026-07-10.

Filtro de regime (Close > SMA100): **78.4% dos dias classificados 'alta'**.

Filtro de regime (Close > SMA200): **87.4% dos dias classificados 'alta'**.

Buy & hold: retorno=42.27%, cagr=19.44%, sharpe=0.909, max_dd=-24.32%

| Estratégia | Retorno (bruto) | Retorno (líquido) | Sharpe (bruto) | Sharpe (líquido) | Max DD (líquido) | Trades | Win rate (líquido) |
|---|---|---|---|---|---|---|---|
| sinal europeu sempre ligado (long/flat) | 24.40% | -14.13% | 1.009 | -0.598 | -19.60% | 269 | 56.5% |
| híbrido: SMA100 liga/desliga (long/flat) | 0.35% | -8.01% | 0.084 | -0.224 | -20.08% | 447 | 55.7% |
| híbrido: SMA200 liga/desliga (long/flat) | -0.92% | -5.00% | 0.045 | -0.095 | -19.93% | 468 | 55.3% |
| sinal europeu sempre ligado (long/short) | 36.41% | -34.66% | 0.951 | -1.062 | -39.96% | 495 | 46.7% |
| híbrido: SMA100 liga/desliga (long/short) | -10.04% | -24.44% | -0.196 | -0.671 | -33.47% | 497 | 54.1% |
| híbrido: SMA200 liga/desliga (long/short) | -11.84% | -19.01% | -0.252 | -0.478 | -33.17% | 497 | 54.7% |


## correcao_2022_2023 (memory downcycle + selloff de juros)

Alvo: Nasdaq Composite (^IXIC). Período de teste: 2022-01-03 a 2023-06-29.

Filtro de regime (Close > SMA100): **36.9% dos dias classificados 'alta'**.

Filtro de regime (Close > SMA200): **29.4% dos dias classificados 'alta'**.

Buy & hold: retorno=-14.16%, cagr=-9.78%, sharpe=-0.220, max_dd=-35.49%

| Estratégia | Retorno (bruto) | Retorno (líquido) | Sharpe (bruto) | Sharpe (líquido) | Max DD (líquido) | Trades | Win rate (líquido) |
|---|---|---|---|---|---|---|---|
| sinal europeu sempre ligado (long/flat) | 78.83% | 35.15% | 2.378 | 1.276 | -14.69% | 193 | 62.7% |
| híbrido: SMA100 liga/desliga (long/flat) | 61.82% | 35.60% | 1.869 | 1.217 | -14.69% | 255 | 58.4% |
| híbrido: SMA200 liga/desliga (long/flat) | 74.53% | 41.94% | 2.195 | 1.414 | -15.34% | 242 | 59.9% |
| sinal europeu sempre ligado (long/short) | 195.36% | 69.75% | 3.254 | 1.648 | -15.19% | 371 | 58.2% |
| híbrido: SMA100 liga/desliga (long/short) | 140.98% | 69.22% | 2.639 | 1.623 | -15.19% | 373 | 59.2% |
| híbrido: SMA200 liga/desliga (long/short) | 179.76% | 85.07% | 3.081 | 1.889 | -16.03% | 373 | 60.1% |
