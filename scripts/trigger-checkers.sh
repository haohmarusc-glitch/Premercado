#!/bin/bash
# Dispara o ciclo de checkers via POST /api/checkers/run. Feito pra ser chamado
# por um Replit "Scheduled Deployment" a cada 5 minutos (mesmo padrão do
# trigger-scheduled-run.sh do agente diário).
#
# Por quê: no Autoscale a CPU só é garantida DURANTE um request. Os timers
# internos rodavam com o container estrangulado (imports de Python de até 156s)
# e falhavam; dentro do request, o mesmo trabalho leva segundos. O endpoint
# decide sozinho a cadência de cada checker (5min/15min/1h/24h) -- este script
# só precisa chamar num intervalo fixo.
#
# Variáveis de ambiente necessárias (configurar como Secrets DO SCHEDULED
# DEPLOYMENT -- secrets de deployment são separadas das do workspace!):
#   OPERATOR_API_KEY - mesmo valor já usado no deploy Autoscale principal
#   AGENT_APP_URL    - URL pública do app (opcional, default abaixo)

set -euo pipefail

if [ -z "${OPERATOR_API_KEY:-}" ]; then
  echo "ERRO: variável OPERATOR_API_KEY não configurada neste deployment" >&2
  exit 1
fi

APP_URL="${AGENT_APP_URL:-https://agente-bolsa.replit.app}"

response=$(curl -sS -w '\n%{http_code}' -X POST "${APP_URL}/api/checkers/run" \
  -H "Authorization: Bearer ${OPERATOR_API_KEY}" \
  -H "Content-Type: application/json" \
  --max-time 280)

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

echo "HTTP ${http_code}: ${body}"

# 409 = ciclo anterior ainda rodando OU agente diário em execução -- não é
# falha deste gatilho; a próxima chamada roda normalmente.
if [ "$http_code" != "200" ] && [ "$http_code" != "409" ]; then
  echo "ERRO: disparo do ciclo de checkers falhou (HTTP ${http_code})" >&2
  exit 1
fi
