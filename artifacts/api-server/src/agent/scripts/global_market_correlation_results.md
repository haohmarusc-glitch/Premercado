# Correlação: mercados globais overnight x gap de abertura da Nasdaq

Mede se a variação do fechamento de Nikkei/KOSPI/Hang Seng/DAX/FTSE/CAC/EUR-USD/futuros Nasdaq antecipa o gap de abertura da Nasdaq (^IXIC) no mesmo dia de calendário. `corr_gap`/`hit_rate_gap` usam o gap real (Open de hoje vs. Close de ontem da Nasdaq); `corr_day` usa a variação do pregão inteiro, pra ver se o efeito (quando existe) sobrevive além da abertura ou se dilui ao longo do dia. NÃO testa nenhuma estratégia de compra/venda -- só mede se o dado tem poder preditivo antes de decidir se vale a pena construir um sinal em cima dele.

## recente_2y (supercycle HBM/IA)

Alvo: Nasdaq Composite (^IXIC). Período: 2024-07-11 a 2026-07-10.

| Mercado | Ticker | N dias | Corr. x gap abertura | Hit-rate direção (%) | Corr. x variação dia todo |
|---|---|---|---|---|---|
| Nikkei 225 (Japao) | ^N225 | 465 | 0.424 | 61.9% | 0.111 |
| KOSPI Composite (Coreia) | ^KS11 | 467 | 0.318 | 60.8% | 0.105 |
| Hang Seng (Hong Kong) | ^HSI | 476 | 0.266 | 58.8% | 0.101 |
| DAX (Alemanha) | ^GDAXI | 490 | 0.484 | 66.1% | 0.318 |
| FTSE 100 (Reino Unido) | ^FTSE | 492 | 0.418 | 59.1% | 0.182 |
| CAC 40 (Franca) | ^FCHI | 494 | 0.479 | 66.8% | 0.269 |
| EUR/USD | EURUSD=X | 497 | -0.086 | 49.2% | -0.009 |
| Nasdaq 100 futuros | NQ=F | 499 | 0.565 | 70.7% | 0.970 |


## correcao_2022_2023 (memory downcycle + selloff de juros)

Alvo: Nasdaq Composite (^IXIC). Período: 2022-01-03 a 2023-06-29.

| Mercado | Ticker | N dias | Corr. x gap abertura | Hit-rate direção (%) | Corr. x variação dia todo |
|---|---|---|---|---|---|
| Nikkei 225 (Japao) | ^N225 | 350 | 0.149 | 55.4% | 0.170 |
| KOSPI Composite (Coreia) | ^KS11 | 351 | 0.205 | 59.8% | 0.133 |
| Hang Seng (Hong Kong) | ^HSI | 353 | 0.247 | 60.3% | 0.130 |
| DAX (Alemanha) | ^GDAXI | 370 | 0.596 | 72.7% | 0.488 |
| FTSE 100 (Reino Unido) | ^FTSE | 362 | 0.463 | 64.3% | 0.324 |
| CAC 40 (Franca) | ^FCHI | 370 | 0.567 | 72.2% | 0.460 |
| EUR/USD | EURUSD=X | 373 | -0.036 | 49.3% | -0.017 |
| Nasdaq 100 futuros | NQ=F | 373 | 0.546 | 68.6% | 0.989 |
