---
name: SMCI — squeeze + reversão técnica, gate earnings 04/ago
description: SMCI em tendência de baixa (-56% da máxima de 52 sem.), sem reversão técnica confirmada ainda; earnings de 04/08/2026 é catalisador estrutural mas só vale combinado com RSI<30 + volume de pânico
---

# SMCI — monitoramento de squeeze + reversão técnica

## Contexto (levantado em 16/07/2026, dados reais via FMP + Alpha Vantage)

- Fechamento 15/07/2026: **$26,89**, RSI(14) = **40,2** (não sobrevendido — mínimo recente foi ~38, nunca rompeu 30)
- Volume do dia (25,2M) **abaixo** da média de 20 pregões (51,4M) — sem sinal de "volume de pânico no fundo"
- Mínima de 50 pregões: **$26,25** (07/07/2026) — preço está ~2,4% acima, ou seja, perto mas sem toque/rompimento novo
- Mínima de 200 pregões: $20,53. Máxima de 52 semanas: $60,71 → papel caiu **56%** da máxima
- Queda de ~36% só no último mês
- Float: 557.062.446 ações, 86,1% do total em free float (fonte: filing SEC citado no shares-float da FMP, `smci-20260331.htm`)
- **Sem dado de short interest / days-to-cover / borrow fee** disponível nas ferramentas usadas até agora (FMP quote/short-interest exige plano superior; sem acesso IBKR/iBorrowDesk nesta pesquisa) — não inventar número aqui.

## Earnings confirmado

**04/08/2026** — fiscal Q4 encerrando 30/06/2026, EPS consensus $0,59 (fonte: Alpha Vantage EARNINGS_CALENDAR). Bate com a expectativa do usuário ("19 dias" contados a partir de 16/07).

## Notícias recentes (contexto de catalisador, 09-15/07/2026)

Predominantemente negativo:
- Zacks (14/07): estoque em alta, pressão de caixa, concorrência mais dura levantando dúvidas
- Aviso de ação de investidores (Moore Law PLLC, 09/07) — investigação/risco jurídico
- Empresa vendeu ações + notas conversíveis pra financiar estoque (diluição)
- Selloff setorial em 15/07 (Dell -14%, HPE e SMCI caindo junto — risco de setor, não só da empresa)

Positivo, não confirmado:
- Empresa alega ter recebido **US$ 39 bi em pedidos** em poucas semanas (Motley Fool) — se confirmado/detalhado no guidance do dia 04/08, isso SIM seria o catalisador empresarial forte do framework do usuário ("contrato bilionário/guidance elevado"). Hoje é só alegação da empresa.

## Por que não dá pra usar RSI de fontes web cegamente

Testei WebSearch pra "SMCI RSI hoje" e o mesmo dia apareceu com RSI reportado como **46, 73 e 43** em sites diferentes (TradingView/stockinvest/gurufocus) — inconsistente demais pra um gatilho numérico confiável. O dado real (via FMP technicalIndicators, série diária oficial) era 40,2. **Não confiar em um único snippet de busca pra decidir o gatilho.**

## Limitação: MCP financeiros não persistem em rotinas agendadas

As ferramentas mcp__FMP__* e mcp__Alpha_Vantage_MCP_Server__* que usei pra levantar os números acima **não ficam disponíveis automaticamente** quando uma Routine (create_trigger) dispara em sessão própria — confirmado por um aviso explícito do próprio create_trigger ("this trigger stores no MCP connectors"). Testei também `curl` direto (Bash) pra stooq.com e query1.finance.yahoo.com — bloqueado pelo proxy do sandbox (403 no CONNECT). Ou seja: **rotinas agendadas só têm WebSearch/WebFetch como fonte de dado real** (mesmo padrão já usado nas rotinas de SKHY deste repo — nenhuma delas usa MCP financeiro).

Mitigação adotada na rotina de monitoramento: cruzar RSI de **duas páginas estruturadas via WebFetch** (não snippets de busca) e só considerar o sinal de sobrevenda válido se as duas concordarem em RSI < 30. Ver `.agents/scheduled-tasks/smci-squeeze-earnings-alert/SKILL.md`.

## Gatilho que falta pro setup completo

RSI < 30 **E** volume >= 1,5x a média de 20 dias, idealmente perto da mínima de 50 dias — isso confirmaria a perna técnica de reversão. Combinado com o earnings de 04/08 como catalisador, é exatamente o "catalisador de squeeze + reversão técnica" que o usuário está rastreando (ver check_squeeze_setup em `artifacts/api-server/src/agent/tools.py`, adicionado nos PRs #84/#85).
