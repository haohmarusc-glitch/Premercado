FROM node:20-bookworm-slim

# docker CLI + compose plugin para o painel falar com o daemon do host
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
         > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /panel
COPY package.json ./
RUN npm install --omit=dev
COPY server.js ./
COPY public ./public

ENV NODE_ENV=production
ENV PORT=3090
EXPOSE 3090

CMD ["node", "server.js"]
