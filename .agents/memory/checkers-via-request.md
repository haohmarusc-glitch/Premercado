---
name: Checkers via request agendado
description: Arquitetura pós-05/08 dos checkers de fundo no Autoscale — endpoint HTTP + Scheduled Deployment, timers desligados, trava em Postgres
---

# Checkers via request agendado (Autoscale)

Regra: no Autoscale, trabalho pesado de fundo não roda por `setInterval` — roda
dentro de um request HTTP disparado por um Scheduled Deployment.

**Why (o que está confirmado):** o Autoscale mantém instâncias antigas vivas
durante trocas de versão, cada uma com o próprio timer. Medido em 04/08: dois
pids logando "Ciclo de checkers pulado" com filas INDEPENDENTES, com 3s de
diferença — a instância sem tráfego falhava o conjunto inteiro em todo ciclo.
Sem timer, a instância fantasma fica inofensiva. Essa é a razão sólida da
arquitetura, e ela vale independentemente de qualquer teoria sobre CPU.

**Why (o que NÃO está confirmado):** a frase "CPU só existe durante um request"
é boa aproximação, não regra. Contraexemplos no mesmo dia: um `run_checkers`
disparado por TIMER bootou em 0,05s (05/08 00:18:09), e um timeout de conexão
do Postgres estourou DENTRO de um request (00:40:14). O que os dados sustentam
é disputa por CPU, que piora com instância duplicada — não uma chave
liga/desliga entre timer e request. Não escreva a versão forte como fato.

**How to apply:**
- `POST /api/checkers/run` (auth estrita Bearer `OPERATOR_API_KEY`, não sessão)
  roda o ciclo. Cadência por etapa (5min/15min/1h/24h) e trava
  anti-sobreposição vivem na linha única `checker_lease` do Postgres — estado
  in-process não coordena instâncias.
- A trava tem **token de posse** (`owner_token`), não só prazo. O release é
  condicionado ao token: sem isso, um ciclo que passa da validade solta a trava
  de OUTRA instância que já assumiu, e ainda sobrescreve a cadência dela com
  uma cópia velha.
- Timers internos: desligados por padrão em TODO ambiente;
  `RUN_BACKGROUND_CHECKERS=1` força (worker dedicado/teste).
- Gatilho: Scheduled Deployment a cada 5min rodando
  `scripts/trigger-checkers.sh` (secret `OPERATOR_API_KEY` no PRÓPRIO
  deployment — secrets de deployment são separadas das do workspace). 409 do
  endpoint não é erro.
- **NUNCA troque `deploymentTarget` para `"scheduled"` no `.replit`.** Aquele
  bloco descreve o deployment DO APP; `scheduled` é um job de cron que roda e
  sai, sem servir HTTP. Publicar assim tira o site e a API do ar. O gatilho
  precisa de um deployment SEPARADO, criado no painel.
- Com os timers desligados, um gatilho que nunca foi criado (ou que parou) faz
  os alertas sumirem **em silêncio** — o log só diria "Timers de checkers
  desligados", que parece intencional. Por isso existe o vigia
  (`lib/checker-watchdog.ts`): sem ciclo há mais de 20 min, ERROR no log.
- Complemento de config do usuário: Max machines = 1 no deployment Autoscale.
  Além de eliminar a duplicação, concentra o tráfego numa instância só, o que
  a mantém quente por mais tempo.
