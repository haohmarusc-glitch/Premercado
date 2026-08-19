# Infra / Monitor

Quatro atalhos vivem fora do app principal, direto na VPS: **Monitor VPS**,
**Status do site**, **Logs Docker** e **Editor (Simple Replit)**. Nenhum dos
quatro está versionado neste repo (nem em nenhum outro) — existem só como
processos/config na máquina. Este documento existe porque isso já causou um
apagão silencioso: em 11/08/2026 os três primeiros pararam de funcionar (502)
depois de um reboot da VPS, e levou uma sessão inteira de investigação pra
achar a causa, porque nada disso estava escrito em lugar nenhum.

## Mapa

| Botão | Rota pública | Backend | Onde mora |
|---|---|---|---|
| Monitor VPS | `/netdata*` | Netdata, serviço systemd na porta 19999 | `netdata.service` (systemd nativo, não é container) |
| Status do site | `/status*` | Uptime Kuma, container `uptime-kuma:3001` | `/opt/monitoring` (compose próprio) |
| Logs Docker | `/logs*` | Dozzle, container `dozzle:8080` | `/opt/monitoring` (compose próprio) |
| Editor (Simple Replit) | `IP-DA-VPS:3080` direto (**sem** passar pelo domínio/Caddy) | Node/Express | `/opt/simple-replit` — repo `Simple-replit` |
| *(portão de login dos 3 primeiros)* | `/auth/*` | `monitor-gate`, Node na porta 3095 | `/opt/monitor-gate` — **código só existe na VPS, não versionado em repo nenhum** |

## Como o roteamento funciona

O `Caddyfile` **deste repo** só tem um bloco (`reverse_proxy app:8080`) — de
propósito, é o que serve local/dev e o pacote de deploy genérico (ver
`docs/deploy-fora-do-replit.md`). O Caddyfile **rodando na VPS**
(`/opt/premercado/Caddyfile`) tem blocos extras que nunca foram trazidos pra
cá:

```caddyfile
www.{$DOMINIO} {
    redir https://{$DOMINIO}{uri} permanent
}
{$DOMINIO} {
    encode zstd gzip

    handle /auth/* {
        reverse_proxy 172.17.0.1:3095
    }

    @needsAuth {
        path /netdata* /status* /logs*
        not header Cookie *monitor_ok=1*
    }
    handle @needsAuth {
        redir * /auth/login?next={uri}
    }

    handle /netdata* {
        uri strip_prefix /netdata
        reverse_proxy 172.17.0.1:19999
    }
    handle /status* {
        uri strip_prefix /status
        reverse_proxy uptime-kuma:3001
    }
    # Dozzle - repassa o caminho completo (obrigatório com DOZZLE_BASE)
    handle /logs* {
        reverse_proxy dozzle:8080
    }

    reverse_proxy app:8080 {
        flush_interval -1
    }

    log {
        output stdout
        format console
    }
}
```

Regra central: `/netdata`, `/status` e `/logs` exigem o cookie
`monitor_ok=1`. Sem ele, o Caddy redireciona pra `/auth/login?next=...`, que
é servido pelo `monitor-gate` (porta 3095) — uma tela de senha simples que,
ao acertar, seta esse cookie. **Se o `monitor-gate` cair, os três links
caem juntos com ele**, mesmo que Netdata/Uptime Kuma/Dozzle estejam
perfeitamente saudáveis — foi exatamente o que aconteceu no incidente que
motivou este documento: o `ss -tlnp | grep 3095` não achava nada, e o
`pm2 list` vinha vazio.

`172.17.0.1` nos dois primeiros `handle` é o gateway do Docker pro host — ou
seja, tanto o `monitor-gate` quanto o Netdata rodam **direto na VPS**, fora
de container. `uptime-kuma` e `dozzle` são os únicos dois dessa lista que são
containers de verdade, e por isso são referenciados pelo nome do serviço, não
por IP — o que só funciona porque a stack de `/opt/monitoring` compartilha
rede Docker com o `caddy` deste app (sem isso, `reverse_proxy uptime-kuma:3001`
não resolveria nada).

## Editor (Simple Replit) é diferente dos outros três

Não passa pelo Caddy nem pelo domínio: é acessado direto em
`http://IP-DA-VPS:3080`. Por isso ele **não depende do `monitor-gate`** — tem
autenticação própria via `AUTH_TOKEN` (`.env` em `/opt/simple-replit`, ver
`Simple-replit/.env.example` e `Simple-replit/README.md`). Sem esse token
setado, a API do editor roda **sem nenhuma autenticação** — leitura/escrita de
arquivo, execução de código e `git push` liberados pra qualquer um que ache a
porta. Confira que existe sem revelar o valor:

```bash
grep -c "AUTH_TOKEN=.\+" /opt/simple-replit/.env   # deve vir "1"
```

