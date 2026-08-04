# Deploy fora do Replit

Pacote de migração: uma VPS qualquer, Docker, e o mesmo código que roda hoje.
Nada aqui muda o comportamento no Replit — enquanto `SERVE_STATIC` não estiver
definido, o app se comporta exatamente como hoje.

## O que compõe o pacote

| Arquivo | Papel |
|---|---|
| `Dockerfile` | Imagem única com Node 24 + Python 3.11 (o Express spawna Python; separar quebraria isso) |
| `docker-compose.yml` | `app` + `db` (Postgres 16) + `caddy` (TLS automático) |
| `Caddyfile` | Proxy reverso e certificado |
| `.env.example` | Todas as variáveis, marcadas entre obrigatórias e opcionais |
| `artifacts/api-server/src/lib/servir-estatico.ts` | Express passa a servir o frontend (no Replit quem faz isso é o roteador da borda) |

## Por que uma imagem só

O agente Python **não é empacotado**. `runner.ts` aponta `agentDir` para
`artifacts/api-server/src` e spawna os scripts por caminho — a árvore de código
precisa existir em runtime, não só o `dist/`. Por isso o estágio de runtime
copia `/app` inteiro em vez de só a saída do esbuild, e por isso Node e Python
moram no mesmo container: são o mesmo processo pai e filho.

O venv fica em `/app/.venv` porque é exatamente onde `getPythonBin()` procura.
Se não achar, ele cai em `python3` do sistema — que não teria pandas nem
yfinance, e o primeiro checker falharia cinco minutos depois do deploy.

## Máquina

O gargalo medido em produção não foi rede nem Yahoo: foi **CPU**. Os mesmos
imports (`numpy`, `pandas`, `yfinance`) levaram 64–74 s no container do Replit
contra 4,25 s no workspace, sem nenhuma chamada de rede envolvida e com a
concorrência já reduzida a 2. Qualquer vCPU dedicada resolve isso.

Referência: 2 vCPU / 4 GB de RAM / 40 GB de disco é folgado para esta carga
(Hetzner CX22 ou equivalente). Menos que 2 GB de RAM aperta na hora em que o
`run_checkers` e uma run do agente coincidem.

## Passo a passo

### 1. DNS

Aponte um registro A do subdomínio escolhido para o IP da máquina **antes** de
subir a stack. O Caddy só consegue emitir certificado depois que o nome
resolve.

### 2. Preparar a máquina

```
apt update && apt install -y docker.io docker-compose-plugin git
```

### 3. Clonar e configurar

```
git clone <url-do-repo> premercado
cd premercado
cp .env.example .env
```

Preencha no `.env` tudo que está marcado como obrigatório. Os dois segredos
gerados na máquina:

```
openssl rand -base64 48   # JWT_SECRET
openssl rand -base64 32   # OPERATOR_API_KEY
```

`JWT_SECRET` novo desloga todo mundo (é o que assina o cookie de sessão) — o
que é aceitável numa migração, mas não é surpresa que se queira descobrir
depois. As chaves de LLM e de provedores de dados são as mesmas que estão nos
Secrets do Replit; copie de lá.

### 4. Migrar o banco

Ainda com o Replit no ar, com a URL do Postgres de lá:

```
docker compose up -d db
pg_dump "<DATABASE_URL do Replit>" --no-owner --no-privileges -Fc -f premercado.dump
docker compose exec -T db pg_restore -U premercado -d premercado --no-owner < premercado.dump
```

Confira antes de seguir — a contagem tem que bater com a do Replit:

```
docker compose exec db psql -U premercado -d premercado -c "\dt"
docker compose exec db psql -U premercado -d premercado -c "select count(*) from reports;"
```

O `ensureSchema()` roda no boot e cria o que faltar, mas ele cria **estrutura**,
não dados. Restaurar o dump antes de subir o `app` evita descobrir isso com a
carteira vazia.

### 5. Subir

```
docker compose up -d --build
docker compose logs -f app
```

O primeiro build demora (instala pandas/numpy e roda o build do frontend). Os
seguintes reaproveitam a camada do pip enquanto o `requirements.txt` não mudar.

### 6. Conferir

```
curl -sS https://seu.dominio/api/healthz     # {"status":"ok"}
docker compose ps                            # app deve ficar "healthy"
```

E abra o domínio no navegador: se o HTML carrega mas o gráfico da TradingView
não aparece, é a CSP — confira o console do navegador e ajuste a lista de hosts
em `app.ts`.

## Atualizar depois

```
git pull
docker compose up -d --build
```

O volume `pgdata` não é tocado por `--build`. O `histcache` também sobrevive,
que é o ponto de ele ser volume nomeado: sem isso todo deploy recomeçaria
baixando o histórico inteiro do Yahoo.

## Backup

O que precisa de backup é `pgdata`. Um cron diário na própria máquina:

```
0 3 * * * cd /root/premercado && docker compose exec -T db pg_dump -U premercado premercado | gzip > /root/backups/premercado-$(date +\%F).sql.gz
```

Isso protege contra o modo de falha que já aconteceu duas vezes no Replit: uma
migração aplicada pelo painel de Publishing destruiu tabelas de produção. Fora
do Replit não há painel que faça isso sozinho — mas um `pg_restore` errado
também apaga, e o backup é o que diferencia susto de perda.

## Diferenças em relação ao Replit

- **Frontend**: servido pelo Express (`SERVE_STATIC=1`), não pelo roteador da
  borda. Isso também liga a CSP explícita do `app.ts` — no Replit ela nunca se
  aplicou ao documento HTML, porque o documento não passava por aqui.
- **Banco**: Postgres no mesmo compose, sem `ports:` publicado. Se preferir
  continuar com um gerenciado, basta apontar `DATABASE_URL` para ele e remover
  o serviço `db` e o `depends_on` do `app`.
- **PID 1**: `tini`. Sem init os subprocessos Python mortos por timeout viram
  zumbis, e com dezenas de spawns por hora o container acumula defuntos até
  estourar o limite de PIDs.
- **Sem sleep de cold start**: o processo fica de pé o tempo todo, então o
  `scheduler` e os checkers de fundo não dependem mais de a instância estar
  acordada na hora certa.
