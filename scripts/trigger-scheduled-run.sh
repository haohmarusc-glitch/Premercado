#!/bin/bash
# Dispara a análise diária via POST /api/agent/run. Feito pra ser chamado por
# um Replit "Scheduled Deployment" SEPARADO do deploy Autoscale principal --
# o agendador interno (node-cron, em lib/scheduler.ts) só dispara se o
# processo do Autoscale já estiver acordado no horário exato, e o Autoscale
# hiberna o container quando ninguém está usando o app. Nesse caso o
# agendamento interno simplesmente não dispara, sem erro nenhum (run
# perdida silenciosamente). Essa chamada HTTP acorda o container via cold
# start e garante que a run aconteça de verdade.
#
# Variáveis de ambiente necessárias (configurar como Secrets do Scheduled
# Deployment no Replit, não como argumento de linha de comando):
#   OPERATOR_API_KEY - mesmo valor já usado no deploy Autoscale principal
#   AGENT_APP_URL     - URL pública do app (opcional, default abaixo)

set -euo pipefail

if [ -z "${OPERATOR_API_KEY:-}" ]; then
  echo "ERRO: variável OPERATOR_API_KEY não configurada neste deployment" >&2
  exit 1
fi

APP_URL="${AGENT_APP_URL:-https://agente-bolsa.replit.app}"

response=$(curl -sS -w '\n%{http_code}' -X POST "${APP_URL}/api/agent/run" \
  -H "Authorization: Bearer ${OPERATOR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"mode":"scheduled"}')

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

echo "HTTP ${http_code}: ${body}"

# 409 = já tem uma run em andamento (ex.: alguém disparou manualmente antes)
# -- não é uma falha desse gatilho, só significa que não há nada a fazer.
if [ "$http_code" != "200" ] && [ "$http_code" != "409" ]; then
  echo "ERRO: disparo da análise agendada falhou (HTTP ${http_code})" >&2
  exit 1
fi
