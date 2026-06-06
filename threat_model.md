# Threat Model

## Project Overview

Pré-Mercado is a publicly deployed stock-monitoring application with a React frontend, an Express 5 API, PostgreSQL via Drizzle ORM, and a Python subprocess that runs an Anthropic-powered market-analysis agent. The production deployment is public, so every exposed `/api/*` route should be treated as internet-reachable unless the code enforces a stricter boundary.

## Assets

- **Agent execution capability** — triggering the Python analysis loop consumes Anthropic credits, external API quota, CPU, and mail-sending capacity. Abuse can create direct cost and service disruption.
- **Application data** — reports, observations, alert rules, alert firing history, and scheduler settings are business data that drive the dashboard and downstream emails.
- **Notification channel** — the configured recipient email and SMTP-backed outbound mail flow are sensitive because misuse can redirect or spam notifications.
- **Application secrets** — `DATABASE_URL`, `ANTHROPIC_API_KEY`, SMTP credentials, and any internal service configuration must stay server-side and out of logs/responses.
- **Prompt context / agent memory** — observations pulled into the LLM system prompt influence future tool use and generated reports. If tampered with, they can alter future agent behavior.

## Trust Boundaries

- **Browser to API** — the frontend and any third party can call the Express API. The client is untrusted and cannot enforce security policy.
- **API to PostgreSQL** — the API has write access to core application tables. Any route-level flaw can directly alter persistent state.
- **API to Python subprocess** — Express spawns Python helpers and the full agent. Triggering these boundaries can consume significant resources and cause side effects.
- **Agent to external market/news/SEC sources** — the Python agent ingests untrusted third-party content and feeds portions of it into LLM context.
- **Agent to internal API routes** — the Python subprocess talks back to `/api/observations/internal` and alert endpoints over HTTP. These routes are only truly internal if the server enforces that boundary.
- **Public to operator-only functionality** — agent runs, alert management, scheduler/settings changes, and observation-ingestion endpoints are operator functions and must not be publicly writable.

## Scan Anchors

- **Production entry points:** `artifacts/api-server/src/index.ts`, `artifacts/api-server/src/app.ts`, `artifacts/api-server/src/routes/`, `artifacts/api-server/src/lib/runner.ts`, `artifacts/api-server/src/agent/`.
- **Highest-risk areas:** unauthenticated Express routes, Python agent tool bridge, internal observation endpoints, alert/settings management, email side effects.
- **Public surfaces:** reports, chart/quotes reads, health, current dashboard-backed APIs.
- **Operator-only surfaces:** `/api/agent/run`, `/api/settings`, `/api/alerts` mutations, `/api/observations/internal` read/write, scheduler/runner state.
- **Usually dev-only / ignore unless reachable:** `artifacts/mockup-sandbox/**`.

## Threat Categories

### Spoofing

This project currently has no application-layer authentication boundary in front of sensitive API routes. The system must require a trustworthy server-side identity check before allowing anyone to trigger agent runs, mutate alerts, modify settings, or use endpoints labeled internal. Naming a route `internal` is not a security control.

### Tampering

The API writes directly to reports, observations, alerts, and settings tables. All state-changing routes must enforce operator authorization and validate attacker-controlled input before it can affect persistent state, downstream emails, or the LLM’s future memory context.

### Information Disclosure

Reports, observations, run history, alert history, and configured notification details may reveal operator behavior and internal workflow state. The system must avoid exposing operator-only data to anonymous users and must not leak secrets or sensitive headers through logs or responses.

### Denial of Service

The highest DoS risk is the public ability to trigger expensive work: spawning the LLM-driven Python agent, polling external market sources, and generating outbound email. Sensitive or resource-intensive endpoints must be authenticated and rate-limited so an internet user cannot turn the service into a cost or availability sink.

### Elevation of Privilege

Any public caller who can reach operator-only routes effectively gains operator privileges today. The system must enforce server-side authorization for administrative functions and ensure external content reaching the agent cannot indirectly coerce privileged state changes beyond the intended tool policy.
