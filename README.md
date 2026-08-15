# Pré-Mercado — Agente de Análise de Bolsa

Agente autônomo de análise pré-mercado para o ecossistema de semicondutores/IA:
relatórios diários gerados por LLM sobre dado verificado, gestão de carteira,
alertas de mercado, estudos probabilísticos de entrada/saída e parâmetros de
risco derivados de volatilidade e correlação medidas.

> **Aviso**: infraestrutura de análise, não recomendação de investimento. Todo
> número aqui descreve o passado ou uma projeção estatística explícita.

Documento de referência para retomar o contexto do projeto. Para o dia a dia de
operação (env vars, comandos, stack) ver também [`replit.md`](replit.md);
para o modelo de ameaças, [`threat_model.md`](threat_model.md).

---

## Índice

- [Arquitetura](#arquitetura)
- [O que o agente faz](#o-que-o-agente-faz)
  - [1. Loop agêntico e relatórios](#1-loop-agêntico-e-relatórios)
  - [2. Carteira e performance](#2-carteira-e-performance)
  - [3. Alertas](#3-alertas)
  - [4. Painel de Cenários](#4-painel-de-cenários)
  - [5. Estudo de Entrada e Saída](#5-estudo-de-entrada-e-saída)
  - [6. Reação a Earnings](#6-reação-a-earnings)
  - [7. Radar IA 2026](#7-radar-ia-2026)
  - [8. Risco, backtest e confluência](#8-risco-backtest-e-confluência)
  - [9. Exportar relatório das telas de análise](#9-exportar-relatório-das-telas-de-análise)
  - [10. Chat e memória](#10-chat-e-memória)
- [Telas](#telas)
- [Jobs de background](#jobs-de-background)
- [Fontes de dado](#fontes-de-dado)
- [Convenções e armadilhas do repo](#convenções-e-armadilhas-do-repo)
- [Operação](#operação)
- [Histórico de PRs](#histórico-de-prs)

---

## Arquitetura

Monorepo pnpm com três camadas que se comunicam por processo e por banco:

```
artifacts/api-server/     Node + Express (TypeScript)
  src/routes/             ~30 arquivos de rota HTTP
  src/lib/                checkers de background, runner do agente, validadores
  src/agent/              ~50 scripts Python (o "agente" propriamente dito)
artifacts/premarket/      React + Vite (36 telas)
lib/db/                   schema Drizzle + migrations (28 tabelas)
lib/api-spec, api-zod, api-client-react   contrato de API (mantidos à mão)
lib/scenario-math/        matemática lognormal compartilhada TS
```

**O Node não calcula nada de mercado.** Ele orquestra: recebe HTTP, spawna
Python, persiste no Postgres, dispara e-mail. Todo cálculo de preço, vol,
correlação e probabilidade vive nos scripts Python, que são a fonte de verdade
numérica. **O Python, por sua vez, não acessa o banco** — recebe entrada por
`argv`/stdin e devolve JSON por stdout.

Deploy: Docker Compose em VPS (Hetzner), Caddy como reverse proxy com TLS.
Postgres no mesmo compose. Ver [`docs/deploy-fora-do-replit.md`](docs/deploy-fora-do-replit.md).

---

## O que o agente faz

### 1. Loop agêntico e relatórios

Loop multi-turno com **tool calling** sobre 35 ferramentas (`agent/tools.py`) e
**cadeia de fallback entre provedores** de LLM (Anthropic, OpenAI-compatível,
Gemini, OpenRouter, Kimi). Modos:

| Modo | O que faz |
|---|---|
| `premarket` | Relatório diário completo da carteira antes da abertura |
| `portfolio` | Análise focada nas posições abertas |
| `ai` / `coal` | Relatórios setoriais |
| `news` | Varredura de notícias |
| `manual` | Execução avulsa |

**Consenso entre provedores** (`consensus_report.py`): o relatório da carteira
roda em 3 provedores independentes e publica, ticker a ticker, o rótulo
(🟢/🟡/🔴) que a maioria deu — marcando divergência explicitamente quando os
três discordam, em vez de escolher sozinho.

**Dois validadores determinísticos** auditam o que o LLM escreveu, porque
prompt é pedido e não garantia:

- `veredito_validator.py` — recalcula percentuais citados, checa frescor de RSI,
  dia da semana, earnings fantasma ("pós-earnings" antes do balanço acontecer),
  claim de "flat" sem número, "distribuição" atribuída a papel em perfil de
  fundo, e concentração por correlação (2+ compras no mesmo cluster). Erro
  dispara **um retry de correção**.
- `report_validator.py` — gates de rótulo do relatório diário (proíbe 🟢 em
  papel caindo com RSI baixo, etc.).

**Orçamento de tempo**: `AGENT_SOFT_DEADLINE_MS` força um turno final sem
ferramentas quando o tempo acaba, garantindo relatório parcial em vez de
processo morto sem saída.

### 2. Carteira e performance

Posições com **lotes de compra** (`portfolio_purchases`) como fonte de verdade —
o campo `quantity` da posição existe só para correção manual e **nunca** deve
ser usado para decidir se uma posição está ativa (ver [armadilhas](#convenções-e-armadilhas-do-repo)).

Cobre preço médio, P&L realizado e não realizado, backfill de preços históricos,
caixa, câmbio USD/BRL e curva de performance.

### 3. Alertas

**Alertas de preço** definidos pelo usuário (acima/abaixo de nível absoluto ou
variação %), com histórico de disparos e notificação por e-mail.

**Checks automáticos de mercado** (`market_alerts.py`) rodando no ciclo de
background:

- sobrecompra (RSI calibrado por ticker), gap de volume, padrões de candle
- *dead cat bounce*, *squeeze setup*, spike intradiário
- proximidade de earnings, mudanças de recomendação de analista, *sell the news*
- contágio de setor (bellwethers caindo), pressão asiática (SK Hynix / KRX)
- gatilhos macro (FOMC, CPI, PCE, payroll), regime macro por notícia
- EDGAR (filings novos), circuit breaker / halt
- **contágio de earnings** — empresa correlacionada reporta em ≤2 dias, avisa
  quais posições estão expostas e com que correlação medida
- **dedup de sinal por cluster** — dois tickers com correlação ≥0.70 alertando
  no mesmo ciclo é o mesmo trade contado duas vezes
- **overnight asiático** — Coreia/Taiwan/Japão/China fecham horas antes de NY;
  o movimento vira impacto estimado por posição

### 4. Painel de Cenários

Probabilidade de a carteira "empatar" (voltar ao preço médio) até uma data-alvo,
com premissa explícita de movimento de setor. Matemática lognormal em
`lib/scenario-math` (compartilhada TS) com adição da variância de salto de
earnings quando o balanço cai dentro da janela. Snapshots diários, resolução ao
vencer e alertas configuráveis.

### 5. Estudo de Entrada e Saída

Acompanhamento por ticker: **probabilidade de bater um preço-alvo até uma data**.

- **Número principal com drift zero** — só a volatilidade real do papel, sem
  viés direcional embutido. Decisão deliberada: o modelo não finge saber a
  direção.
- **Número alternativo com momentum** — mesma matemática somando drift de
  `beta × momentum do setor`, rotulado como premissa explícita, nunca como o
  número principal.
- Salto de earnings somado à variância quando o balanço cai na janela.
- **Três alertas automáticos** por estudo: saída acima do alvo, entrada na média
  das mínimas de 6 meses, e entrada no **nível projetado pela vol** (não na
  mínima de 12 meses — para papel que subiu muito, aquele piso histórico fica
  tão longe que o alerta nunca dispararia).
- Snapshot diário persistido → gráfico de evolução da probabilidade.
- Notícias traduzidas para pt-BR, com selo "nova" e rótulo de sentimento por LLM
  (informativo; **não** entra no cálculo).
- Resolução automática ao vencer a data-alvo (bateu / não bateu).

### 6. Reação a Earnings

Parametriza a volatilidade esperada em torno de balanços a partir do histórico
real, em vez do "calor do momento". Por evento: gap de abertura, variação de
fechamento, range intradiário, volume vs. média. Como o yfinance não informa de
forma confiável se o resultado saiu antes da abertura (BMO) ou depois do
fechamento (AMC), o script **reporta as duas janelas** e deixa o dado dizer qual
foi a reação real.

Inclui o **run-up pré-earnings** (variação dos 21 pregões anteriores) cruzado
com a direção da reação — o padrão "bom não é bom o suficiente": papel que chega
esticado tende a cair mesmo com resultado forte. Os contadores por bucket
("X de N esticados caíram") acompanham a correlação de Pearson, que numa amostra
de ~8 eventos é indício, nunca prova.

### 7. Radar IA 2026

Camada de dados estruturais sobre o tema de IA/semicondutores.

**Núcleo** (`radar_ia_2026.py`): matriz de correlações medidas, calendário de
earnings com EVR e move implícito, YTD/vol/beta por ticker, riscos mapeados,
screening de proximidade da mínima de 52 semanas.

**Correlações vivas** (`atualizar_correlacoes.py`): recalcula a matriz completa
do universo a partir de 6 meses de fechamentos do yfinance, sobre **retornos
diários** (não nível de preço — dois papéis em tendência exibem correlação de
nível altíssima sem co-movimento real). Grava um **overlay JSON** que o núcleo
aplica por cima do snapshot embutido no import. Roda sozinho toda semana.

**Parâmetros operacionais** (`parametros_volatilidade.py`):

- classe de regime de vol, conversões semanal/diária/anualizada
- **stop sugerido** como múltiplo da vol — a classe *extrema* usa o **menor**
  múltiplo de propósito: em papel que oscila 18%/semana, alargar o stop
  proporcionalmente cria um stop que só existe no papel; a resposta certa é
  posição menor
- **teto de posição** por orçamento de risco
- **vol de carteira com covariância completa**, contribuição marginal de risco
  por posição e **cenário de stress** (correlações ×1.6) — corrigindo o sizing
  ingênuo que ignorava covariância e subestimava o risco justamente numa cesta
  onde tudo é do mesmo cluster

**Camada macro** (`parametros_macro.py`): modo FOMC (vol inflada, com adicional
para beta alto em reunião com dot plot — derivado do mês, regra do Fed) e sinal
overnight com o proxy líder de cada posição **calculado do dado**, não de texto
fixo.

### 8. Risco, backtest e confluência

- **Backtest** de estratégias por ticker, por cesta e análise de sensibilidade
- **ConfluenceEngine** — sinal por consenso multi-indicador (trend, momentum,
  volatilidade, volume, setor). O catalisador de calendário **não é um voto: é
  um veto** — perto do evento força "flat" independente dos outros sinais
- **Risco**: correlação e exposição da carteira, métricas, beta intradiário,
  position sizing
- **Plano de Saída**: itens com prazo, revisados pelo agente

### 9. Exportar relatório das telas de análise

Oito telas de análise (Backtest, Radar IA, Cenários, Veredito, Reação a
Earnings, Estudo de Entrada/Saída, Setor IA, Setor Carvão) têm **Salvar
relatório** e **Enviar por e-mail**. A tela monta um markdown com os números
que está exibindo; `POST /reports/export` grava em `reports` com um `mode`
próprio (`tela_*`) e, se pedido, envia.

Três decisões que valem lembrar:

- O relatório é sempre gravado com o **`userId` de quem clicou**, mesmo nas
  telas que não derivam de carteira. Export é retrato pessoal — gravar com
  `userId` nulo o publicaria para todos os usuários.
- O e-mail vai para o **endereço de login de quem clicou**, não para o
  `notifyEmail` das configurações (que é o endereço de alertas da casa).
- Falha de SMTP **não** vira erro na tela: o relatório já foi gravado, então a
  resposta é 200 com `erroEnvio`. Devolver erro faria a pessoa reclicar e
  duplicar o registro no histórico.

O Histórico ganhou a aba **Exportados**, e os modos `tela_*` não caem mais no
rótulo genérico "diário".

### 10. Chat e memória

Chat conversacional com as mesmas ferramentas do agente, contexto rico da
carteira e memória filtrada por identidade. Sessões persistidas.

---

## Telas

36 telas React. As principais:

| Tela | Conteúdo |
|---|---|
| Dashboard | Relatório do dia, status do agente, cards de mercado |
| Carteira / Performance | Posições, lotes, P&L, curva |
| Cenários | Probabilidade de empate, termômetro diário |
| Veredito do Dia | Síntese opinativa validada |
| **Estudo Entrada/Saída** | Probabilidade de alvo, histórico, notícias, alertas |
| **Reação a Earnings** | Estatística de reação + run-up prévio |
| **Radar IA** | Earnings, correlações, tema IA, riscos, mínimas de 52s |
| Alerts / Runs / Gastos com IA | Operação e custo |
| Screener, Técnicos, Cotações, Gráfico, Bolhas, Short, Analistas, Opções, Notícias, Macro, Cripto | Consulta de mercado |
| Backtest, Calculadora, Watchlist, Diário, Plano de Saída | Ferramentas de decisão |

---

## Jobs de background

Disparados por `POST /checkers/run` a cada 5 min (Scheduled Deployment
autenticado), com trava e cadência coordenadas por linha única no Postgres
(`checker_lease`) — não em memória, porque pode haver mais de uma instância.

| Etapa | Cadência | O que faz |
|---|---|---|
| `alerts` | todo ciclo | Alertas de preço do usuário |
| `market` | todo ciclo | Spike, bounce, squeeze (batelados num processo só) |
| `portfolio` | 15 min | Alertas da carteira |
| `scenario_alerts` | 60 min | Alertas do Painel de Cenários |
| `scenario_params` | 24 h | Recalcula vol/beta e momentum do setor |
| `entry_exit_study` | 24 h | Snapshot diário dos estudos + resolução |
| `radar_correlacoes` | 7 dias | Correlações e vol medidas do radar |

---

## Fontes de dado

**yfinance é a espinha dorsal** — 24 módulos dependem dele: cotação, histórico,
earnings, técnicos, vol, beta, fundamentos, opções.

Fontes opcionais, todas **fail-open** (sem a chave, a seção some ou mostra como
ativar, em vez de quebrar): FRED (macro), FMP (valuation/DCF), Finnhub e Alpha
Vantage (notícias), Quiver (Congresso), Unusual Whales (dark pool), Form4API
(insider), ApeWisdom (Reddit), FINRA (short volume), SEC EDGAR (filings),
FlashAlpha (gamma), ROIC (transcrições).

> A **Analytics API da Alpha Vantage** (`ANALYTICS_FIXED_WINDOW`) é paga —
> devolve 403 persistente no plano free mesmo com chave válida. Por isso as
> correlações são calculadas localmente do yfinance.

---

## Convenções e armadilhas do repo

Aprendidas de incidentes reais. Detalhamento em
`.claude/skills/premercado-playbook/`.

1. **Nunca confie em campo derivado quando dá pra recalcular da fonte.** Uma
   posição totalmente vendida continuava "ativa" porque `quantity` foi editado à
   mão e ninguém recalculou dos lotes. Corrigido em 4 lugares independentes —
   qualquer novo consumidor de "posições ativas" precisa filtrar pelos lotes.
2. **yfinance: `fast_info` e `.history()` divergem.** Usar `fast_info.previous_close`
   para calcular variação já produziu percentual com **sinal trocado** em vários
   tickers ao mesmo tempo. Para fechamento anterior, sempre `.history()`.
3. **Subprocesso Python precisa morrer de verdade.** `ThreadPoolExecutor` espera
   as threads no `__exit__` mesmo se a aplicação já desistiu; sem
   `bounded_parallel_map` + `exit_now` (que usa `os._exit`), o processo fica
   preso até a chamada de rede terminar. Já derrubou 4 checkers ao mesmo tempo.
4. **Timezone**: `date.today()`/`new Date()` usam o fuso do processo (UTC no
   container). Para qualquer coisa que diga "hoje" ao usuário, usar
   `brt.today_brt()` / `todayBRTDateString()`.
5. **Postgres `numeric` chega como string** via Drizzle, mesmo com
   `.$type<number>()`. Sempre `Number(...)` antes de aritmética.
6. **Mudança de schema tem TRÊS lugares**: schema Drizzle, migration `.sql`
   numerada, e o bloco idempotente em `ensure-schema.ts` (bootstrap de boot).
7. **Os arquivos "generated" de API são mantidos à mão** — não existe codegen
   funcional. Ao adicionar campo: `openapi.yaml` + `api-zod` + `api-client-react`.
8. **Modelo fraco na cadeia de fallback quebra o protocolo**: já alucinou
   tool-call como texto plano e abandonou fluxo obrigatório no meio. Assuma que
   pode acontecer e cobre o resultado.
9. **Tabela larga precisa de `overflow-x-auto` E `min-w`** — o overflow só rola
   quando o filho é maior que o pai; sem `min-w` a tabela espreme as colunas até
   o conteúdo sumir no celular.
10. **Dado de fonte externa merece checagem cruzada.** A vol coletada à mão
    discordava da medição do próprio agente em 2,5× para o INTC, e isso
    contaminava stop e sizing em silêncio.

---

## Operação

```bash
# Deploy (VPS)
cd /opt/premercado && git pull origin main && docker compose up -d --build

# Qualidade (não há CI — rodar antes de cada PR)
pnpm run typecheck
pytest artifacts/api-server/src/__tests__      # ~762 testes
pnpm --filter @workspace/api-server test       # vitest
pnpm --filter @workspace/premarket test

# Ferramentas do radar (dentro do container)
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.atualizar_correlacoes < /dev/null
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.parametros_volatilidade < /dev/null
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.earnings_reaction_analysis --tickers NVDA < /dev/null
```

> `< /dev/null` é necessário: os scripts detectam "chamado pelo Node" testando
> `stdin.isatty()`, e `docker compose exec -T` deixa o stdin aberto sem TTY —
> sem o redirecionamento, o script espera um EOF que nunca chega.

> Um `--build` apaga `/var/cache/premercado`, onde mora o overlay de correlações.
> O checker semanal regrava sozinho; para não esperar, rodar o comando acima.

---

## Histórico de PRs

Agrupado por tema. Números são links no GitHub (`haohmarusc-glitch/Premercado`).

### Radar IA 2026 — correlação, volatilidade e macro (ago/2026)

| PR | O que entregou |
|---|---|
| #266 | Núcleo do radar: dados, contágio de earnings, dedup por cluster, concentração no veredito, tela nova |
| #267 | `atualizar_correlacoes.py` via Alpha Vantage + mecanismo de overlay |
| #268 | Troca para yfinance (Analytics API é paga) + tabelas roláveis no celular |
| #269 | Parâmetros de vol: stops, sizing, vol de carteira **com covariância**, stress; camada macro (FOMC) e sinal overnight |
| #270 | Proxy líder por posição vindo do dado + refresh semanal automático |
| #271 | Vol **medida por nós** substitui a coleta manual (contaminava stop e sizing) |

### Backtest e exportação (ago/2026)

| PR | O que entregou |
|---|---|
| #273 | Walk-forward com validação out-of-sample: otimiza no treino, mede na janela seguinte |
| #275 | Campos de janela (treino/teste/objetivo) na tela — a rota já aceitava, a tela não enviava |
| #276 | Salvar relatório e enviar por e-mail nas oito telas de análise |

### Estudo de Entrada e Saída (ago/2026)

| PR | O que entregou |
|---|---|
| #256 | Feature completa: schema, calculadora, rotas, checker diário |
| #258 | Fix: `get_earnings()` não retornava nada, só imprimia |
| #259 | Tela dedicada |
| #260 | Testes de integração contra Postgres real |
| #261 | Resolução ao vencer, notícias persistidas, gráfico, edição |
| #262 | Tradução, selo "nova", sentimento por LLM, aviso de provedor sem crédito |
| #263 | Probabilidade alternativa com drift de momentum do setor |
| #265 | Nível de entrada projetado pela vol (mínima de 12m não servia para papel esticado) |

### Reação a Earnings

| PR | O que entregou |
|---|---|
| #264 | Run-up pré-earnings × direção da reação ("bom não é bom o suficiente") |

### Segurança e infraestrutura

| PR | O que entregou |
|---|---|
| #257 | **Senha em texto puro no log** — `err.body` do body-parser era logado sem filtro |
| #248 | Admin-gating em rotas compartilhadas, rate limit de auth, CI |
| #250 | `internal.ts` derrubava a API inteira para tráfego via Caddy |
| #249, #246 | Stack Infra/Monitor (Netdata, Uptime Kuma, Dozzle) |
| #226, #231 | Empacotamento para rodar fora do Replit |
| #241 | CSP liberando TradingView + fallback do DeepSeek |

### Provedores de LLM e desempenho do agente

| PR | O que entregou |
|---|---|
| #212 | Disjuntor de provedor morto, probe de todos, corrida da FMP |
| #210, #215, #213, #214 | Corte por `max_tokens` visível, tiers do Gemini, probe corrigido |
| #245 | Relatório da carteira com **consenso entre 3 provedores** |
| #216–#224 | Batelamento de checkers, contagem de subprocessos, junção de buscas em voo, orçamento antes do import pesado |
| #232 | Teto de Python simultâneo vindo de rota HTTP |
| #225 | Cache em disco do histórico diário, compartilhado entre processos |
| #237, #238, #239 | Tool calling no chat, contexto rico, memória filtrada |

### Notícias

| PR | O que entregou |
|---|---|
| #242, #243, #247 | Busca por ticker avulso, link para a matéria, manchete clicável |
| #244 | Filtra manchete não relacionada, marca correlação entre tickers |
| #252, #253 | Alpha Vantage News & Sentiment; fix do `topics=` (interseção, não união) |

### Correções de dado

| PR | O que entregou |
|---|---|
| #240 | Relatório diário cobria além da carteira |
| #251 | `get_quotes.py` não sinalizava ticker cortado pelo orçamento |
| #254 | Dependência `lxml` faltando — `get_earnings_dates` falhava em silêncio |
| #255 | Agente nunca limpava item do plano de saída de posição já vendida |

Histórico completo: `git log` ou a
[lista de PRs](https://github.com/haohmarusc-glitch/Premercado/pulls?q=is%3Apr+is%3Aclosed).
