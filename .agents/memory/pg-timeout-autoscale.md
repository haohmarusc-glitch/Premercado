---
name: Timeouts de Postgres sob throttling do Autoscale
description: Causa raiz dos erros de banco "cause: {}" vazios em produção — pool pg estoura connection timeout quando a instância Autoscale está sem CPU
---

# Erros de banco em produção = CPU throttling, não bug de banco

**O que acontece:** em produção (Autoscale), erros intermitentes em queries simples
("Alert check error", "Scenario alert check error", "Scenario params check error" com
`cause: {}` vazio; e `_DrizzleQueryError` com "Connection terminated due to connection
timeout" quando o log captura o erro real).

**Causa raiz (confirmada 05/08/2026):** o Autoscale corta a CPU fora de requests HTTP.
Abrir conexão nova no pool pg — normalmente ms — estoura o connect timeout quando a
instância está throttled (mesma causa dos boots Python de 10s e filas de 200s+).
Por isso os erros ocorrem só nos checkers de fundo/rotas internas chamadas pelo agente,
nunca durante uso interativo (requests trazem CPU).

**Why:** evita caçar "bug de Postgres" inexistente; o banco está saudável.

**How to apply:** ao investigar erro de banco em produção, primeiro verificar se ocorreu
fora de um request de usuário (timer/checker/agente). Solução definitiva é a mesma do
throttling geral: mover trabalho de fundo para requests agendados (Scheduled Deployment
batendo endpoint) + max machines = 1. Mitigação pontual: aumentar connectionTimeoutMillis
do pool e retry — usuário cancelou task de hardening do pool; não repropor sem pedido.
