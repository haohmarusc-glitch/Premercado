# Pré-Mercado — Agente de Análise

App de monitoramento pré-mercado com loop agêntico Claude para MU (Micron) e SMCI (Super Micro Computer).

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — API server (porta 5000)
- `pnpm --filter @workspace/premarket run dev` — Frontend React (porta 19156)
- `pnpm run typecheck` — typecheck completo
- `pnpm --filter @workspace/api-spec run codegen` — regenerar hooks e schemas do OpenAPI
- `pnpm --filter @workspace/db run push` — aplicar schema no banco (dev only)
- Required env: `DATABASE_URL`, `ANTHROPIC_API_KEY`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod, drizzle-zod
- API codegen: Orval (from OpenAPI spec)
- Frontend: React + Vite + shadcn/ui + Tailwind
- Agent: Python 3 + Anthropic SDK + yfinance + requests (SEC EDGAR)

## Where things live

- `lib/api-spec/openapi.yaml` — contrato único da API
- `lib/db/src/schema/premarket.ts` — tabelas `reports` e `observations`
- `artifacts/api-server/src/routes/` — rotas Express (reports, observations, agent)
- `artifacts/api-server/src/agent/` — código Python do loop agêntico
  - `agent.py` — loop principal (chama Claude com ferramentas)
  - `tools.py` — ferramentas: get_stock_data, get_news, search_edgar_filings, read_filing, save_observation
  - `memory.py` — lê observações anteriores para injetar no system prompt
  - `run_agent.py` — entry point chamado como subprocess pelo Node
  - `config.py` — TICKERS, MODEL, MAX_TOKENS, MAX_AGENT_TURNS
- `artifacts/premarket/src/` — frontend React

## Architecture decisions

- Agent roda como subprocess Python lançado pelo Express; progress via stdout "STEP:" lines
- Relatório final começa com "REPORT:" no stdout e é salvo no banco pelo Node após o processo terminar
- Observações salvas via chamada HTTP interna do Python para `POST /api/observations/internal`
- Memória dos dias anteriores injetada no system prompt via `GET /api/observations/internal`
- Frontend polling do status do agente a cada 3s enquanto `running: true`

## Product

- Dashboard com relatório do dia em Markdown, indicador de status do agente, botão "RUN AGENT"
- Histórico de todos os relatórios passados com badges de sentimento
- Feed de observações (memória do agente) filtrável por ticker

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- O agente Python usa `python3 -m agent.run_agent` com `cwd = artifacts/api-server/src`
- `INTERNAL_API_URL` precisa apontar para o servidor Express correto em runtime
- Para o agente rodar, `ANTHROPIC_API_KEY` deve estar disponível como variável de ambiente
