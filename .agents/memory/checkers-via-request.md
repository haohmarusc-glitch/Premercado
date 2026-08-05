---
name: Checkers via request agendado
description: Endpoint HTTP + trava em Postgres para rodar os checkers sem timer — necessário no Autoscale, opcional na Reserved VM (onde os timers voltam a ser o padrão)
---

# Checkers via request agendado

**Estado atual (05/08, depois da migração para Reserved VM): os timers estão
LIGADOS de novo e este endpoint não é usado no dia a dia.** Ele continua no
código como disparo manual e como a saída pronta caso a plataforma volte a ser
uma que estrangula processo ocioso ou duplique instâncias. O resto deste
documento descreve quando e por que ele é necessário.

Regra: numa plataforma que mantém várias instâncias (Autoscale/Cloud Run),
trabalho pesado de fundo não roda por `setInterval` — roda dentro de um request
HTTP disparado por um agendador externo. Numa VM dedicada, `setInterval` é o
modelo certo e mais simples.

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
- Timers internos: padrão LIGADO fora de development (Reserved VM).
  `RUN_BACKGROUND_CHECKERS=0` desliga, que é o que se usa quando os ciclos vêm
  do endpoint. Um padrão `false` sem gatilho externo configurado faz NENHUM
  checker rodar, e o log só diz "Timers de checkers desligados" -- parece
  intencional. Ver background-checkers.ts.
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
