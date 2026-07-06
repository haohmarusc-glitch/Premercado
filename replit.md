# Pré-Mercado — Agente de Análise

App de monitoramento pré-mercado com loop agêntico (Claude + fallback multi-provider)
para uma cesta de tickers do ecossistema de semicondutores/IA, com gestão de carteira,
alertas de preço e chat conversacional.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — API server (porta via `PORT` env)
- `pnpm --filter @workspace/premarket run dev` — Frontend React (porta via `PORT` env)
- `pnpm run typecheck` — typecheck completo do monorepo
- `pnpm --filter @workspace/api-spec run codegen` — regenerar hooks e schemas do OpenAPI
- `pnpm --filter @workspace/db run push` — aplicar schema no banco (dev only)
- `pnpm --filter @workspace/api-server test` — testes do servidor (vitest)
- Required env: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPERATOR_API_KEY`, `JWT_SECRET`
  (segredo do cookie de sessão — o servidor falha no boot se faltar)
- Opcional (fallback chain): `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `KIMI_API_KEY`
- Opcional (dados alternativos "smart money" — sem a chave, a seção correspondente
  some/mostra como ativar em vez de quebrar): `QUIVER_API_KEY` (negociações do
  Congresso via Quiver Quant), `UNUSUAL_WHALES_API_KEY` (dark pool/opções não-usuais
  via Unusual Whales), `INSTITUTIONAL_CIKS` (lista de gestores 13F acompanhados,
  formato `cik:Rótulo,cik:Rótulo` — tem default sem precisar configurar)

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5, login por email/senha (cookie httpOnly + JWT) atrás de
  `requireAuth` — ver Architecture decisions
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod, drizzle-zod
- API codegen: Orval (a partir do OpenAPI spec)
- Frontend: React + Vite + shadcn/ui + Tailwind
- Agent: Python 3 + Anthropic SDK + yfinance + requests + pandas
- Scheduler: `node-cron`, timezone `America/Sao_Paulo`, só dias úteis

## Where things live

- `lib/api-spec/openapi.yaml` — contrato único da API
- `lib/db/src/schema/premarket.ts` — todas as tabelas (ver lista abaixo)
- `artifacts/api-server/src/routes/` — rotas Express:
  - `agent.ts` (`/agent/run`, `/agent/status`) — dispara e monitora o loop agêntico
  - `runs.ts` (`/agent/runs`) — histórico de execuções
  - `reports.ts` (`/reports`, `/reports/latest`, `/reports/:id`)
  - `observations.ts` (`/observations`, `/observations/internal`) — memória do agente
  - `alerts.ts` — CRUD de alertas de preço + histórico de disparos
  - `portfolio.ts` — posições, compras/vendas, alertas de carteira
  - `chat.ts` — sessões + streaming SSE de chat conversacional
  - `settings.ts`, `quotes.ts`, `chart.ts`, `health.ts`
  - `auth.ts` (`/auth/signup`, `/auth/login`, `/auth/logout`, `/auth/me`,
    `/auth/claim-seed-account`) — únicas rotas abertas, sem exigir sessão
  - `index.ts` monta `requireAuth` centralmente pra tudo que vem depois de
    `health`/`internal`/`auth` (ver Architecture decisions)
- `artifacts/api-server/src/middleware/require-auth.ts` — sessão (cookie) OU
  bearer `OPERATOR_API_KEY`
- `artifacts/api-server/src/lib/auth.ts` — hash de senha (bcryptjs), JWT
  (jsonwebtoken), cookie de sessão
- `artifacts/api-server/src/lib/claim-seed-account.ts` — cria a conta seed
  (dono original) e faz o backfill de `user_id` nas linhas antigas no boot
- `artifacts/api-server/src/lib/`:
  - `runner.ts` — spawna o subprocess Python do agente completo
  - `scheduler.ts` — cron diário + scan intradiário de pré-mercado
  - `alert-checker.ts`, `portfolio-alerts.ts` — avaliam gatilhos de preço/tempo
  - `mailer.ts` — envio de relatório por e-mail
- `artifacts/api-server/src/agent/` — código Python do loop agêntico
  - `agent.py` — prompts (estável/cacheável + volátil) e os 3 modos de run:
    `run()` completo, `run_premarket()` flash intradiário, `run_chat_stream()`
  - `provider.py` — adapter Anthropic/OpenAI-compatible + `FallbackClient`
    (cadeia configurável via `AGENT_PROVIDER_ORDER`, default:
    `anthropic → gemini → openrouter → openai → kimi`); recupera tool-calls
    "vazadas" como texto por modelos menores
  - `tools.py` — todas as ferramentas do agente (cotação, notícias, técnicos,
    opções, short interest, analyst ratings, EDGAR, alertas, contágio setorial)
  - `cache.py` — cache em disco (JSON, `/tmp`) com TTL por chamada, falha aberta
  - `security.py` — `sanitize_ticker`, `sanitize_url` (bloqueia SSRF: localhost,
    RFC1918, link-local/metadata de cloud), `sanitize_for_llm` (mitiga prompt
    injection em conteúdo externo), `mask_sensitive_data` (mascara chaves em logs)
  - `market_alerts.py` — contágio, macro (FOMC/CPI), técnico, Form 4, circuit breaker
  - `sector_contagion.py` — detecção de líder/catch-up entre grupos da cadeia de IA
  - `memory.py` — lê observações anteriores via API interna para injetar no prompt
  - `run_agent.py` / `run_chat.py` — entry points chamados como subprocess pelo Node
  - `config.py` — `TICKERS`, modelos por tier, limites de turnos/tokens, cache TTL
