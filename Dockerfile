# syntax=docker/dockerfile:1
#
# Imagem única com Node 24 + Python 3.11, porque o app precisa dos dois no MESMO
# container: o Express spawna scripts Python (runner.ts::getPythonBin) e lê o
# stdout deles. Separar em dois serviços quebraria isso.
#
# Base bookworm-slim de propósito: Debian 12 já traz Python 3.11, que é a versão
# que o .replit declara. Instalar Python por PPA daria a mesma coisa com mais
# passos e mais chance de divergir da versão testada.

# ── Build ─────────────────────────────────────────────────────────────────────
FROM node:24-bookworm-slim AS build

WORKDIR /app
RUN corepack enable

# Copia o repo inteiro antes do install, em vez de só os package.json.
#
# O truque de copiar manifests primeiro melhora o cache de camada, mas aqui
# exigiria listar à mão cada pacote de lib/ e artifacts/ -- e a lista sai de
# sincronia no dia em que alguém adicionar um pacote, com falha silenciosa (o
# install "funciona" sem a dependência nova). Robustez vale mais que a camada
# em cache.
COPY . .

# ...mas o preço disso é que QUALQUER mudança de código invalida a camada do
# install, e sem store persistente o pnpm rebaixa 934 pacotes da rede a cada
# deploy. Medido em 18/08/2026, num deploy que só mexeu em dois arquivos
# Python: "resolved 934, reused 0, downloaded 934".
#
# O cache mount separa as duas coisas. Ele NÃO é camada: sobrevive à
# invalidação do COPY acima, então o install continua rodando (robusto, sem
# lista para manter em dia) mas resolve por hardlink do store em vez de
# baixar. O "reused" passa a ser quase o total.
#
# Não é cache de CORRETUDE: --frozen-lockfile continua mandando, e o store é
# endereçado por conteúdo -- pacote com hash diferente não é reaproveitado.
# Na pior das hipóteses o store está vazio e o build é o de hoje.
ENV PNPM_STORE_DIR=/pnpm-store
RUN --mount=type=cache,target=/pnpm-store,sharing=locked \
    pnpm install --frozen-lockfile

# PORT e BASE_PATH existem aqui porque o vite.config.ts dos artifacts de
# frontend lança exceção quando eles faltam -- é validação de config, avaliada
# no build, não em runtime. PORT não influencia nada no bundle gerado (só
# alimenta o dev server); BASE_PATH influencia: vira o `base` do Vite e o
# `scope`/`start_url` do manifest do PWA. "/" é o certo pra quem serve o
# frontend na raiz do domínio, que é o caso deste container.
ENV PORT=8080 \
    BASE_PATH=/ \
    NODE_ENV=production

RUN pnpm run build

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM node:24-bookworm-slim AS runtime

# python3-venv: getPythonBin() procura .venv/bin/python na raiz do workspace e
# só cai em `python3` se não achar. Criar o venv no caminho esperado mantém o
# comportamento igual ao do Replit, em vez de depender do fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# As dependências Python primeiro: pandas e numpy são o que demora, e essa
# camada só reconstrói quando o requirements.txt muda.
COPY requirements.txt ./
RUN python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir --upgrade pip \
    && .venv/bin/pip install --no-cache-dir -r requirements.txt

# O /app inteiro do estágio de build, não só o dist.
#
# A ordem importa: este COPY vem DEPOIS do venv acima. O COPY do Docker mescla
# no diretório de destino em vez de substituí-lo, e o estágio de build não tem
# .venv (está no .dockerignore), então o venv sobrevive. Inverter as duas
# instruções custaria o cache da camada pesada de pip a cada mudança de código.
#
# O agente Python NÃO é empacotado: runner.ts aponta agentDir para
# artifacts/api-server/src e spawna os scripts de lá por caminho. Uma imagem só
# com dist/ subiria e só quebraria no primeiro checker, cinco minutos depois --
# o pior momento pra descobrir.
COPY --from=build /app /app

ENV NODE_ENV=production \
    PORT=8080 \
    PYTHONUNBUFFERED=1 \
    # Fora do Replit não há roteador de borda entregando o frontend: quem serve
    # artifacts/premarket/dist/public é o próprio Express (ver
    # lib/servir-estatico.ts). Ligar isso também ativa a CSP explícita do app.ts.
    SERVE_STATIC=1 \
    # O cache de histórico (hist_cache.py) mora aqui. Em volume nomeado no
    # compose, pra sobreviver a restart -- sem isso todo deploy recomeça
    # batendo no Yahoo do zero.
    AGENT_HIST_CACHE_DIR=/var/cache/premercado/hist

RUN mkdir -p /var/cache/premercado/hist

EXPOSE 8080

# tini como PID 1: sem init, os subprocessos Python que morrem por timeout
# viram zumbis, e o container acumula processos defuntos até estourar o limite
# de PIDs. Com o app spawnando dezenas de Python por hora, isso importa.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "--enable-source-maps", "artifacts/api-server/dist/index.mjs"]
