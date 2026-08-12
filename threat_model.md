# Threat Model

> **Revisado em 2026-08-12.** Esta revisão comparou cada afirmação do documento
> anterior com o código atual (`artifacts/api-server/src/`). A versão anterior
> descrevia um estado "sem nenhuma barreira de autenticação" que não reflete
> mais a implementação: `requireAuth` já é aplicado globalmente e
> `routes/internal.ts` já restringe por IP. Ver "Threat Categories" abaixo para
> o que está de fato mitigado e o que ainda é risco real. Trate este documento
> como código: quando uma rota mudar, atualize a seção correspondente na mesma
> PR — um threat model desatualizado é pior do que nenhum, porque passa falsa
> sensação de segurança.

## Project Overview

Pré-Mercado is a publicly deployed stock-monitoring application with a React frontend, an Express 5 API, PostgreSQL via Drizzle ORM, and a Python subprocess that runs an Anthropic-powered market-analysis agent. The production deployment is public, so every exposed `/api/*` route should be treated as internet-reachable unless the code enforces a stricter boundary.

## Assets

- **Agent execution capability** — triggering the Python analysis loop consumes Anthropic credits, external API quota, CPU, and mail-sending capacity. Abuse can create direct cost and service disruption.
- **Application data** — reports, observations, alert rules, alert firing history, and scheduler settings are business data that drive the dashboard and downstream emails.
- **Notification channel** — the configured recipient email and SMTP-backed outbound mail flow are sensitive because misuse can redirect or spam notifications.
- **Application secrets** — `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPERATOR_API_KEY`, SMTP credentials, and any internal service configuration must stay server-side and out of logs/responses.
- **Prompt context / agent memory** — observations pulled into the LLM system prompt influence future tool use and generated reports. If tampered with, they can alter future agent behavior.
- **Shared/global configuration** — `settingsTable` is a single row for the whole deployment (tickers monitored, schedule, `notifyEmail`, daily LLM budget). It is not per-user, so any principal who can write to it affects every user and the operator's own notifications.

## Trust Boundaries

- **Browser to API** — the frontend and any third party can call the Express API. The client is untrusted and cannot enforce security policy.
- **API to PostgreSQL** — the API has write access to core application tables. Any route-level flaw can directly alter persistent state.
- **API to Python subprocess** — Express spawns Python helpers and the full agent. Triggering these boundaries can consume significant resources and cause side effects.
- **Agent to external market/news/SEC sources** — the Python agent ingests untrusted third-party content and feeds portions of it into LLM context.
- **Agent to internal API routes** — the Python subprocess talks back to `/api/observations/internal` over HTTP. **Mitigated**: `routes/internal.ts` enforces `localhostOnly` (checks `req.socket.remoteAddress` against `127.0.0.1`/`::1`) before any handler runs, so this boundary is now a real server-side control, not just a route name.
- **Public to authenticated functionality** — most mutating/expensive routes require a valid session cookie or the `OPERATOR_API_KEY` bearer token (`middleware/require-auth.ts`, wired in `routes/index.ts`). See Elevation of Privilege below for the boundary this *doesn't* cover: authenticated-but-unprivileged users.

## Scan Anchors

