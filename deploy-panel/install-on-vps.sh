#!/usr/bin/env bash
set -euo pipefail
DEST=/opt/deploy-panel
echo "==> Instalando Deploy Panel em $DEST"
mkdir -p "$DEST"
cp -a package.json server.js Dockerfile docker-compose.yml .env.example public "$DEST/" 2>/dev/null || true
cd "$DEST"
if [[ ! -f .env ]]; then
  cp .env.example .env
  PASS=$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)
  SECRET=$(openssl rand -hex 32)
  sed -i "s/troque-por-senha-forte/$PASS/" .env
  sed -i "s/gere-com-openssl-rand-hex-32/$SECRET/" .env
  echo ""
  echo "Senha do painel gerada: $PASS"
  echo "(salva em $DEST/.env — anote!)"
  echo ""
fi
docker compose up -d --build
echo ""
echo "OK. Painel em 127.0.0.1:3090"
echo "Tunel: ssh -L 3090:127.0.0.1:3090 root@SEU_IP"
