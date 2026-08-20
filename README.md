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
  - [11. Risco macro setorial](#11-risco-macro-setorial)
- [Telas](#telas)
- [Jobs de background](#jobs-de-background)
- [Fontes de dado](#fontes-de-dado)
- [Convenções e armadilhas do repo](#convenções-e-armadilhas-do-repo)
- [Operação](#operação)
- [Histórico de PRs](#histórico-de-prs)
- [Diário](#diário)

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

O módulo tem **três camadas com validades diferentes**, e a tela rotula cada
uma — confundi-las foi o erro que a auditoria de 17/08/2026 pegou:

| Camada | Como atualiza | Cadência |
| --- | --- | --- |
| Preço e faixa de 52 semanas | `atualizar_min52_vivo()` na própria resposta | a cada request |
| Correlações e vol medida | `atualizar_correlacoes.py` → overlay | semanal |
| Calendário de earnings | `atualizar_earnings.py` → overlay | diário |
| EVR, move implícito, reação histórica | transcrição manual do OptionSlam | quando alguém abre o site |

`HOJE_SNAPSHOT` descreve a **última** linha, não as três primeiras.

**Correlações vivas** (`atualizar_correlacoes.py`): recalcula a matriz completa
do universo a partir de 6 meses de fechamentos do yfinance, sobre **retornos
diários** (não nível de preço — dois papéis em tendência exibem correlação de
nível altíssima sem co-movimento real). Grava um **overlay JSON** que o núcleo
aplica por cima do snapshot embutido no import. Roda sozinho toda semana.

**Calendário vivo** (`atualizar_earnings.py`): busca o `EARNINGS_CALENDAR` da
Alpha Vantage e sobrescreve as datas e a janela (BO/AC) do calendário
embutido. **Diário**, ao contrário das correlações: são os dois extremos da
mesma escala — correlação de 6 meses se move devagar e o que importa é
mudança de regime; data de earnings vira passado em dias, e a confirmação
oficial sai na semana anterior, que é justo quando o dado importa.

Pede o calendário **inteiro numa chamada só** e filtra local. O endpoint
aceita `symbol`, mas o universo tem ~51 papéis e a cota diária compartilhada
com o feed de notícias é 15 — pedir por ticker esgotaria a cota e derrubaria
as notícias junto. (Medido: transcrevendo o universo à mão pelo MCP em
19/08/2026, a chave estourou na 30ª chamada.)

Quem editar o `EARNINGS` à mão: o campo `nota` é reservado a especulação
**sobre a data** e o overlay o remove quando a fonte responde — manter
"fontes divergem" embaixo da data confirmada faria a tela contradizer o
próprio dado.

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
  um veto** — perto do evento força "flat" independente dos outros sinais.
  O backtest é **long-only por padrão** desde 20/08/2026: "sell" fecha posição,
  nunca abre short — o diagnóstico mediu os shorts perdendo em **6 de 6**
  células ticker × regime, inclusive no downcycle (a MU caiu 32% e os shorts
  perderam 14,9%: o motor entra vendido depois da fraqueza confirmada e apanha
  do repique). O voto vendedor continua na leitura da tela.
  A execução é **honesta desde 20/08/2026**: o sinal do candle D executa na
  abertura de D+1 (antes executava no próprio fechamento de D — um preço que
  não existia na hora da decisão), e stop/take-profit checam o pregão inteiro
  via High/Low, com política conservadora (stop e target no mesmo candle →
  assume o stop; gap de abertura através do nível → sai no open, sem fingir
  fill no nível).
  **Importante**: como estratégia, o motor não tem edge demonstrado — ver o
  Diário de 20/08/2026. O valor dele hoje é como tela de leitura
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
- Tabela markdown vira `<table>` com **estilo inline em cada célula**. A
  primeira versão deixava a tabela como texto contando com o `font-family`
  monoespaçado do bloco `<style>`; o Gmail no celular ignora esse bloco e o
  relatório chegou como linhas de `|` cruas, quebrando de linha. Estilo inline
  é o único que nenhum cliente de e-mail descarta.

O Histórico ganhou a aba **Exportados**, e os modos `tela_*` não caem mais no
rótulo genérico "diário".

### 10. Chat e memória

Chat conversacional com as mesmas ferramentas do agente, contexto rico da
carteira e memória filtrada por identidade. Sessões persistidas.

---

### 11. Risco macro setorial

Seis sinais de pano de fundo para IA/semicondutores, derivados de dois
episódios reais (o sell-off de 28-29/07/2026 e o choque de petróleo de
18/08/2026), que viraram golden dataset em `test_macro_risk.py`.

| sinal | fonte | o que detecta |
|---|---|---|
| `RATE_SHOCK` | FRED `DGS30` | yield de 30 anos disparando |
| `ASIA_MEMORY_CONTAGION` | `^KS11`, SK Hynix, Samsung | Coreia fecha 6-8h antes dos EUA — leading indicator |
| `PRICED_FOR_PERFECTION` | `earnings_dates` | bateu o consenso e a ação caiu |
| `CHINA_COMPETITION_RISK` | `get_geopolitical_news` | notícia atacando a tese de escassez |
| `OVEREXTENDED_SECTOR` | `^SOX` (reserva `SOXX`) | quanto o setor subiu em 9 semanas |
| `GEOPOLITICAL_OIL_SHOCK` | `CL=F` + FRED `DGS10` | petróleo e yield subindo juntos |

**Não é sinal de compra ou venda: é modulador** do Kelly no
`confluence_engine`. Reduz o tamanho sugerido de posição quando o pano de fundo
amplifica qualquer gatilho — e **também quando o sistema está cego**, contando
sinal não medido como meio flag ativo.

Cada sinal tem **três** estados (`ok` / `sem_dado` / `nao_aplicavel`), e o
agregado publica `cobertura_pct` junto do score. Abaixo de 50% de cobertura o
score é `None`: um 0 apurado sobre um terço do peso tem a mesma cara de um 0
apurado sobre tudo, e diz coisa muito diferente.

Retrato diário persistido em `macro_risk_snapshots` (upsert por data), coletado
às 07:50 BRT — 19:50 em Seul, com a Ásia fechada e o FRED já publicado. A tela
fica no topo de `/macro`, com o estado "sem dado" **listrado**, não cinza:
num painel de risco, silêncio lido como calmaria é pior que erro.

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
| `radar_earnings` | 24 h | Datas e janela (BO/AC) do calendário do radar |

Fora desse ciclo, o **retrato de risco macro** roda pelo agendador interno
(`node-cron` em `lib/scheduler.ts`), às 07:50 BRT em dias úteis. Não passa por
`runAgent()` de propósito: aquilo serializa por `state.running` para não rodar
duas análises de LLM juntas, e o retrato seria PULADO nos dias em que o diário
atrasa — justamente os dias movimentados.

---

## Fontes de dado

**yfinance é a espinha dorsal** — 24 módulos dependem dele: cotação, histórico,
earnings, técnicos, vol, beta, fundamentos, opções.

**Fallback de continuidade** (`market_data_provider.py`): se o Yahoo bloquear o
IP do VPS ou mudar formato, existe uma cadeia de degradação explícita em vez de
perder tudo ao mesmo tempo —

```
yfinance (retry curto)
  → cache dentro do TTL
  → cache VENCIDO, conferido contra a fonte externa
  → Alpha Vantage TIME_SERIES_DAILY (marcada como tal)
  → erro explícito (nunca dado inventado)
```

Um disjuntor por provedor (`provider_health.py`, arquivo em `/tmp` compartilhado
entre os processos Python) abre após 3 falhas seguidas e evita que cada checker
redescubra a queda pagando o timeout inteiro.

> **A fonte externa é fallback de continuidade, não fonte de verdade.**
> `TIME_SERIES_DAILY` devolve OHLCV "as traded" — a versão ajustada por
> split/dividendo é paga. Serve para indicador técnico e para a tela não ficar
> sem gráfico; **nunca** para P&L, preço médio ou qualquer coisa que vire
> dinheiro. A cotação por ela é sempre fechamento do dia anterior e vem com
> `is_delayed=True` — a UI mostra "atrasado", nunca finge preço ao vivo.
>
> A chave é a **mesma do feed de notícias**, então toda chamada de fallback
> passa por um teto diário (`AGENT_ALPHAVANTAGE_MAX_DIA`, padrão 15). Sem ele,
> um dia de yfinance fora esgotaria a cota e derrubaria as notícias junto —
> uma falha parcial viraria duas.

> **O Stooq foi descartado.** Era a escolha do plano original por ser gratuito
> e sem chave, mas medido do IP do VPS ele devolve 404 para cliente
> programático e, com User-Agent de navegador, um 200 cujo corpo é uma página
> de desafio anti-bot (proof-of-work em JavaScript). Fingir navegador trocaria
> um 404 limpo por um 200 com HTML no lugar de preço.

`provider_preflight.py` mede as duas fontes antes do deploy (`python -m
agent.provider_preflight`, exit 0/1/2) e roda como passo informativo no CI.

**Adoção**: `get_quotes.py` e `market_alerts.py` são os primeiros dos 24
módulos ligados à cadeia. Em `market_alerts` o critério não é lote e sim
**período**: só os cacheáveis (3mo+) vão para a cadeia. Os curtos (`5d`,
`1mo`, `2mo`) são pedidos em laço por ticker e drenariam sozinhos a cota do
dia antes do 6mo/1y, que é o que alimenta RSI, médias e tendência. Quando um
indicador sai de dado degradado, o ciclo emite um alerta `ATENCAO` dizendo de
qual fonte veio — indicador silencioso sobre série velha é pior que indicador
nenhum.

`get_technicals.py` entrou com a regra mais restritiva: pede série **ajustada**
(`auto_adjust=True`), então a cadeia vai até o cache vencido e **para**
(`permitir_externa=False`). A fonte externa é "as traded" — um split dentro da
janela viraria degrau de preço, e RSI/médias sairiam com um salto que nunca
existiu. O cache vencido continua valendo porque foi gravado do yfinance, já
ajustado.

`get_trend.py` entrou com a mesma regra, mais uma: quando o histórico vem
degradado, o resultado sai **marcado** (`stale` + `fonteHistorico`). Sem isso a
integração seria uma piora — o módulo já marcava resultado velho no
stale-if-error, e calcular sobre série vencida devolvendo resultado "fresco"
esconderia a degradação num campo que já existia para revelá-la. Um
`sinal: compra` calculado sobre o fechamento de ontem, sem aviso, é o caso a
evitar.

`entry_exit_study.py` é o caso mais delicado, porque tem DUAS buscas com
riscos diferentes. O **histórico** (mínimas, níveis de entrada) usa a cadeia
inteira — série não ajustada, e média de mínimas sobre dado de ontem continua
útil. O **preço atual** continua exigindo yfinance ao vivo: ele entra em
`log(alvo/preço)` e define a probabilidade inteira, então servir o fechamento
de ontem como "preço atual" mudaria a resposta sem mudar a pergunta. Se o
preço ao vivo não vier, o estudo falha — de propósito.

`confluence_engine.py` exigiu um ajuste antes: `18mo`, o período padrão dele,
não estava em `PERIODOS_CACHEAVEIS` — sem cache, e com a fonte externa cortada
por ser série ajustada, integrar não mudaria nada. Pelo critério do próprio
`hist_cache` ("períodos em que o candle de hoje não domina o resultado") ele se
qualifica igual a `1y` e `2y`; ficou de fora só porque ninguém usava esse
período quando a lista foi escrita. O caminho `start/end` do módulo continua
direto no yfinance: a cadeia trabalha em período, e é caminho de investigação
manual, não do ciclo automático.

`risk_manager.py` trouxe o formato que faltava: `correlation` e
`portfolio_risk_metrics` consomem VÁRIOS tickers numa chamada (`yf.download`
em lote). `get_daily_closes_batch` no provider mantém o lote no caminho feliz
(uma chamada de rede, como antes) e desce a cadeia **por ticker** quando o
lote falha — `fontes` diz de onde veio cada coluna, e ticker sem fonte não
vira coluna de NaN. No sucesso, cada recorte OHLCV **completo** do lote é
gravado no `hist_cache` com a chave normal (o lote de hoje é o fallback de
amanhã); recorte parcial nunca é gravado — o cache é compartilhado com
`get_technicals`/`get_trend` e um frame com metade das colunas seria corrupção
silenciosa.

`get_scenario_params.py` fechou a fila: vol anual, beta setorial e momentum do
benchmark vêm de um único `yf.download` em lote — a mesma forma do
`risk_manager`, então a adoção foi só trocar a chamada por
`get_daily_closes_batch` (série ajustada, fonte externa cortada) com o
benchmark **deduplicado no mesmo lote**. São números que mudam devagar: servir
cache de ontem numa queda do Yahoo é aceitável desde que rotulado
(`fontesDegradadas` por ticker). O contrato de erro
(`{params: {ticker: {error}}}`) não mudou — o checker que persiste o resultado
não precisou mudar.

**Fora da adoção, por decisão**: `get_historical_price.py` alimenta o
`purchasePrice` dos lotes — base do preço médio e do P&L. Diferente de um
indicador, que é recalculado no ciclo seguinte, o preço de compra é gravado no
banco e nunca mais recalculado. Se o yfinance estiver fora, o certo é falhar e
deixar o usuário informar o preço à mão (o fluxo já existe), não preencher com
uma aproximação que ninguém vai auditar depois. A exclusão está escrita na
docstring do próprio módulo.

Em `get_quotes.py`: O
fallback é decidido por **lote, nunca por símbolo** — um ticker isolado sem
preço quase sempre é o próprio ticker (deslistado, digitado errado) e falharia
em qualquer fonte; o lote inteiro sem preço é o sintoma real de Yahoo fora do
ar. O registro no disjuntor também é uma vez por lote. Quando o fallback entra,
a resposta carrega `isDelayed`/`source`/`sourceWarnings`, e o layout mostra uma
faixa de aviso — preço de ontem sem rótulo é pior que preço nenhum.

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
11. **Existe um `agent.py` DENTRO do pacote `agent/`.** Inserir `src/agent/` no
    `sys.path` (vários testes fazem, para `from brt import ...`) faz o nome
    `agent` resolver para o módulo em vez do pacote, e todo
    `from agent.x import y` coletado depois estoura. O sintoma engana: a suíte
    inteira passa e o mesmo arquivo falha noutra ordem de coleta. O
    `conftest.py` fixa o pacote em `sys.modules` antes de tudo — mas em teste
    novo prefira import de pacote e não mexa no path.
    **O mesmo vale para spawn**: script que usa `market_alerts`, `tools` ou
    qualquer módulo com import relativo tem de ser spawnado como
    `-m agent.xxx`, com `cwd` e `PYTHONPATH` no diretório do agente. Rodar por
    caminho põe `agent/` no `sys.path` e reproduz o sombreamento — o sintoma é
    `attempted relative import with no known parent package` numa fonte só,
    enquanto as outras seguem, então o resultado sai **degradado em silêncio**
    em vez de estourar.
12. **Checagem cruzada só vale onde as duas pontas existem.** Na primeira
    versão do `market_data_provider.py` a comparação de fechamento ficava no
    ramo do Stooq, onde o cache é sempre `None` por construção — nunca rodava.
    Guarda que não dispara é pior que guarda nenhuma: passa confiança sem dar
    proteção.
13. **"Sem dado" é um estado, não é "sem risco".** O módulo de risco macro
    devolvia score 0 tanto para "medi e está calmo" quanto para "não consegui
    medir" — e num módulo que dimensiona posição isso INVERTE o sentido da
    falha: coleta quebrada vira permissão para posição cheia. Mesma classe do
    `null` que virava "volatilidade histórica baixa" na tela de earnings.
    Ausência de dado tem de alargar a cautela, nunca removê-la, e a camada que
    busca precisa devolver `None` — um `except` que devolve `0.0` reintroduz o
    bug uma camada abaixo, onde ninguém procura.
14. **A fronteira da serialização é o único ponto onde a garantia vale para o
    payload inteiro.** `json.dumps` emite `NaN`/`Infinity`, que o `JSON.parse`
    do Node recusa — um campo não-finito torna ilegível uma resposta em que
    todo o resto estava certo. Use `json_seguro.dumps`. Cuidado com a
    assimetria do numpy: `np.float64` **é** subclasse de `float` (por isso a
    limpeza de NaN funcionou sem ninguém pensar em numpy), mas `np.bool_`
    **não é** `bool`, nem `np.int64` é `int`. Basta uma comparação sobre valor
    do pandas (`preco <= -6`) para derrubar o payload.
15. **Barra do dia corrente não é pregão fechado, e `sem_barra_incompleta` não
    pega isso** — ela descarta `Close` vazio, e barra intradiária tem `Close`,
    só que provisório. Duas coletas do mesmo ticker com minutos de diferença
    deram `+1,03%` e `-9,33%` porque a bolsa coreana tinha aberto no meio. Para
    comparar "o último pregão", confirme que a praça fechou (ver
    `_sessao_ainda_aberta` em `macro_risk_snapshot.py`). E lembre que o mesmo
    dado pode chegar por dois caminhos: o conserto nas AÇÕES não alcançou o
    índice, que vem do snapshot global.
16. **Teste não pode embutir número de mercado.** A sonda de qualidade cobrava
    o literal `225` — preço que ela mesma fabricava e que `analisar()`
    descartava ao rebuscar o fundamento. Passou por coincidência enquanto o
    mercado rondava aquele valor e reprovou quando ele andou, levando a dois
    "consertos" de prompt atrás de uma regressão que não existia. Teste que
    embute cotação vira oráculo de preço e manda investigar o lugar errado.
17. **Fonte externa velha é pior que fonte ausente**, porque parece medição. O
    WTI vinha do FRED com **sete dias** de atraso e o sinal de choque
    geopolítico comparava a semana anterior achando que era o dia. Faça as
    datas viajarem no payload e recuse observação acima de um limite de idade.
18. **Erro que não nomeia quem foi PULADO manda investigar o lugar errado.** A
    Análise com IA falhou com `condenados nesta run: openrouter | openai |
    kimi` — três contas quebradas, e a leitura natural é "tentei tudo que
    tenho". Não era: a cadeia tem cinco, e os dois primeiros não apareciam.
    Havia DOIS jeitos de sumir (filtrado por falta de chave; tentado e falhado
    sem condenação) e o erro não distinguia nenhum. A regra: quem não respondeu
    entra no erro **com o motivo**, e "quebrou" fica separado de "nem foi
    tentado" — apontam para investigações diferentes.
19. **Teto de tempo se dimensiona pela DURAÇÃO MEDIDA, não por estimativa.** O
    teto por chamada era 55s sobre a suposição de "~40s típico". O `agent_runs`
    dizia outra coisa: 33,5 / 57,7 / 63,9 / 65,0 / 70,0. Três das cinco
    passavam de 55s — o teto cortava trabalho NORMAL, não cauda, e `failed
    after 55.1s` era o nosso relógio batendo, não o provedor falhando. A
    primeira correção (75s) ainda ficou 15% acima do pico observado, o que não
    é teto: é sorteio com viés bom.
20. **O livro de gastos registra CHAMADAS DE API, não entregas de texto.**
    Cache por TTL e carona do coalescer devolvem o `usage` junto (a tela mostra
    o custo, e isso está certo), e a rota lançava uma linha nova a cada
    entrega: duas linhas de US$ 0,062852 terminando no MESMO instante para uma
    chamada só. Quem sabe a resposta é a closure que o coalescer executa —
    quem pega carona nunca roda a dela.
21. **Comentário viaja junto com a constante que ele explica.** Ao subir o teto
    da rota, o comentário de `IDADE_MAX_CARONA_MS` ficou citando os 150s
    antigos; ao mover `MAX_TOKENS` para módulo próprio, quase deixei para trás
    o histórico de por que ele é 6000. Comentário que descreve um número errado
    é pior que comentário nenhum — alguém confere a conta contra ele.

---

## Operação

```bash
# Deploy (VPS)
cd /opt/premercado && git pull origin main && docker compose up -d --build

# Qualidade — os MESMOS comandos que a CI roda (.github/workflows/ci.yml)
pnpm run typecheck
pnpm -r --if-present run test -- --run    # vitest de TODOS os pacotes
pytest                                     # coleta via pytest.ini, sem exclusão

# Risco macro (dentro do container, a partir de /opt/premercado)
#
# Roda como MÓDULO, nunca por caminho -- ver convenção 11.
docker compose exec -T -w /app/artifacts/api-server/src \
  app /app/.venv/bin/python3 -m agent.macro_risk_snapshot 2>&1 | tail -30
# O stderr resume: "cobertura X% | score Y | N fonte(s) com erro".
# Fonte que falhou aparece nomeada em coleta.erros, com o motivo.

# A série gravada
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A' <<'SQL'
select snapshot_date, coverage_pct, aggregate_score, active_flags
from macro_risk_snapshots order by snapshot_date desc limit 10;
SQL

# Sonda de qualidade do prompt -- NÃO roda no CI (custa dinheiro e depende de
# rede). Rodar depois de mexer no SYSTEM da Análise com IA. ~US$ 0,15.
for p in anthropic gemini; do
  docker compose exec -T -w /app/artifacts/api-server/src -e AGENT_PROVIDER_ORDER=$p \
    app /app/.venv/bin/python3 -m agent.sonda_qualidade 2>&1 | tail -6
done

# Ferramentas do radar (dentro do container, a partir de /opt/premercado)
cd /opt/premercado
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.atualizar_correlacoes < /dev/null
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.atualizar_earnings < /dev/null
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.parametros_volatilidade < /dev/null
docker compose exec -T -w /app/artifacts/api-server/src app \
  /app/.venv/bin/python -m agent.earnings_reaction_analysis --tickers NVDA < /dev/null
```

> O `cd /opt/premercado` não é decoração: `docker compose` procura o
> `docker-compose.yml` no diretório atual e, rodado do `~` do root, falha com
> `no configuration file provided: not found` — que parece container fora do ar
> e não é.

> `< /dev/null` é necessário: os scripts detectam "chamado pelo Node" testando
> `stdin.isatty()`, e `docker compose exec -T` deixa o stdin aberto sem TTY —
> sem o redirecionamento, o script espera um EOF que nunca chega.

> Um `--build` apaga `/var/cache/premercado`, onde moram os overlays de
> correlações e de earnings. Os checkers regravam sozinhos (7 dias e 24 h);
> para não esperar, rodar os dois comandos acima.

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
| #345, #355 | Calendário de earnings deixa de ser digitado: overlay diário da Alpha Vantage. Quatro datas estavam erradas na transcrição — BABA por 15 dias, na véspera do próprio catalisador |

### Backtest e exportação (ago/2026)

| PR | O que entregou |
|---|---|
| #273 | Walk-forward com validação out-of-sample: otimiza no treino, mede na janela seguinte |
| #275 | Campos de janela (treino/teste/objetivo) na tela — a rota já aceitava, a tela não enviava |
| #276 | Salvar relatório e enviar por e-mail nas oito telas de análise |
| #277 | Tabela do e-mail vira `<table>` com estilo inline (Gmail no celular ignora o `<style>`) |
| #278 | Rótulo para todo grupo do Radar, com teste que lê o Python e pega a deriva |
| #344 | Parâmetro impossível no ConfluenceEngine falha alto: `min_votes=6` com 5 sinais deixava o motor mudo em `flat` |
| #358 | Diagnóstico do ConfluenceEngine: separa sinal de sizing (priors placeholder davam trades a 6% do capital), decompõe por direção, exposição e captura |
| #359 | Long-only por padrão no `run_backtest` — shorts perderam em 6/6 células ticker × regime; e o veredito do diagnóstico no Diário |
| #362 | Execução honesta nos dois motores: sinal de D executa na abertura de D+1 (era no próprio close de D — look-ahead) e stop/target checam High/Low com política conservadora |
| #363 | A régua ganha estatística: Sortino, Calmar, profit factor, expectancy e IC 95% por bootstrap na tela; embargo de 5 pregões no walk-forward; recalibração de Kelly exige 30 trades |

### Diversificação de fontes de dado (ago/2026)

| PR | O que entregou |
|---|---|
| #279 | Cadeia de fallback (yfinance → cache → cache vencido → fonte externa), disjuntor por provedor, preflight de deploy |
| #280 | Stooq descartado (anti-bot) e trocado por Alpha Vantage, com teto diário de cota |
| #281 | Teto de cota que não contava: dia gravado do relógio vs. dia por parâmetro |
| #282 | `get_quotes.py` ligado à cadeia (fallback por lote) + `isDelayed` no contrato e na UI |
| #283 | `market_alerts.py` ligado à cadeia (fallback por período) + alerta de dado degradado |
| #284 | `get_technicals.py` na cadeia sem fonte externa (série ajustada); `get_historical_price.py` excluído por escrito |
| #285 | `get_trend.py` na cadeia, com marcação de degradação propagada ao resultado |
| #286 | `entry_exit_study.py`: histórico na cadeia, preço atual continua ao vivo |
| #287 | `confluence_engine.py` na cadeia + `18mo` entra no conjunto cacheável |
| #288 | `risk_manager.py` na cadeia via `get_daily_closes_batch` (lote com fallback por ticker) |
| #289 | `get_scenario_params.py` na cadeia (último da fila) + sela a rede nos testes do risk_manager que derrubaram o CI do #288 |
| #290 | Tela Análise Rápida: tendência, técnica e níveis/reações de um ticker avulso (3 botões, sem SSH) + rota `/ticker-snapshot` |
| #291 | Tela Previsão de Vol: ciclo de volatilidade por ticker (COMPRIMIDA→GATILHO→EXPANSAO→DECAIMENTO), EWMA λ=0.94, banda de amanhã |
| #292 | Botão "Baixar .md" no ExportarRelatorio — download local do relatório, nas 10 telas de uma vez |
| #293 | Botão "Análise com IA" na Análise Rápida: números viram leitura em texto, com camada fundamental (alvos de analistas, DCF/múltiplos, manchetes) e custo visível |

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
| #354 | Interpretação com IA da CESTA — a leitura por ticker já existe por regra; o que a IA acrescenta é a comparação |
| #361 | Leitura de cesta estourava o body-parser: a tela mandava 107KB de eventos que o Python descarta; 413 virava 500 genérico no errorHandler |

### Segurança e infraestrutura

| PR | O que entregou |
|---|---|
| #257 | **Senha em texto puro no log** — `err.body` do body-parser era logado sem filtro |
| #248 | Admin-gating em rotas compartilhadas, rate limit de auth, CI |
| #250 | `internal.ts` derrubava a API inteira para tráfego via Caddy |
| #249, #246 | Stack Infra/Monitor (Netdata, Uptime Kuma, Dozzle) |
| #226, #231 | Empacotamento para rodar fora do Replit |
| #241 | CSP liberando TradingView + fallback do DeepSeek |

### Risco macro setorial (ago/2026)

| PR | O que entregou |
|---|---|
| #330 | Os seis sinais, com **"sem dado" como estado próprio** — antes, cegueira e calmaria davam a mesma saída e o Kelly ficava cheio |
| #331 | Coleta real reusando o que já existia (Kospi do snapshot global, FOMC do `MACRO_EVENTS`, léxico do `get_trend`) |
| #332 | Persistência em Postgres, rotas e a seção em `/macro`; conserta escalar do numpy no `json_seguro` |
| #333 | Sinal de earnings com fonte real; retrato agendado às 07:50 BRT |
| #334, #335 | Número exibido passa a bater com o veredito; petróleo sai de 7 dias de atraso; preço do provider sem ruído de float32 |
| #336–#338 | Spawn como módulo (3 fontes caíam em silêncio), sessão coreana em curso, e "hoje" em BRT — a data é a chave da série |

### Análise com IA — orçamento, diagnóstico e prompt (ago/2026)

| PR | O que entregou |
|---|---|
| #322 | `definir_orcamento` — a cadeia percorre provedores POR DENTRO de um `create()`, então quem tem o prazo precisa passá-lo para dentro |
| #324 | `timeout` nos DOIS clientes: o SDK da OpenAI tem default de 600s, então 5 de 6 provedores rodavam sem teto |
| #325 | O log para de creditar o tempo da cadeia inteira a quem respondeu |
| #326, #327 | Precedência no `_TIER_MAP` (troca de provedor rebaixava o modelo em silêncio) e ordem por tempo até resposta útil |
| #328, #329 | Sonda de qualidade do prompt + o furo do detector de divergência; a sonda deixa de reprovar por presença de palavra |
| #339–#342 | 17 regras do SYSTEM em 5 grupos (-27%); exclusão do deepseek, que gasta o teto raciocinando e entrega 0 chars; sonda deixa de embutir cotação |
| #346–#349 | Erro do fallback deixa de esconder provedor sem chave e falha sem condenação; teto por chamada 55→85s vindo das durações reais; gasto contado uma vez por chamada de API |
| #350 | Gemini abre a fila da Análise com IA — quem falha barato na frente deixa orçamento para o próximo |
| #351 | A objeção ao gemini não reproduz: 3/3 nos dois provedores, e a sonda que a produziu estava quebrada |
| #354 | Política de provedores e teto de tokens saem para módulos compartilhados — a segunda tela com IA seria a terceira cópia da sequência |

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

---

## Diário

Relato por dia, para quando a tabela de PRs não conta a história. Fica aqui o
que foi APRENDIDO — o que cada conserto entregou está nas tabelas acima, e as
regras generalizáveis viraram numeração em *Convenções*.

### 19/08/2026 — os erros que se escondiam

Onze PRs (#344–#355). O fio que liga quase todos: **o sistema falhava e não
dizia direito onde**.

**A cegueira do fallback, em três camadas.** A Análise com IA voltou `All
providers exhausted -- condenados nesta run: openrouter | openai | kimi`. Três
contas quebradas, e a leitura natural é "tentei tudo". A cadeia daquela tela
tem cinco. Anthropic e gemini não apareciam nem como condenados.

Investiguei e concluí que tinham sido filtrados por falta de chave. **Errado** —
o dump do ambiente mostrou as cinco presentes. A causa real: `_mortos` só recebe
falha PERMANENTE, então timeout e 503 caíam para o próximo provedor em silêncio.
Os dois caminhos produziam a MESMA mensagem enganosa, e os dois foram fechados
(#346, #347).

Vale registrar que o bloco que monta essa mensagem já tinha um comentário do
incidente de 03/08 dizendo que "sem isso o operador não sabe que a cadeia
INTEIRA está fora, nem por quê". Aquele conserto nomeou os condenados e parou um
passo antes dos nunca-tentados. Cada correção fechou um caminho e deixou o irmão
aberto — o padrão que mais se repete neste repo.

**O relógio era nosso, não do provedor.** Com a mensagem consertada, o log disse:
`anthropic failed after 55.1s` contra um teto de 55s. Não era falha, era corte —
e o corte queimava uma das duas tentativas que o orçamento compra, sem produzir
resposta nem diagnóstico. A primeira correção foi por estimativa e ficou
apertada; a segunda veio das durações medidas no `agent_runs` (#348, #349).

**Um custo contado duas vezes.** Olhando a mesma tabela para outra pergunta,
apareceram duas linhas com tokens e custo idênticos terminando no MESMO instante:
uma requisição pegando carona no coalescer, e a rota lançando gasto por entrega
de texto em vez de por chamada de API (#349).

**O dado mais perecível deixou de ser digitado.** O calendário de earnings do
Radar era transcrição humana, e quatro datas estavam erradas — BABA por 15 dias,
na véspera do próprio catalisador. Virou overlay diário da Alpha Vantage
(#345, #355). No dia seguinte o coletor pegou sozinho a antecipação da LI.

**Evidência produzida por instrumento quebrado.** A sonda de qualidade
registrava, como razão para desconfiar do gemini, que ele lia divergência de
preço como argumento de compra. Usei esse registro para recomendar cautela.
Remedido: 3/3 nos dois provedores — e a sonda que produziu a evidência original
estava quebrada, com o payload fabricado nunca chegando ao modelo. O texto que o
gemini escreveu naquele dia foi real; as condições documentadas, não (#351).

**Um parâmetro que calava o motor.** `min_votes=6` com 5 sinais votantes não é
raro: é impossível. O ConfluenceEngine não ficava conservador, ficava
permanentemente em `flat` — sem erro, sem sinal, sem nada que dissesse por quê
(#344).

**O que o backtest disse, e continua valendo.** Calibrando o Kelly com o
`backtest_confluence`: +1,61% da estratégia contra +771,47% de buy & hold na MU
em dois anos, e Kelly recalibrado em **zero** em 3 dos 6 casos. Kelly zero não é
sizing cauteloso — é a fórmula dizendo que não há edge para dimensionar. O
`trade_journal` está vazio, então não há trade real para calibrar em cima. Ficou
aberto até o dia seguinte — a entrada de 20/08 responde.

### 20/08/2026 — o diagnóstico responde, e quatro specs vão para o arquivo

O dia anterior terminou com uma pergunta ("por que +1,61% contra +771%?") e
uma hipótese minha — "o sinal tem edge, o sizing esmaga". O diagnóstico
(#358) respondeu, e a hipótese estava só meio certa.

**O sizing era real, mas secundário.** Os priors placeholder do Kelly davam
`size_frac=0,06`: cada trade do backtest arriscava 6% do capital, e o headline
comparava isso com buy & hold a 100% — réguas diferentes. Corrigida a régua,
porém, o sinal continuou ruim na maior parte das células:

| | B&H | 100% exp. | só longs | só shorts | % fora |
|---|---|---|---|---|---|
| MU 2y | +773% | +16,8% | +41,0% | −17,2% | 72% |
| AVGO 2y | +123% | −25,1% | +12,8% | −33,6% | 70% |
| MRVL 2y | +244% | −51,5% | **−24,6%** | −35,7% | 73% |
| MU 22-23 | −32% | **−37,8%** | −26,9% | −14,9% | 68% |
| AVGO 22-23 | +36% | −7,4% | +0,7% | −8,1% | 69% |
| MRVL 22-23 | −34% | −27,1% | +9,4% | −33,3% | 63% |

Três leituras: os **shorts perdem em 6 de 6** (inclusive no downcycle — o
motor entra vendido depois da fraqueza confirmada e apanha do repique); o
custo dominante é **ausência** (~71% dos pregões fora, a MU fez +437% nesses
dias, e dos 10 melhores dias dela o motor pegou 1); e a tese de projeto foi
**refutada nos dois regimes** — no lateral, onde a confluência devia brilhar,
a MU a 100% perdeu mais que o buy & hold. O caso MRVL 2y é pior que ausência
de edge: os dias de "buy" renderam −24,6% dentro de um rali de +244% —
seleção adversa.

**A consequência no código** foi uma só, a única que o dado sustenta sem
ressalva: `run_backtest` virou long-only por padrão (#359) — melhora as 6
células. O resto virou arquivamento: os três backlogs de sinais de regime
(R1–R9) e o spec de influência setorial recebidos no dia propõem modular
`kelly_final = kelly_base × modifiers`, e o diagnóstico mostrou que
`kelly_base` não tem o que modular. O critério foi combinado ANTES do
resultado — "longs a 100% claramente positivos" — e deu positivo em 1 célula
de 6. Dois agravantes documentados na análise: o repo já tinha um modificador
de Kelly completo e testado (`apply_macro_risk_modifier`) com **zero
chamadores** fora dos testes, e um dos specs citava como padrão de referência
um arquivo que não existe e uma cadeia de fallback (Stooq) removida do repo.

O que sobreviveu dos quatro documentos, reclassificado de sizing para
LEITURA: divergência setorial (R6) e regime do basket como informação de
tela, na família do risco macro das 07:50. Sem prazo — atrás de tudo que
opera de verdade.

### 20/08/2026 (parte 2) — um 413 fantasiado de 500, e a régua fica honesta

**O incidente da noite.** A tela Reação a Earnings quebrou com "Internal
server error" ao pedir a leitura de cesta — o log dizia a verdade:
`PayloadTooLargeError, expected:107650 limit:102400`. Três defeitos
encadeados (#361): a tela mandava os `results` inteiros (eventos e
trajetórias que o Python descarta na entrada) e o dado do dia cruzou os
~100KB do `express.json` global; o guarda de 256KB da rota era código morto
atrás desse limite; e o errorHandler transformava qualquer erro em 500
genérico. Consertos na ordem da causa: a tela passou a mandar só o que o
prompt usa (`payloadDaInterpretacao`, ~10KB, tamanho independe do número de
eventos), e erro de cliente já classificado (4xx + `expose`, contrato do
http-errors) agora responde com o próprio status — o teste do handler
reproduz o 413 com o parser real e corpo de 107KB, não um erro sintético.

**A auditoria externa que valeu.** Chegou uma auditoria técnica do repo —
a mais útil da série, porque as duas alegações críticas dela conferiram no
código: (1) o backtest executava o sinal do candle D **no próprio fechamento
de D** — look-ahead, nos dois motores (`_simulate` e o `run_backtest` da
confluência); (2) stop/take-profit checavam **só o Close** — stop tocado
intradia e devolvido não existia (limitação que o código até documentava em
comentário). O ponto que a auditoria não fechou e que muda a leitura dela:
os dois vieses eram **a favor** da estratégia, e mesmo com a régua generosa
o diagnóstico já tinha dado negativo. Corrigir só piora os números — o
veredito de arquivamento sai mais forte, não mais fraco.

**O que mudou.** Sinal de D executa na abertura de D+1 (o sinal do último
candle nunca executa — seria a ordem de amanhã); a busca traz OHLC e o
stop/target checam gap de abertura e toque intradia, com política
conservadora quando stop e target caem no mesmo candle (o OHLC não diz qual
veio primeiro; assumir o target inflaria o resultado exatamente nos dias
mais voláteis). O `run_backtest` da confluência ganhou slippage (5 bps por
lado, o mesmo default do `backtest.py`). Os números do diagnóstico de
ontem foram medidos com a régua velha — valem como TETO; re-rodar na VPS
com a régua honesta fica pendente e só pode confirmar o arquivamento.

**O que ficou da auditoria sem implementar.** Portfolio backtest, decision
engine determinístico antes do LLM, bootstrap/Monte Carlo: instrumentos para
validar um edge que primeiro precisa existir — arquivados junto com as
specs, mesma prateleira, mesmo critério. Uma direção anotada como válida
para o futuro: o Veredito devolver JSON estruturado e o texto virar
renderização disso (a crítica à fragilidade do regex sobre prosa é justa).

### 20/08/2026 (parte 3) — o roteiro do motor de pesquisa auditável, e a etapa 1

A auditoria externa pedia, no fundo, uma coisa: que o Premercado deixasse de
ser "agente de análise com validação" e ganhasse um **motor de pesquisa
auditável** — DADOS → QUANT → RISCO → DECISÃO → LLM só explicando. O roteiro
decidido, em ordem de valor por esforço e sempre sobre a premissa de que a
régua vem antes da estratégia:

1. **Estatística na régua** (#363, esta entrada) — feita.
2. **Auditor independente** (item 38 da auditoria): um segundo simulador,
   mínimo e burro de propósito, que replica trades/equity/métricas do motor
   principal e faz DIFF linha a linha. O motor não pode ser o único juiz de
   si mesmo.
3. **Backtest de carteira (modo B)**: capital único, exposição por setor,
   caixa — responde "o sistema melhora uma CARTEIRA?", que o modo
   $10k-por-ticker não responde.
4. **Veredito estruturado**: o LLM devolve JSON (`action`, `confidence`,
   `reason_codes`) e o texto vira renderização; o validador confere
   JSON ↔ texto em vez de regex sobre prosa. A maior mudança, por último —
   mexe no coração do Veredito.

O que a etapa 1 entregou: **Sortino, Calmar, profit factor, expectancy e
payoff** no `_simulate` e na tela do Backtest; **IC de 95% por bootstrap**
(2 000 reamostras, semente fixa — reproduzível é requisito de auditoria) do
composto dos trades e do win rate, com a leitura interpretada dizendo a
frase que importa: *IC cruzando o zero = resultado indistinguível de sorte
de sequência com esta amostra*. No walk-forward, **embargo de 5 pregões**
entre treino e teste (os últimos dias do treino compartilham janelas de
indicador com os primeiros do teste — otimizar neles e medir neles é
vazamento suave; 5 = a janela do cruzamento, o sinal mais lento). E a
recalibração de Kelly do grid agora **exige 30 trades** — Kelly calibrado de
amostra pequena é pior que o placeholder, porque transforma erro padrão em
tamanho de posição. De brinde, um bug antigo no relatório exportado: a taxa
de acerto era multiplicada por 100 duas vezes ("+5500.0%").

O que segue explicitamente fora, com o mesmo critério de sempre: pesos por
voto no Confluence e veto gradual de earnings (precisam de evidência que a
base ainda não deu), Monte Carlo/probability of ruin (instrumentos de um
edge que não existe ainda), e o pacote `quant_core` unificado (a duplicação
é deliberada — scripts standalone — e os testes de sincronia são a
mitigação; promover a refactor agora seria risco sem pergunta nova).