- **Production entry points:** `artifacts/api-server/src/index.ts`, `artifacts/api-server/src/app.ts`, `artifacts/api-server/src/routes/index.ts`, `artifacts/api-server/src/middleware/require-auth.ts`, `artifacts/api-server/src/lib/runner.ts`, `artifacts/api-server/src/agent/`.
- **Auth enforcement:** `middleware/require-auth.ts` (`requireAuth`, `requireAdmin`), `middleware/llm-rate-limit.ts` (`llmLimiter`), `middleware/auth-rate-limit.ts` (`authLoginLimiter`, `authSignupLimiter`), `routes/internal.ts` (`localhostOnly`), `routes/checkers.ts` (`requireOperatorKey`).
- **Public surfaces (no session required):** `GET /health`, `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/claim-seed-account` — see Spoofing below, signup is intentionally open but that has knock-on effects.
- **Session-authenticated but NOT admin-scoped:** `POST /agent/run`, `GET/PATCH /settings`, `GET /agent/spend`, and every router mounted after `router.use(requireAuth)` in `routes/index.ts` except `admin-users.ts`. Any user with a valid session — including one created via open signup — can reach these.
- **Admin-only:** `routes/admin-users.ts` (`requireAdmin`, checked per-route, not per-router — see comment in that file about why `router.use(requireAdmin)` would leak).
- **Operator-key-only:** `POST /checkers/run` (`requireOperatorKey` in `routes/checkers.ts`, separate from and stricter than `requireAuth`).
- **Localhost-only:** `routes/internal.ts` (`/observations/internal` GET/POST).
- **Usually dev-only / ignore unless reachable:** `artifacts/mockup-sandbox/**`.

## Threat Categories

### Spoofing

**Mitigated:** every route except health, auth, and internal now requires either a valid session cookie (httpOnly, `secure` in production — `lib/auth.ts:56-58`) or the `OPERATOR_API_KEY` bearer token (`middleware/require-auth.ts`). Passwords are hashed with bcrypt (`bcryptjs`), and `/auth/login` returns a generic "Invalid email or password" for both wrong-password and unknown-email cases, so it doesn't leak account existence. **(2026-08-12)** `/auth/login` and `/auth/signup` now carry a dedicated rate limit (`authLoginLimiter`/`authSignupLimiter`, `middleware/auth-rate-limit.ts`, 10 req/15min per IP each, independent counters) on top of the generous global limiter — credential stuffing and account-creation spam are now bounded per IP instead of only hitting the 1000 req/15min backstop meant for polling traffic. Verified in isolation (same `rateLimit()` config mounted on a throwaway route: 10 requests pass, the 11th returns 429 with the expected message); not yet verified against the live app + Postgres.

