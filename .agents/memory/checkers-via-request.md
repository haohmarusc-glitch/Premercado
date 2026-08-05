---
name: Checkers via request agendado
description: Arquitetura pós-05/08 dos checkers de fundo no Autoscale — endpoint HTTP + Scheduled Deployment, timers desligados
---

# Checkers via request agendado (Autoscale)

Regra: no Autoscale, trabalho pesado de fundo NUNCA por setInterval — sempre dentro de um request HTTP disparado por Scheduled Deployment.

**Why:** CPU só é garantida durante um request. Por timer: boot Python 8–11s, imports até 156s, timeouts de 120s estourando; dentro de request o mesmo boot leva ~0,05s (medido 04–05/08/2026). Instâncias fantasma de versões antigas mantinham timers próprios e falhavam tudo.

**How to apply:**
- `POST /api/checkers/run` (auth estrita Bearer OPERATOR_API_KEY, não sessão) roda o ciclo; cadência por etapa (5min/15min/1h/24h) e trava anti-sobreposição vivem na linha única `checker_lease` do Postgres (UPDATE atômico com expiração de 8min) — estado in-process não coordena instâncias.
- Timers internos: desligados por padrão em TODO ambiente; `RUN_BACKGROUND_CHECKERS=1` força (worker dedicado/teste).
- Gatilho: Scheduled Deployment a cada 5min rodando `scripts/trigger-checkers.sh` (secret OPERATOR_API_KEY no PRÓPRIO deployment). 409 do endpoint não é erro.
- Complemento de config do usuário: Max machines = 1 no deployment Autoscale.
