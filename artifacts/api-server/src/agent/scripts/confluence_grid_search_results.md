# Grid search — ConfluenceEngine em MU, AVGO, MRVL (universo: MU, AVGO, MRVL)

## MU

### Com sector_returns (AVGO, MRVL)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | 3.36 | 1.67% | 0.894 | -2.61 | 48 | 45.8% |
| 5 | 5 | -0.06 | -0.03% | -0.030 | -1.19 | 24 | 33.3% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Sem sector_returns (ablação)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | -0.06 | -0.03% | -0.013 | -1.93 | 35 | 34.3% |
| 5 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Buy & hold MU (2y)

total_return_pct=656.71%, cagr=175.66%, sharpe=1.809, max_drawdown=-51.36%


### Recalibração de Kelly (min_votes=4, com setor)

win_rate real: 0.4583, avg_win real: 0.0609, avg_loss_abs real: 0.0302 (priors placeholder: 0.5/0.05/0.03)

total_return_pct antes: 3.36% -> depois: 3.19%


## AVGO

### Com sector_returns (MU, MRVL)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | -0.83 | -0.42% | -0.275 | -2.35 | 48 | 35.4% |
| 5 | 5 | -1.43 | -0.72% | -0.702 | -1.93 | 21 | 38.1% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Sem sector_returns (ablação)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | -1.43 | -0.72% | -0.647 | -1.99 | 28 | 32.1% |
| 5 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Buy & hold AVGO (2y)

total_return_pct=138.87%, cagr=54.69%, sharpe=1.073, max_drawdown=-41.15%


### Recalibração de Kelly (min_votes=4, com setor)

win_rate real: 0.3542, avg_win real: 0.0454, avg_loss_abs real: 0.0293 (priors placeholder: 0.5/0.05/0.03)

total_return_pct antes: -0.83% -> depois: 0.00%


## MRVL

### Com sector_returns (MU, AVGO)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | -2.90 | -1.46% | -0.400 | -7.90 | 47 | 31.9% |
| 5 | 5 | 1.35 | 0.68% | 0.348 | -2.12 | 16 | 43.8% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Sem sector_returns (ablação)

| min_votes | total_signals | total_return_pct | cagr | sharpe | max_drawdown_pct | num_trades | win_rate |
|---|---|---|---|---|---|---|---|
| 4 | 5 | -3.27 | -1.65% | -0.704 | -6.58 | 34 | 29.4% |
| 5 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |
| 6 | 5 | 0.00 | 0.00% | 0.000 | 0.00 | 0 | 0.0% |

### Buy & hold MRVL (2y)

total_return_pct=228.74%, cagr=81.54%, sharpe=1.179, max_drawdown=-60.79%


### Recalibração de Kelly (min_votes=5, com setor)

win_rate real: 0.4375, avg_win real: 0.1140, avg_loss_abs real: 0.0631 (priors placeholder: 0.5/0.05/0.03)

total_return_pct antes: 1.35% -> depois: 0.86%

