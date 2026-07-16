---
name: TSM earnings swing — entrada pós-resultado, saída em 3 pregões
description: TSM reporta 16/07/2026 pré-mercado com expectativas já muito altas; plano é confirmar a reação real antes de entrar, não comprar às cegas antes do resultado
---

## REVISÃO 16/07/2026 09h~ ET: o gap foi de BAIXA, não de alta

O resultado saiu excelente (receita +34-36% YoY, lucro líquido +77-80% YoY,
recorde; guidance de receita e capex do ano ELEVADOS) — mas a ação abriu o
pré-mercado em queda forte: fechamento anterior $420,39 → pré-mercado
~$403,40 (**-3,83%**), print do próprio usuário via TradingView. A queda
bateu forte logo após a divulgação (~2h ET) e desde então vem consolidando
entre ~$400-404. Confirma exatamente o risco de "sell the news" já
sinalizado antes do resultado (expectativa "excepcionalmente alta demais",
citação de gestor via MarketWatch) — e um selloff mais amplo no setor de
semicondutores de IA no mesmo dia (AMD, Dell, Intel, MU também caindo).

**O plano original abaixo (confirmar gap de ALTA segurando com volume) NÃO
se aplica mais.** Nova tese, contrária: com fundamentos genuinamente fortes
e consenso de analistas "Strong Buy" (PT $493, ~22% de upside do preço de
pré-mercado), pode valer comprar a queda -- mas só COM confirmação de
estabilização na abertura regular, não às cegas. Ver critério de
confirmação atualizado em
`.agents/scheduled-tasks/tsm-earnings-swing-entry/SKILL.md`.

# TSM (Taiwan Semiconductor) — swing de earnings, 16-21/07/2026

## Contexto (levantado 16/07/2026, dados reais via FMP + Alpha Vantage)

- Resultado (Q2 2026, fiscal encerrado 30/06): hoje, 16/07/2026, pré-mercado (~2h ET). EPS consensus $3,80, alta de 59% YoY esperada no lucro líquido — 5º trimestre seguido de recorde.
- Fechamento 15/07/2026: $419,48, RSI(14)=45,4 (neutro), volume normal (1,14x média 20d), ~12% abaixo da máxima de 52 semanas ($477,57)
- Free float: 99,9% do total de ações — sem concentração, sem mecânica de squeeze (diferente do caso SMCI)
- Manchetes pré-resultado uniformemente otimistas (Reuters, Benzinga, Wedbush Outperform) — expectativa já muito alta ("priced for perfection")
- Risco citado (Invezz): guidance de capex de IA relacionado à Nvidia pode "descarrilhar o rali" mesmo com lucro recorde

## Por que NÃO comprar antes de ver a reação

Com expectativa já tão alta, um "beat" apenas em linha pode cair (sell the news). Diferente de um setup de reversão técnica (tipo SMCI), aqui a decisão de entrada só faz sentido DEPOIS de confirmar a reação real do mercado ao número.

## Plano

1. **Confirmação de entrada (hoje, ~10h15 ET / 30-45min após abertura)**: checar se o gap de abertura (pra cima, no lucro recorde) se sustenta com volume acima do normal, mesmo método ORB já usado no plano de SKHY — não comprar se estiver fazendo fade (voltando pro fechamento anterior) nos primeiros minutos.
2. **Se confirmado**: entrada no rompimento do range inicial com volume, ordem limitada (nunca a mercado).
3. **Saída em 3 pregões**: regra fixa, não estender. Contando hoje (16/07, quinta) como dia da entrada: 17/07 (sex), 20/07 (seg, pula fim de semana), **21/07 (ter) é o 3º pregão — saída até ~15h45 ET desse dia**, independente do P&L, pra não virar posição de convicção sem tese nova.
4. **Se NÃO confirmado** (gap fecha ou volume fraco): não entrar. Reavaliar sem pressa — não é a única janela de entrada do papel.
