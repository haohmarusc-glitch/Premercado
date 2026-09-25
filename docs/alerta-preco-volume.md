# Alerta composto (preço + volume relativo) e "Monitorar" a partir do Chat

Projeto: **Premercado** — página `/alerts` (Alertas de Preço) e `/chat`.

Estado desta especificação: **partes 1 e 2 entregues**, 3 a 5 pendentes (ver
"Onde isto está" no fim).

## Contexto

O alerta aceitava **um** indicador (Preço/Variação, RSI(14), MACD, SMA20,
SMA50) com condição "sobe acima / cai abaixo". Não existia condição de
**volume**, nem combinação de condições.

O agente do chat gera recomendações assim:

> **AVGO** — Ação: Monitorar confirmação acima de **$365-370** com volume
> **>1.2x**. Por enquanto, segure a posição mas sem adicionar.

Isso precisa virar um alerta automático: **preço > $365 E RVOL > 1.2**.

---

## 1. RVOL (volume relativo)

`RVOL = volume da sessão de hoje / (mediana de volume de 20 pregões × fração
do pregão decorrida)`.

**A fórmula NÃO é reimplementada no avaliador.** Ela mora em
`agent/volume_intradiario.py::medida_do_rvol` e é a mesma que alimenta a
Análise Rápida. O motivo é um incidente: a conta já esteve duplicada em
`tools.py` e `get_technicals.py`, quebrou nas duas ao mesmo tempo (NVDA,
26/08/2026 — rvol 8,89 com o real em 0,78) e a duplicação era "documentada como
segura" por um teste que nunca foi escrito. Uma fonte, e os números da tela e do
alerta batem por construção.

### Qual dos dois números da tela é o RVOL

A Análise Rápida mostra dois números que pareciam medir a mesma coisa e
discordar (AVGO: `RVOL 0.84` e `Volume vs média 0.81x`). Eles medem coisas
diferentes:

| campo | o que é | ajustado ao horário? |
| --- | --- | --- |
| `rvol` | volume da sessão de **hoje** contra o esperado para este ponto da sessão | **sim** |
| `volumeRatio` | média dos volumes dos **5 pregões fechados** sobre a mediana de 20 | não, e não inclui hoje |

O alerta usa `rvol`. O rótulo da tela virou **"Vol 5d / mediana 20d"** para o
segundo, porque "Volume vs média" ao lado de "RVOL" sugeria comparação entre
duas medidas do mesmo fato.

### Quando o RVOL não vale

Indefinido **nunca** dispara alerta, e num alerta com E (preço **e** RVOL) um
RVOL indefinido torna o alerta **inteiro** falso — ele não pode desistir da
condição de volume e disparar só pelo preço. Alertas que olham apenas o preço
não mudam.

| situação | `rvolSignal` | efeito |
| --- | --- | --- |
| menos de 30 min de pregão | `indefinido_abertura` | condição de RVOL não satisfeita |
| pré-mercado, feriado, sem base de volume | `indisponivel` | idem |
| barras de outro pregão (`rvolData ≠ hoje em ET`) | — | idem |
| pregão andado | `alto`/`normal`/`baixo` | avalia normalmente |

**Sem piso de 0,1 na fração.** A versão anterior desta especificação pedia esse
piso "para evitar ruído na abertura". Ele não resolve, e o motivo é aritmético:
às 9h40, com média de 20M, o piso põe o esperado em 2M — um spike de abertura
passa de 1,2x com folga e o alerta dispara em ruído do mesmo jeito. O que segura
é marcar o número como não conclusivo, não inventar um denominador.

**Pregão curto: 210 minutos, não 390.** Véspera de Natal, dia depois do
Thanksgiving e 3 de julho fecham às 13h ET. Dividir por 390 nesses dias infla o
rvol em 1,86x no fechamento — um dia de volume normal lido como "alto" em todos
os tickers ao mesmo tempo. As datas saem de **regra** (`pregao_curto`), não de
tabela por ano; tabela mantida à mão ao lado do código envelhece em silêncio. O
filtro de barras usa o fechamento do dia também, senão três horas de pós-mercado
entram como pregão.

Não coberto: fechamento antecipado de ocasião (luto nacional, falha da bolsa),
que é anunciado na hora e não tem regra.

**Recalculado a cada verificação.** O `rvol` sai de um spawn de
`get_technicals.py` por ciclo de 5 minutos; o frame intradiário (`period="1d",
interval="5m"`) não é cacheado. O cache em disco do provedor cobre só a série
diária, que é o insumo da mediana de 20.

### Na tela

RVOL não conclusivo não aparece como número em destaque: o lugar do valor diz
**"indisponível"** e o número desce para a nota
(`abertura — 5.81x não é conclusivo com menos de 30 min de pregão`). Ausência de
RVOL é travessão, nunca `0` — zero afirma volume nenhum, e o que se sabe é que
não há barra para medir.

## 2. Alerta com múltiplas condições (E)

- Um alerta passa a ter uma lista `conditions[]`; dispara só quando **todas**
  forem verdadeiras na mesma verificação. Lista vazia **não** dispara.
- Cada condição:
  `{ indicator: 'price'|'changePct'|'rsi14'|'macd'|'sma20'|'sma50'|'rvol', op: 'above'|'below', value: number }`.
