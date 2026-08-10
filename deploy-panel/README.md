# Deploy Panel

Painel web (PC + celular) para o Premercado no VPS: git pull/push, logs Docker, publish.

## No VPS

```bash
cd /opt/premercado
git pull
mkdir -p /opt/deploy-panel
cp -a deploy-panel/. /opt/deploy-panel/
cd /opt/deploy-panel
cp .env.example .env
nano .env   # PANEL_PASSWORD=...
docker compose up -d --build
```

## Acesso

**PC (tunel SSH):**
```powershell
ssh -L 3090:127.0.0.1:3090 root@65.108.154.111
```
http://localhost:3090

**Celular:** subdominio `panel.premercadosc.com` no Caddy apontando para `127.0.0.1:3090`.
