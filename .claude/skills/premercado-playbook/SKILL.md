---
name: premercado-playbook
description: Conhecimento institucional do repo Premercado (agente "Pré-Mercado" de análise de bolsa) — armadilhas reais de integridade de dados, manias do yfinance, bugs de subprocesso/timeout, comportamento dos múltiplos provedores de LLM (fallback chain) e a armadilha do deploy pelo painel Publishing do Replit, todos aprendidos de incidentes reais em produção. Consulte esta skill SEMPRE que for depurar um bug de dado errado/inconsistente (preço, %, RSI, data), mexer em código que usa yfinance/fast_info, criar ou tocar num checker de fundo/subprocesso Python, trabalhar no loop do agente ou na cadeia de fallback entre provedores de LLM, mudar o schema do banco, ou revisar/aprovar um deploy pelo painel Publishing do Replit — mesmo que o usuário não mencione nada disso explicitamente, porque esses bugs tendem a reaparecer com uma roupagem levemente diferente da vez anterior.
---

# Premercado — Playbook de aprendizados em produção

Este repo tem uma cultura forte de documentar CAUSA RAIZ direto no código, com comentários
"visto em produção" espalhados por vários arquivos. Esta skill consolida esses aprendizados
num só lugar, organizados por padrão recorrente (não por incidente isolado), pra quem for
mexer numa área parecida reconhecer o padrão ANTES de reintroduzir o mesmo bug de outra forma.

Cada seção abaixo é um padrão que já se repetiu mais de uma vez neste repo, com o porquê e
ponteiros pro código real. Quando for tocar em código de uma dessas áreas, leia a seção
correspondente antes de escrever a correção.

## 1. Nunca confie num campo armazenado/derivado quando dá pra recalcular da fonte

O bug mais recorrente deste repo, em formas diferentes: um valor é armazenado/cacheado
separado da fonte de verdade, algum caminho de escrita esquece de atualizá-lo, e o valor
fica travado desatualizado pra sempre.

- `portfolio_positions.quantity` pode ser editado direto via `PUT /portfolio/:id` (pra
  correção manual), sem recalcular a partir dos lotes reais (`portfolio_purchases`). Uma
  posição com todos os lotes vendidos, mas com esse campo editado depois, nunca mais
  "desativa" sozinha. Aconteceu de verdade com MU: ficou aparecendo ativa no Painel de
  Cenários, na tela de Performance e podendo entrar na análise de carteira do agente, bem
  depois de já ter sido totalmente vendida. Corrigido em 3 lugares independentes
  (`lib/portfolio-math.ts::isPositionActiveFromLots`, `routes/scenarios.ts`,
  `routes/performance.ts`, `lib/runner.ts::getPortfolioTickers`) porque cada consumidor
  buscava os dados do seu próprio jeito — **se você adicionar um NOVO lugar que lê
  quantity/posições ativas, ele PRECISA filtrar pelos lotes reais, não confiar no campo
  armazenado.**
- Mesma lógica se aplica a "carteira" no chat: o agente de chat não tinha nenhuma lista
  própria do que é "a carteira" e respondia misturando cobertura geral com o que sobrava
  na memória — corrigido passando `getPortfolioTickers()` (a mesma fonte de verdade acima)
  pro subprocesso do chat.

**Heurística**: se um campo pode ser editado por dois caminhos diferentes (um automático
que recalcula, um manual que não), ele VAI divergir eventualmente. Prefira sempre recalcular
da fonte real no momento da leitura, ou pelo menos documente explicitamente por que não dá
(ex.: campo existe só pra correção manual de posições antigas sem lote registrado).

## 2. yfinance: fast_info e .history() são DUAS fontes que podem divergir

`fast_info.last_price`/`fast_info.previous_close` é uma cotação "rápida"/quase-live que às
vezes diverge do candle diário oficial devolvido por `.history()` — inclusive com o SINAL
trocado no `change_pct` resultante, não só uma diferença pequena de arredondamento.

Isso já causou variação diária errada simultaneamente em vários tickers do Veredito do Dia
(ex.: MRVL reportado -3,97% quando o dia real foi +2,32% — sinal trocado; SKHY -6,61%
informado vs -3,54% recalculado). Como o erro aparecia em vários tickers ao mesmo tempo, não
era coincidência de um ticker só — era sistemático, na fonte (`get_stock_data`,
`get_performance.py`, `get_quotes.py` liam `fast_info.previous_close` direto).