- `artifacts/premarket/src/` — frontend React
- `carteira.py` — script standalone de carteira (usa `psycopg2` direto, fora do agente)

## Architecture decisions

- Agent roda como subprocess Python lançado pelo Express; progress via stdout
  linhas `STEP:`; relatório final começa com `REPORT:` e é salvo no banco pelo
  Node após o processo terminar
- Chat usa o mesmo padrão de subprocess, mas com streaming SSE (`STEP:`,
  `RESULT:`, `TITLE:` no stdout) em vez de esperar o processo terminar
- Observações salvas via chamada HTTP interna do Python para
  `POST /api/observations/internal`; memória dos últimos 7 dias injetada no
  system prompt via `GET /api/observations/internal`
- System prompt do modo completo é dividido em bloco estável (cacheável via
  `cache_control` da Anthropic) e bloco volátil (data + memória, sem cache) —
  ver `_system_blocks()` em `agent.py`
- `FallbackClient` tenta os providers na ordem configurada; ao trocar de
  provider no meio de uma run, trunca o histórico de tool_use/tool_result
  acumulado (o novo provider não tem como continuar de onde o anterior parou)
- Cada ferramenta de rede em `tools.py` é cacheada via `cache.py` com TTL
  proporcional à volatilidade do dado (preço: 120s; filing SEC: 24h)
- Frontend faz polling do status do agente a cada ~3s enquanto `running: true`
- Login por email/senha: `requireAuth` (montado centralmente em
  `routes/index.ts`, logo após `auth.ts`) aceita cookie de sessão (JWT
  httpOnly, 30 dias) OU bearer `Authorization: Bearer $OPERATOR_API_KEY` — esse
  segundo caminho existe pro agente Python (`tools.py`) e o script
  `carteira.py`, que chamam `/api/alerts` e `/api/portfolio` direto e não têm
  sessão de usuário própria; nesse caso `req.userId` resolve pra conta "dona"
  (seed), a mesma que recebeu o backfill dos dados existentes
- Só `portfolio_positions`/`portfolio_purchases` e `alerts`/`alert_firings`
  são separados por usuário (`user_id`); todo o resto (relatórios,
  observações, watchlist, journal, settings, chat, agent_runs) continua um
  dataset único compartilhado por qualquer conta logada
- Jobs de background (`alert-checker.ts`, `portfolio-alerts.ts`) continuam
  rodando sobre TODOS os usuários e mandando e-mail pro único `notifyEmail`
  global de `settings` — não foram escopados por usuário de propósito
- Conta seed (dono original, email fixo em `claim-seed-account.ts`) nasce com
  senha aleatória inutilizável (`isClaimed: false`) e só fica utilizável
  depois de `POST /auth/claim-seed-account`, que define a senha real

## Database (tabelas em `lib/db/src/schema/premarket.ts`)

`users`, `reports`, `observations`, `agent_runs`, `settings`, `alerts`,
`alert_firings`, `chat_sessions`, `chat_messages`, `portfolio_positions`,
`portfolio_purchases`, `portfolio_alert_firings`

`portfolio_positions.user_id` e `alerts.user_id` (nullable, FK pra `users`)
são as únicas colunas de dono — ver Architecture decisions.

## Product

- Dashboard com relatório do dia em Markdown, indicador de status do agente,
  botão "RUN AGENT"
- Histórico de todos os relatórios passados com badges de sentimento
- Feed de observações (memória do agente) filtrável por ticker
- Gestão de carteira (posições, compras/vendas parciais, alertas de
  ganho/perda e marcos de tempo de holding)
- Chat conversacional com subconjunto de ferramentas read-only
- Scan intradiário de pré-mercado opcional (janela configurável, modelo "flash")

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- `openapi.yaml` é a fonte de verdade dos pacotes gerados (`api-zod`,
  `api-client-react`) — NUNCA edite os arquivos em `generated/` à mão; altere
  o spec e rode o codegen. O spec foi ressincronizado em 2026-07-02 depois de
  um período em que os gerados foram editados manualmente e divergiram.

- O agente Python usa `python3 -m agent.run_agent` (ou `agent.run_chat`) com
  `cwd = artifacts/api-server/src`; `PYTHONPATH` precisa apontar pro mesmo dir
- `INTERNAL_API_URL` precisa apontar para o servidor Express correto em runtime
- `ANTHROPIC_API_KEY` vazia não impede o agente de rodar — `FallbackClient` só
  monta a cadeia com os providers que têm chave configurada; se nenhuma chave
  estiver presente, levanta `RuntimeError` explícito
- A lista de tickers default existe duplicada em `runner.ts` (TS) e
  `config.py` (Python) — `runner.ts` é a fonte de verdade em runtime, porque
  o valor lido de `settingsTable` é repassado ao Python via `AGENT_TICKERS`;
  ao mudar a lista default, atualizar os dois lugares
- `search_edgar_filings` só cobre os tickers presentes em `TICKER_TO_CIK`
  (em `tools.py`) — tickers fora desse dict retornam erro tratado, não crash
- Erros de provider em `provider.py` passam por `mask_sensitive_data` antes
  de log/persistência — não remover essa máscara ao tocar nesse código,
  porque `runner.ts` grava `errorMessage` direto em `agent_runs` no Postgres