## Processos e como sobrevivem a reboot

`monitor-gate` e `simple-replit` são geridos pelo PM2 (`pm2 list` mostra os
dois). Netdata é systemd nativo (`systemctl status netdata`). Uptime Kuma e
Dozzle são containers do compose em `/opt/monitoring`.

O PM2 só sobrevive a um reboot da VPS se **as duas coisas** abaixo já tiverem
sido feitas — foi a falta da segunda que causou o incidente:

```bash
pm2 startup     # registra o pm2-root.service no systemd (uma vez só)
pm2 save        # grava o processo atual em /root/.pm2/dump.pm2 (repetir sempre que a lista de processos mudar)
```

`pm2 startup` sem `pm2 save` depois é o modo de falha real: o systemd sobe o
PM2 no boot, mas o PM2 "ressuscita" a partir do último `dump.pm2` salvo — se
esse dump nunca existiu ou está desatualizado, os processos não voltam
sozinhos, mesmo com o serviço systemd corretamente habilitado.

## Diagnóstico rápido

```bash
# os dois processos Node devem aparecer "online"
pm2 list

# Netdata (systemd, não Docker)
systemctl status netdata --no-pager

# Uptime Kuma + Dozzle (containers)
cd /opt/monitoring && docker compose ps

# monitor-gate respondendo local (sem passar pelo Caddy)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3095/auth/login   # espera 200

# simple-replit respondendo local
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3080/            # espera 200

# a rota pública via Caddy, com o SNI certo (127.0.0.1 puro falha o TLS)
curl -s -o /dev/null -w "%{http_code}\n" --resolve premercadosc.com:443:127.0.0.1 https://premercadosc.com/auth/login
```

Se `monitor-gate` ou `simple-replit` não aparecerem em `pm2 list`:

```bash
cd /opt/monitor-gate && pm2 start server.js --name monitor-gate && pm2 save
# ou
cd /opt/simple-replit && set -a; source .env; set +a; pm2 start server.js --name simple-replit && pm2 save
```

## Pendência conhecida

O código do `monitor-gate` (`/opt/monitor-gate/server.js`) só existe na VPS —
não foi inspecionado nem versionado nesta sessão, só religado. Se um dia a
VPS for recriada do zero, esse serviço específico precisa ser reconstruído
do zero (é pequeno: só uma tela de senha que seta o cookie `monitor_ok=1`),
já que não há cópia dele em nenhum repositório. Trazê-lo pra um repo (mesmo
que privado) evitaria repetir esse ponto cego.


## Registro de memória do host

`scripts/registrar-memoria.sh` — cron horário que grava os maiores consumidores
de RAM em `/var/log/memoria-premercado.log`.

**Por que existe.** Em 18/08/2026 a VPS chegou a 3,3 Gi usados de 3,7 Gi, com
1 Gi em swap, depois de 10 dias ligada. O `docker stats` mostrava os cinco
containers somando **192 MiB** — ou seja, mais de 3 GB estavam sendo consumidos
por processos do HOST, fora do Docker.

Isso não era curiosidade: a pressão de memória fazia uma chamada de 26 s à API
da Anthropic estourar o teto de 55 s, e a Análise com IA falhava sem causa
aparente. Medimos rede (134 ms de handshake), a própria API (26-29 s, três
vezes) e o teto — tudo saudável. O gargalo era a máquina.

**E o reboot apagou a evidência.** O consumo voltou a 1,0 Gi e o swap zerou,
mas ninguém sabe quem acumulou. Sem registro contínuo, o próximo episódio custa
a mesma investigação e termina do mesmo jeito.

É a mesma lição do resto deste documento, aplicada a tempo em vez de depois:
o que só existe na máquina, e não está escrito, some.

**Instalar:**

```sh
install -m 755 scripts/registrar-memoria.sh /usr/local/bin/
( crontab -l 2>/dev/null; echo "0 * * * * /usr/local/bin/registrar-memoria.sh" ) | crontab -
```

**Ler, depois de alguns dias:**

```sh
grep '^===' /var/log/memoria-premercado.log | tail -30    # a curva do total
grep -A8 '^=== 2026-08-2' /var/log/memoria-premercado.log # quem crescia
```

O que procurar é RSS subindo de forma monotônica ao longo dos dias. O `uptime`
vai em cada linha de propósito: sem ele, uma queda no total é ambígua — não dá
para saber se o vazamento foi resolvido ou se a máquina só reiniciou.

Suspeito principal pela medição de 18/08: **netdata** (270 MB seis minutos após
o boot, e ele mantém o banco de métricas em memória). Ele é o maior consumidor
identificável do host e serve a um único botão — se confirmar, `systemctl
disable --now netdata` devolve a memória, e o Uptime Kuma e o Dozzle continuam
cobrindo o essencial.