- Migração: alertas existentes viram `conditions` com 1 item, sem mudar
  comportamento (`condicoesDoAlertaAntigo` preserva a precedência de
  `thresholdPrice` sobre `thresholdPct` e o corte inclusivo `>=`).
- Opção **"confirmar no fechamento"**: avaliar só após 16:00 ET com preço de
  fechamento e RVOL do dia inteiro. Para alertas de confirmação como o da AVGO é
  a opção mais confiável — o RVOL segue instável até ~10h30 ET mesmo depois dos
  30 minutos, e o guarda de abertura não cobre isso.
- Manter cooldown de 4h; adicionar opção **"disparar uma vez e desativar"**.

### UI (`/alerts`)

- Botão "+ Adicionar condição" (E).
- Campo opcional **Nota/Origem** (texto livre, ex.: "Chat 25/09 — confirmação de
  reversão").
- Lista de alertas mostra: condições, valores atuais de cada uma (ex.:
  `preço 351,05 / 365 ❌ · RVOL 0,89 / 1,2 ❌`), último disparo.

### E-mail

Assunto: `[Premercado] AVGO — confirmação: preço 366,20 > 365 e RVOL 1,35x > 1,2x`
Corpo: condições atendidas com valores, nota/origem, link para
`/analise-rapida?t=AVGO`.

## 3. Botão "Monitorar" no Chat

- Quando a resposta do agente tiver uma linha `Ação: Monitorar ...`, mostrar
  botão **"Criar alerta"** abaixo da mensagem.
- Extrair ticker, preço (limite inferior da faixa, ex.: 365 de "$365-370") e
  volume (1.2 de ">1.2x").
- Preferível: pedir ao agente um bloco estruturado (JSON oculto) além do texto:
  `{"monitor": {"ticker":"AVGO","conditions":[{"indicator":"price","op":"above","value":365},{"indicator":"rvol","op":"above","value":1.2}],"note":"..."}}`
  Fallback com regex se não houver JSON.
- O botão abre o formulário de `/alerts` **pré-preenchido** para revisar e
  confirmar (não criar sem confirmação).

## Critérios de aceite

- [x] RVOL ajustado ao horário: 12h00 ET → fração 150/390 = 0,385; volume 9M,
      média 20M → 9 ÷ (20 × 0,385) ≈ 1,17x.
      (`test_volume_intradiario.py::test_meio_dia_com_a_aritmetica_da_especificacao`)
- [x] **Às 9h40 ET o RVOL é indefinido, e um alerta de preço + RVOL não dispara
      mesmo com o preço acima do alvo.** Substitui o critério do piso de 0,1, que
      deixaria o spike de abertura passar — o próprio teste registra a conta.
      (`test_as_9h40_um_alerta_de_preco_mais_rvol_nao_dispara` +
      `alert-conditions.test.ts::"preço acima do alvo + RVOL indefinido"`)
- [x] Pregão curto: às 13h de uma véspera de Natal a fração vale 1,0 e o rvol
      converge para volume/mediana, não 1,86x.
      (`test_o_rvol_do_fim_de_um_pregao_curto_e_1x_e_nao_1_86x`)
- [x] Pré-mercado não produz RVOL. (`test_pre_mercado_nao_produz_rvol`)
- [x] RVOL de outro pregão não satisfaz condição.
      (`alert-conditions.test.ts::"RVOL de outro pregão"`)
- [x] Avaliador: preço 366 + RVOL 1,3 → dispara; preço 366 + RVOL 0,9 → não;
      preço 351 + RVOL 1,5 → não. (`alert-conditions.test.ts`)
- [x] Alertas de preço puro não mudam de comportamento.
      (`condicoesDoAlertaAntigo`, e o caso "alerta que olha SÓ o preço não muda")
- [ ] Schema `conditions` + migração dos alertas existentes.
- [ ] Checker avaliando condições múltiplas e e-mail no formato acima.
- [ ] UI de `/alerts` com "+ Adicionar condição" e Nota/Origem.
- [ ] Botão no chat pré-preenche AVGO / preço > 365 / RVOL > 1,2 a partir da
      resposta de 25/09/2026.

## Onde isto está

| parte | arquivos | estado |
| --- | --- | --- |
| 1. avaliador de condições | `api-server/src/lib/alert-conditions.ts` | entregue |
| 2. fonte do RVOL | `agent/volume_intradiario.py`, `get_technicals.py`, `tools.py`, `lib/timezone.ts`, `premarket/src/lib/indicators.ts` | entregue |
| 3. schema + migração | `lib/db/src/schema/premarket.ts`, `lib/api-spec/openapi.yaml`, `lib/api-zod`, `lib/api-client-react` | pendente |
| 4. checker + e-mail | `api-server/src/lib/alert-checker.ts`, `lib/mailer.ts` | pendente |
| 5. UI + botão do chat | `premarket/src/pages/alerts.tsx`, `pages/chat.tsx` | pendente |

Nada da parte 1 ou 2 mudou o comportamento dos alertas em produção: o avaliador
novo ainda não é chamado por ninguém. O que mudou de fato é o RVOL — pregão
curto, sinal de uma fonte só, e a apresentação na Análise Rápida.