**Residual risk:** `/auth/signup` is still open to anyone and creates a real, logged-in account with no email verification — rate limiting slows automated abuse but doesn't close the question of whether public self-service accounts are desired at all for what's described as a single-operator tool. That's a product decision, not something to default into; flag it back to the operator rather than deciding unilaterally. Also note the limiter key is per-IP by default (`express-rate-limit`'s default `keyGenerator`), so a distributed attempt from many IPs (or one attacker who guesses a specific victim's email) isn't slowed by this alone — if a specific account needs stronger protection, consider adding a per-email lockout/backoff in addition to the per-IP limiter.

### Tampering

**Mitigated:** the internal observation-write path is IP-restricted and input-validated with Zod (`routes/internal.ts`). Portfolio positions are scoped to `req.userId` on every read/write (`routes/portfolio.ts`, see `getOwnedPosition`), so one user cannot edit another user's positions.

**Residual risk:** `PATCH /settings` (`routes/settings.ts`) is guarded only by `requireAuth`, not `requireAdmin`. Because `settingsTable` is a single global row, **any authenticated user — including someone who just signed up through the open `/auth/signup` — can change the ticker list, the schedule, the daily LLM budget, and `notifyEmail` for the entire deployment.** This directly touches two assets called out above (shared configuration and the notification channel) and should be admin-gated the same way `routes/admin-users.ts` already is, unless multi-tenant settings-per-user is the intended design (it doesn't look like it, given `getOrCreateSettings()` always selects a single row with `.limit(1)` and no `userId` filter).

### Information Disclosure

**Mitigated:** the Pino logger redacts `req.headers.authorization`, `req.headers.cookie`, and `res.headers['set-cookie']` (`lib/logger.ts`). The global error handler in `app.ts` never returns raw error messages/stack traces to the client — only a generic `"Internal server error"`, with the real trace going to Pino. `reports.ts` scopes user-private reports correctly via `visibleToUser()` (house reports with `userId: null` plus the caller's own), so one user cannot read another user's private reports.

**Residual risk:** because reports with `userId: null` ("house" reports — daily/premarket/veredito) are visible to *any* authenticated user by design, confirm this is the intended product shape (shared market analysis behind a login) and not a leftover from before per-user reports existed. If it's intended, no action needed — just worth a one-line comment in `reports.ts` saying so explicitly, since a future reader may "fix" it as a bug.

### Denial of Service

**Mitigated:** `POST /agent/run` and `POST /chat/message` — the two LLM-cost-bearing routes — carry a dedicated `llmLimiter` (30 req/15min, `middleware/llm-rate-limit.ts`), separate from and much stricter than the generous global limiter (1000 req/15min) that exists for legitimate polling traffic. `app.set("trust proxy", 1)` is correctly set to exactly `1` (not `true`), which matters: with `true` a client could forge `X-Forwarded-For` to get a fresh rate-limit bucket on every request and silently defeat `llmLimiter` — this is called out explicitly in `app.ts`'s own comments, a good sign the team already thought about it. `routes/checkers.ts` uses a Postgres-backed lease (`checker_lease`) so overlapping scheduled cycles can't stack up and multiply cost.

**Residual risk:** `POST /agent/run` accepts a `mode` field but is not `llmLimiter`'d *per mode* — 30 runs/15min still lets an authenticated user (again, including a freshly self-registered one) burn through `dailyBudgetUsd` by triggering the more expensive modes repeatedly. This is a smaller version of the same root cause as the Tampering finding above: authenticated-but-unprivileged is currently equivalent to full write access on shared, cost-bearing resources.

### Elevation of Privilege

**Mitigated (2026-08-12):** `routes/admin-users.ts` correctly applies `requireAdmin` per-route (not per-router) with a comment explaining why blanket `router.use(requireAdmin)` would be wrong (it would shadow other routers mounted after it in `routes/index.ts`). `routes/checkers.ts` requires the operator key specifically, not just any session, because it triggers work "on behalf of all users." `PATCH /settings` now requires `requireAdmin` (`routes/settings.ts`) since `settingsTable` is a single global row — no per-user justification for a plain session to write it. `POST /agent/run` (`routes/agent.ts`) now requires admin for every mode **except** `"portfolio"` and `"veredito"`, which run over the calling user's own data (`req.userId` is threaded into `runAgent()` for exactly those two modes) — the admin check there is conditional on `mode`, using the new `isAdminUser()` helper in `middleware/require-auth.ts` (extracted from `requireAdmin` so the same check is reusable outside pure Express-middleware form). `GET /settings` stays open to any session (read-only).

**Residual risk:** this closes the concrete gap found in the previous review (open signup + unprivileged session = de facto admin on shared config/spend), but two things are worth tracking. First, `MODOS_PROPRIOS_DO_USUARIO` (`routes/agent.ts`) is a hardcoded set of two mode strings — if a future "runs on my own data" mode is added to `runner.ts`'s mode union without also adding it here, it will silently require admin (fails closed, which is the safe direction, but will look like a bug report from a legitimate non-admin user). Second, this change was verified with a full monorepo typecheck (`pnpm run typecheck:libs` + `tsc --noEmit` on `api-server`, zero errors) but **not** with a live end-to-end request against a running Postgres instance, since spinning up the full stack (DB + Python agent + API keys) was out of scope for this pass — worth a quick manual smoke test (`curl` as a non-admin user hitting `portfolio` vs `premarket` modes) before relying on this in production.

## Suggested Next Review Triggers

Re-review this document (not just skim it) when: a new router is mounted in `routes/index.ts`, `requireAuth`/`requireAdmin` logic changes, `/auth/signup` gets closed or gated, or `settingsTable` moves from a single global row to per-user rows.