**Padrão de correção (já usado em `market_alerts.py::_gap_pct`, `tools.py::get_stock_data`,
`get_performance.py`, `get_quotes.py`, e no `agent.py::_fetch_veredito_quote` do validador
do Veredito)**:
```python
hist = ticker.history(period="5d")
prev_close = float(hist["Close"].iloc[-2])  # candle oficial, nao fast_info
```
`fast_info.last_price` continua sendo a melhor fonte pro preço "agora" (pré-mercado,
intradiário) — o problema é especificamente usar `fast_info.previous_close` como referência
pra calcular variação. Se você adicionar QUALQUER novo código que calcula `change_pct`/
variação percentual a partir do yfinance, use `.history()` pro fechamento anterior, não
`fast_info`.

Isso também motivou um validador dedicado pro Veredito do Dia (`agent/veredito_validator.py`):
`validate_snapshot()` recalcula os números ANTES do prompt e expõe fades intradiários como
fato verificado (o LLM não recalcula sozinho, evita erro); `lint_veredito()` confere o TEXTO
gerado depois (percentuais citados, incluindo claims qualitativos tipo "flat" sem número
nenhum, dia da semana, datas de earnings, "pós-earnings" fantasma) e dispara um retry único
de correção se achar erro. Se for adicionar um novo tipo de dado ao Veredito que o LLM pode
citar errado, considere se ele merece uma checagem aqui também.

## 3. Subprocesso Python com timeout do lado Node: o processo tem que morrer de verdade

`with ThreadPoolExecutor(...) as pool: ...` espera IMPLICITAMENTE todas as threads
terminarem no `__exit__` (`shutdown(wait=True)`) — mesmo que a aplicação já tenha
"desistido" de esperar uma chamada específica (ex.: um loop `as_completed` sem timeout).
Python não tem como matar uma thread de verdade, então o processo continua vivo até a
chamada de rede realmente terminar (ou até o timeout interno do próprio yfinance, ~30s por
request).

Isso já derrubou 4 checkers de fundo ao mesmo tempo (spike intradiário, bounce, squeeze,
carteira): todos bateram o teto do timeout do lado Node (60-120s) em TODO ciclo, porque
nenhuma camada interna tinha um orçamento de tempo mais curto que o timeout externo — o Node
só descobria matando o subprocesso à força, todo santo ciclo.

**Padrão de correção** (`agent/bounded_parallel.py`): `bounded_parallel_map()` roda em
paralelo mas devolve o que já completou dentro de um orçamento configurável (sempre MENOR
que o timeout do lado Node que chama o script), sem esperar o resto. `exit_now()` imprime o
resultado e sai via `os._exit()` — **testado e confirmado que sem isso o processo ainda
ficaria preso** esperando a thread travada via o atexit hook do `concurrent.futures.thread`,
mesmo já tendo desistido dela no nível da aplicação. Um `sys.exit()`/fim normal do
`__main__` NÃO resolve isso.

Se você for criar um novo script `get_*.py` que busca vários tickers em paralelo via
subprocess chamado do Node, use `bounded_parallel_map`/`exit_now` desde o início, com
orçamento sempre menor que o `setTimeout` do lado TS que o invoca.

## 4. Loop do agente e cadeia de fallback entre provedores de LLM

O agente suporta múltiplos provedores (Anthropic, OpenAI-compatível, Gemini, OpenRouter,
Kimi) com fallback automático. Modelos mais fracos/baratos da cadeia de fallback já
causaram, em produção:
- Sintaxe de tool-call "alucinada" como texto plano em vez de `tool_calls` estruturado
  (vazando texto de function-call cru pro relatório final).
- Abandono do fluxo multi-turno no meio (ex.: `gemini-2.5-flash-lite` completando as 12
  rodadas do relatório diário inteiro sem NUNCA chamar `save_observation`, gerando um
  relatório vazio mesmo com duas cobranças).
