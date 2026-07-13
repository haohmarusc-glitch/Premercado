# Backtest: estratégia de sinal global (Europa/Ásia) vs. buy & hold da Nasdaq

Testa se o sinal de correlação encontrado em global_market_correlation_results.md (Europa e Ásia batem o gap de abertura da Nasdaq com hit-rate > 50%, EUR/USD não) vira retorno de verdade. Retorno simulado é abertura -> fechamento da Nasdaq no mesmo dia (o que dava pra capturar agindo no sinal antes/na abertura), não fechamento -> fechamento (que já estaria contaminado pelo próprio gap testado). Compara com buy & hold do índice inteiro no mesmo período. Reporta retorno/Sharpe BRUTO (sem custo) e LÍQUIDO (com commission_pct/slippage_pct iguais aos defaults de backtest.py, cobrados por perna a cada troca de posição) lado a lado.

## recente_2y (supercycle HBM/IA)

Alvo: Nasdaq Composite (^IXIC). Período: 2024-07-11 a 2026-07-10.

Buy & hold: retorno=43.75%, cagr=19.94%, sharpe=0.931, max_dd=-24.32%

Custo modelado (igual backtest.py): commission_pct=0.001, slippage_pct=0.0005 por perna, cobrado a cada troca de posição.

| Sinal | Posição | Retorno (bruto) | Retorno (líquido) | Sharpe (bruto) | Sharpe (líquido) | Max DD (líquido) | Trades | Win rate (líquido) |
|---|---|---|---|---|---|---|---|---|
| europa (DAX+CAC+FTSE) | long/flat | 25.05% | -13.56% | 1.033 | -0.570 | -19.36% | 271 | 56.5% |
| europa (DAX+CAC+FTSE) | long/short | 37.21% | -34.48% | 0.965 | -1.052 | -39.96% | 497 | 46.7% |
| asia (Nikkei+KOSPI+HSI) | long/flat | -9.01% | -35.39% | -0.356 | -1.847 | -38.67% | 301 | 51.8% |
| asia (Nikkei+KOSPI+HSI) | long/short | -26.64% | -63.05% | -0.755 | -2.599 | -67.63% | 500 | 45.4% |
| combinado (europa+asia) | long/flat | -0.21% | -29.99% | 0.050 | -1.453 | -33.98% | 303 | 53.5% |
| combinado (europa+asia) | long/short | -11.80% | -56.62% | -0.249 | -2.158 | -62.12% | 501 | 44.9% |


## correcao_2022_2023 (memory downcycle + selloff de juros)

Alvo: Nasdaq Composite (^IXIC). Período: 2022-01-03 a 2023-06-29.

Buy & hold: retorno=-14.16%, cagr=-9.78%, sharpe=-0.220, max_dd=-35.49%

Custo modelado (igual backtest.py): commission_pct=0.001, slippage_pct=0.0005 por perna, cobrado a cada troca de posição.

| Sinal | Posição | Retorno (bruto) | Retorno (líquido) | Sharpe (bruto) | Sharpe (líquido) | Max DD (líquido) | Trades | Win rate (líquido) |
|---|---|---|---|---|---|---|---|---|
| europa (DAX+CAC+FTSE) | long/flat | 81.46% | 37.55% | 2.456 | 1.357 | -14.10% | 192 | 62.5% |
| europa (DAX+CAC+FTSE) | long/short | 199.70% | 72.24% | 3.306 | 1.695 | -15.19% | 370 | 58.1% |
| asia (Nikkei+KOSPI+HSI) | long/flat | 16.12% | -10.41% | 0.687 | -0.360 | -32.93% | 187 | 55.1% |
| asia (Nikkei+KOSPI+HSI) | long/short | 21.17% | -27.92% | 0.663 | -0.802 | -48.13% | 373 | 51.5% |
| combinado (europa+asia) | long/flat | 58.57% | 26.07% | 1.995 | 1.042 | -14.43% | 188 | 61.2% |
| combinado (europa+asia) | long/short | 125.54% | 42.62% | 2.447 | 1.135 | -23.58% | 373 | 57.9% |
