---
name: SKHY IPO — sem histórico de candles
description: SK Hynix (SKHY) estreou em 10/jul/2026 sem histórico de preços; SMA/RSI/MACD retornam null até haver dados suficientes
---

# SKHY (SK Hynix) — fase de descoberta de preço pós-IPO

## Contexto

- IPO/estreia: 10/jul/2026, ticker temporário SKHYV até 13/jul/2026 (regular trading passa a ser sob SKHY)
- Preço de oferta do ADR: $149; fechou o 1º dia em $168,01 (+13%), máxima intraday ~$177
- Liquidação do ADR: 14/jul/2026. Listagem das ações ordinárias na KOSPI: 29/jul/2026 (mesmo dia dos resultados do Q2 2026)
- Cada ADR representa fração da ação coreana (~1:10)

## Por que os indicadores técnicos ficam null

sma20/sma50/rsi/macd exigem 14-20 períodos mínimos de histórico. Sem candles anteriores ao IPO, essas funções simplesmente retornam null (sem erro, sem sinal) até acumular dados suficientes — não trate isso como bug.

## Duas fases

1. **Fase 1 — range de descoberta (10/jul a ~22/jul/2026, ~5-8 pregões)**: sem indicadores confiáveis. Monitorar só por rompimento do range absoluto $149 (piso, preço de oferta) – $177 (teto, máxima do dia 1). Não abrir posição nova nesta fase; tratar como observação, não entrada.
2. **Fase 2 — pós ~22/jul/2026**: SMA20/RSI/MACD começam a ter base mínima de dados para gatilhos técnicos normais. Mesmo assim, evitar abrir posição nova em 28-29/jul/2026 (véspera/dia dos resultados do Q2 + listagem KOSPI simultânea) por risco de gap duplo.

## Catalisadores no calendário

- 29/jul/2026: resultados Q2 2026 (receita esperada ~82,46 tri won vs 52,58 tri no Q1) + listagem das ordinárias na KOSPI no mesmo dia
- dez/2026: possível inclusão no Nasdaq 100 (rebalanceamento, fluxo passivo)
- set/2027: elegibilidade para o índice SOX (exige 3 meses listado)

## Cuidado se adicionar SKHY a settings.tickers

SKHY é "foreign private issuer" — pode arquivar 20-F em vez de 10-K na SEC EDGAR. Antes de adicionar SKHY a `settings.tickers`, confirmar se `get_fundamentals.py` / `TICKER_TO_CIK` (`artifacts/api-server/src/agent/tools.py`) reconhece o CIK e o tipo de filing certo; caso contrário a busca de fundamentos falha silenciosamente ou retorna dados de outro emissor. Ver também `ticker-source-of-truth.md`.