- Blocos `tool_use` órfãos quando `stop_reason` não é literalmente `"tool_use"` (ex.: a API
  crua devolve `max_tokens`/`pause_turn` com blocos de tool_use já completos) — a próxima
  chamada batia com 400 "tool_use ids were found without tool_result blocks" se esses blocos
  não fossem resolvidos com um `tool_result` mesmo assim.
- Retries do SDK empilhando com retries do loop externo multiplicavam tentativas (3×4=12),
  arriscando estourar o timeout do processo inteiro.

**Heurística**: ao adicionar um modelo novo à cadeia de fallback, ou ao mudar o loop
agêntico, assuma que o modelo pode: (a) não seguir o protocolo de tool-calling
perfeitamente, (b) parar no meio sem terminar o fluxo obrigatório, (c) devolver um
`stop_reason` que não é exatamente o esperado mesmo com tool_use presente. `agent.py`
já tem cobrança de `save_observation` faltante (com número mínimo esperado, não só
"zero") e checagem de relatório curto demais (reconhecimento de continuação em vez do
relatório de verdade) — se for adicionar um novo fluxo de relatório, reaproveite esse
padrão (`require_observations`/`min_observations`/checagem de tamanho mínimo) em vez de
confiar cegamente que o modelo vai terminar direito.

Ver `agent/provider.py` pra classificação de erro transiente (retry no mesmo provedor) vs
definitivo (cai pro próximo provedor) — quota/crédito esgotado NÃO é transiente.

## 5. Deadline suave do agente: a folga de segurança também pode estourar

`AGENT_SOFT_DEADLINE_MS` força um turno final sem ferramentas quando o tempo está
acabando, pra sempre emitir um relatório parcial em vez de deixar o processo ser morto
sem nunca produzir `REPORT:`. Mas essa própria "folga de segurança" já estourou: um run
morreu aos 30min02s mesmo com 2min de margem, porque o turno JÁ EM ANDAMENTO quando o
deadline disparou já tinha consumido parte da folga antes de conseguir fechar o relatório.
Se for mexer no cálculo de `SOFT_DEADLINE_TS`/margem, lembre que a margem precisa cobrir o
turno INTEIRO em andamento no momento em que o deadline é cruzado, não só o tempo depois
disso.

## 6. Timezone: Brasília é fixo UTC-3, mas o processo não é

`datetime.date.today()`/`new Date()` sozinhos usam o fuso do PROCESSO (UTC nos
containers), não o do usuário (BRT). Isso já fez o "dia" virar 3h cedo demais perto da
meia-noite BRT, e já causou desalinhamento de data pra usuários em fusos distantes (ex.:
UTC+14). Sempre use os helpers dedicados (`_today_brt_str()`/`_now_brt()` no Python,
`todayBRTDateString()` no TS) pra qualquer coisa que informe "hoje"/"agora" ao usuário ou
ao agente — nunca `new Date()`/`datetime.date.today()` cru nesses casos.

## 7. Postgres `numeric` chega como STRING via Drizzle

Mesmo com `.$type<number>()` no schema, colunas `numeric` do Postgres vêm como string do
banco através do Drizzle — código que faz `.toFixed()`/aritmética direto num valor lido do
banco sem `Number(...)` primeiro quebra silenciosamente (já matou e-mails de alerta de
verdade, sem erro visível no fluxo principal). Sempre `Number(row.campoNumeric)` antes de
usar um valor numérico vindo direto de uma query.

## 8. Deploy pelo painel "Publishing" do Replit: NUNCA aprovar um DROP sem checar

Esse painel já propôs (e uma vez chegou a APLICAR) uma migração destrutiva calculada
contra uma versão desatualizada do schema — derrubou `squeeze_alert_firings` e
`reports.user_id` de verdade, mesmo os dois existindo no `schema.ts` atual. Sem backup
self-service disponível, a recuperação foi manual (cruzando `created_at` com outra
tabela ainda intacta). Uma segunda ocorrência (colunas `sector_move_pct`/
`sector_move_updated_at`, mergeadas horas antes) foi pega a tempo por causa dessa regra.

**Regra permanente**: se o aviso "esta migração pode remover dados permanentemente" citar
uma tabela/coluna que existe no `schema.ts` atual (`git log` confirma quando foi
adicionada), NÃO aprovar sem investigar — o `ensure-schema.ts` (rodado no boot do
servidor) já recria a ESTRUTURA sozinho de qualquer forma, então esse deploy pelo painel
normalmente nem é necessário só pra uma mudança de schema chegar ao ar. Se o mesmo `DROP`
aparecer de novo depois de um redeploy fresco (commit certo, cache limpo), é bug
recorrente da própria ferramenta do Replit, não do nosso código — vale abrir chamado com o
suporte deles em vez de insistir.

## 9. Toda mudança de schema tem DOIS lugares pra atualizar

Este repo usa migrations versionadas (`lib/db/migrations/NNNN_*.sql`) E um bootstrap
idempotente que roda no boot (`artifacts/api-server/src/lib/ensure-schema.ts`, com
`CREATE TABLE IF NOT EXISTS`/`ALTER TABLE ADD COLUMN IF NOT EXISTS`) — os dois precisam
ficar em sincronia manualmente, não existe geração automática de um a partir do outro.
Ao adicionar uma coluna/tabela nova: (1) `lib/db/src/schema/*.ts` (schema TS/Drizzle), (2)
migration `.sql` numerada sequencialmente, (3) bloco correspondente em `ensure-schema.ts`.
Esquecer o (3) não quebra localmente (a migration já cria a coluna), mas quebra em
qualquer ambiente que dependa só do bootstrap de boot.

## 10. Os arquivos "generated" de api-zod/api-client-react são mantidos à mão

`lib/api-zod/src/generated/api.ts` e `lib/api-client-react/src/generated/api*.ts` têm nome
de gerado, mas não existe nenhum script de codegen funcional neste repo (`grep` por
"codegen"/"openapi-generator"/"orval" não acha nada real) — são editados manualmente.
Ao adicionar um campo novo numa resposta de API, editar TODOS: `lib/api-spec/openapi.yaml`
(fonte "de documentação"), os schemas zod em `api-zod/src/generated/api.ts`, e a interface
TS em `api-client-react/src/generated/api.schemas.ts`. Nenhum desses se atualiza sozinho a
partir do outro.

## 11. Workflow de git/PR deste repo

- A branch de desenvolvimento designada é reaproveitada entre tarefas — depois que uma PR
  dela é mergeada, a branch local FICA VELHA (ainda aponta pro commit pré-merge). Antes de
  começar um novo trabalho: `git fetch origin main && git checkout -B <branch> origin/main`
  (com qualquer trabalho novo guardado via `git stash` antes, e reaplicado depois) — nunca
  empilhar commits novos em cima de uma branch cuja PR anterior já foi mergeada.
- **Existe CI** (`.github/workflows/ci.yml`), disparada em PR pra `main` e em push na
  `main`. São dois jobs, ambos bloqueantes:
  - `Typecheck + Vitest (TS/JS)`: `pnpm install --frozen-lockfile` → `pnpm run typecheck`
    → `pnpm -r --if-present run test -- --run` (roda o vitest de TODOS os pacotes, não só
    o do api-server: `premarket` também).
  - `Pytest (agente Python)`: `pip install -r requirements.txt` → `pytest` puro, que pelo
    `pytest.ini` coleta `artifacts/api-server/src/__tests__` inteiro, **sem exclusão
    nenhuma**. Uma versão anterior desta seção mandava pular
    `test_backtest_confluencia.py` por erro de import pré-existente — isso já foi
    corrigido e a suíte passa completa (982 testes em ago/2026); não exclua nada.
  - Há ainda um passo `Preflight das fontes de dado`, `continue-on-error: true` de
    propósito: rede fora do ar no runner não diz nada sobre produção. Ele não reprova a
    PR, mas o log dele é o aviso de que o Yahoo mudou algo (olhe o `source_used`).
- A CI não substitui rodar localmente antes de abrir a PR — ela leva ~2min e só te conta
  o que você já poderia saber em 1min. Rode os mesmos três comandos (`pnpm run typecheck`,
  `pnpm -r --if-present run test -- --run`, `pytest`) e abra a PR já verde.
- PRs sempre em draft, com subscribe automático de atividade e check-in agendado
  (~1h) pra acompanhar merge/CI sem precisar de polling ativo. Se a PR for mergeada antes
  do check-in disparar, apague o trigger (`delete_trigger`) em vez de deixá-lo acordar a
  sessão à toa.
