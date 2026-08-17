# Relatório de Análise Profunda — Earnings 24–27/08/2026
**Premercado · Auditoria conduzida em 17/08/2026**
Fonte dos dados: `earnings_reaction_analysis.py` e `get_scenario_params.py` (container `premercado-app-1`, venv `/app/.venv`), 8 últimos earnings por ticker via yfinance. Cotações do dia via Alpha Vantage (17/08/2026).

---

## Sumário executivo

Cinco tickers selecionados do Radar IA 2026 para análise profunda: **PDD, XPEV, NVDA, MRVL e ULTA**. Cada um revelou um perfil estatístico distinto de reação a earnings. A semana de 24–27/08 concentra os cinco eventos, com a carteira (NVDA, MRVL, e SMCI por contágio) exposta nos dias 26–27.

| | PDD 24/08 BO | XPEV 24/08 BO | NVDA 26/08 AC | MRVL 27/08 AC | ULTA 27/08 AC |
|---|---|---|---|---|---|
| Reação típica (dia real) | **-8,2%**, 6/8 quedas | -2,4%, reversão D+1 | -2,8%, 6/8 quedas | +1,3%, binário | 50/50 (4×4) |
| Magnitude média | 10,3% | 6,7% | 3,8% | **13,4%** | 9,7% |
| Gap médio (defesa possível?) | -7,7% ✗ | ~0% ✓ | 2,3% ~ | **11,6% ✗✗** | 7,1% ✗ |
| Implícito vs. realizado | Barato (7,4% vs 10,3%) | Caro (10,6% vs 6,7%) | n/d | n/d | Justo (9,6% vs 9,7%) |
| Chegada (run-up 21 pregões) | Neutra (+3,3%) | Descontada (-9,8%) ✓ | Esticada (+11,0%, corr +0,88) ✓ | **Muito esticada (+24,2%)** | Neutra (+2,9%) |
| Veredito | Evitar / pós-evento | Reversão D+1 c/ stop | Gestão de risco da carteira | **Decisão de posição** | Assistir, não operar |

Legenda: BO = before open (reação no próprio pregão) · AC = after close (reação no pregão seguinte, D+1).

**Contexto setorial (momentum 90 dias, anualizado):** KWEB **-17,9%** vs. SMH **+106,6%**. Os pares de segunda (PDD/XPEV) nadam contra a maré setorial; NVDA/MRVL surfam vento de cauda forte — o que explica os run-ups esticados de ambos e, ao mesmo tempo, a barra alta de expectativa embutida.

---

## 1. PDD — earnings seg 24/08 (BO)

**Cotação em 17/08:** $86,94 (+2,54%) · Run-up: +3,3% (neutro) · Setor KWEB: momentum -17,9% a.a., beta 0,84
**Correção pós-auditoria (snapshot vivo):** mínima de 52s real = **$71,94** (o radar hardcoded dizia $87,11 — dado errado na origem). PDD está +20,8% acima do piso real, acima da SMA50 ($84,03) e bem abaixo da SMA200 ($101,76) — repique dentro de tendência de baixa, não "colado no suporte". Reforça o veredito de evitar o evento.

**Histórico das 8 reações (dia do anúncio):**
-10,4% (mai/26) · +4,6% (mar/26) · -1,9%* · -7,3% (nov/25) · +0,9% (ago/25) · -13,6% (mai/25) · +4,0% (mar/25) · -10,6% (nov/24) · -28,5% (ago/24)
*mar/26 D+1

**Números-chave:**
- Média de fechamento: **-8,16%** · magnitude média 10,3% · desvio 10,62 p.p.
- Placar: 6 quedas × 2 altas (as duas altas nos trimestres de março/Q4)
- Gap médio: **-7,68%** — o estrago vem na abertura (BO), sem stop possível
- Volume 4,1x a média · D+1 negativo em 7 de 8 casos (média ~-3%)
- Chegar descontado não inverte o viés: 4 casos, só 2 subiram, média -3,1%
- Referências: S1 $77,99 · S2 $68,75 · R1 $95,89

**Tríade de vols (semana):**
| PainelCenarios (12m) | Opções (implícita 13/08) | Realizado em earnings |
|---|---|---|
| ±4,7%, centro 0 | ±7,38% | 10,3%, **centro -8,2%** |

O modelo subestima o evento em mais da metade e erra o centro em 8 pontos. Vol implícita barata em magnitude, mas o edge histórico é direcional para baixo.

**Veredito:** Não atravessar o evento comprado em ação (gaps de -21% e -18% no retrovisor). Se operar, opções com viés de queda (put spread > straddle). O histórico favorece esperar o D+1: quem quis o papel pagou menos depois da poeira.

---

## 2. XPEV — earnings seg 24/08 (BO)

**Cotação em 17/08:** $12,20 (+4,27%) · Run-up: -9,8% (**descontado**) · Beta KWEB 1,07

**Histórico das 8 reações (dia do anúncio):**
-0,1% (mai/26) · -8,4% (mar/26) · -10,3% (nov/25) · +4,2% (ago/25) · +13,0% (mai/25) · -7,8% (mar/25) · -3,8% (nov/24) · -6,0% (ago/24)

**Números-chave:**
- Média -2,38% · magnitude 6,69% · desvio 7,83 p.p. · Placar 6×2 mas equilibrado em magnitude
- Gap médio **+0,09%** (magnitude 3,3%) — o movimento é intradiário, **stops funcionam**
- **Padrão de reversão D+1**: em 6 de 8 eventos o dia seguinte andou na contramão do anúncio (-8,4→+7,5 · -10,3→+0,9 · +13,0→-7,9 · -7,8→+5,1 · -6,0→+4,3)
- Esticado = punição certa: 3 casos esticados, 3 quedas (média -7,3%). Chegada atual descontada joga a favor
- Referências: S1 $11,38 (≈ mínima 52s $11,49 — confluência de piso) · S2 $10,43 · R1 $13,02

**Tríade de vols (semana):**
| PainelCenarios (12m) | Opções (implícita) | Realizado em earnings |
|---|---|---|
| ±7,7% | ±10,6% | 6,7%, centro -2,4% |

Aqui o modelo está calibrado (XPEV é volátil o ano todo); quem erra é o mercado de opções, cobrando ~60% a mais que o realizado. **Prêmio caro.**

**Veredito:** O mais operável dos cinco. Não pagar prêmio de opção. Playbook histórico: se o dia do anúncio exagerar, operar a reversão no D+1 com stop (range médio de 7% para trabalhar). Caudas estruturais (guerra de preços EV, risco ADR) permanecem fora da estatística.

---

## 3. NVDA — earnings qua 26/08 (AC) · POSIÇÃO EM CARTEIRA

**Preço no estudo:** $225,01 · Run-up: **+10,95% (esticado)** · Vol anual do modelo: 36,9%

**Histórico das 8 reações reais (D+1):**
-1,8% (mai/26) · -5,5% (fev/26) · -3,2% (nov/25) · -0,8% (ago/25) · +3,3% (mai/25) · -8,5% (fev/25) · +0,5% (nov/24) · -6,4% (ago/24)

**Números-chave:**
- **"Sell the news" crônico: 6 quedas em 8**, média ~-2,8%, mesmo em era de beats consecutivos
- Magnitude média 3,75% — mega-cap maduro; o tempo dos ±15% passou. Cenário típico ruim: -3 a -6%
- Gap médio 2,3% — contido
- **Correlação run-up × reação: +0,88** (única positiva das cinco). Momentum forte precedeu reação melhor; único caso esticado (mai/25, +22%) deu +3,3%; os dois descontados caíram (média -5,8%). Estado atual esticado joga a favor — mas n=7, correlação sugestiva, não conclusiva
- Referências: S1 $216,57 · S2 $208,13 · R1 $233,45

**Papel na semana: epicentro de contágio.** NVDA×SMCI 0,51 · NVDA×TSM 0,66. Um D+1 de -5% arrasta SMCI (beta 1,28, vol semanal 12,3%) amplificado, e o MRVL reporta no dia seguinte já reprecificado pelo guidance de datacenter do NVDA.

**Veredito:** Não é trade, é gestão de risco. Distribuição estimada do D+1: -6% a +3%, centro levemente negativo. A pergunta operacional: quanto da carteira manter exposta a um sell-the-news coordenado em 26–27/08, considerando a liquidez comprometida para outubro. O dado sugere correção contida, não crash — salvo surpresa de guidance.

---

## 4. MRVL — earnings qui 27/08 (AC) · POSIÇÃO EM CARTEIRA

**Preço no estudo:** $234,33 · Run-up: **+24,2% (muito esticado — o maior da lista)** · Vol anual do modelo: 79,3%

**Histórico das 8 reações reais (D+1):**
+3,1% (mai/26) · **+18,4%** (mar/26) · +7,9% (dez/25) · **-18,6%** (ago/25) · -5,6% (mai/25) · **-19,8%** (mar/25) · **+23,2%** (dez/24) · +9,2% (ago/24)

**Números-chave:**
- **O evento mais explosivo dos cinco**: magnitude média 13,4%, desvio 16 p.p., 4 dos 8 eventos com move >18%
- Placar 5 altas × 3 quedas — mas as quedas destroem: -19,8% e -18,6% em dois anos
- **Gap médio absoluto de 11,6%** — o triplo do NVDA. Abriu -16,4% (ago/25), -17,8% (mar/25), +17,1% (dez/24). Zero defesa: o gap decide antes de qualquer stop
- Correlação run-up × reação +0,48. Caso análogo ao atual: dez/24 chegou +20,9% e deu +23,2%. Contraexemplo: mai/26 chegou +26,8% e saldo levemente negativo. Esticado = barra alta no guidance
- Amplificador: reporta um dia após o NVDA (correlações MRVL×AVGO 0,55 · ×MU 0,55 · ×ARM 0,56)
- Referências: S1 $202,95 · S2 $165,41 · R1 $265,71

**Tríade de vols (semana):**
| PainelCenarios (12m) | Opções (implícita) | Realizado em earnings |
|---|---|---|
| ±11,0% (volAnnual 79,2%) | n/d — coletar antes do dia 27 | 13,4%, centro +1,3%, caudas ±19-23% |

O modelo chega perto em magnitude (MRVL é volátil o ano todo), mas erra a **forma**: a distribuição real do evento é bimodal (ou +18/+23% ou -18/-20%, quase nada no meio), enquanto a lognormal concentra probabilidade justamente no centro que a história não frequenta.

**Validação de pipeline:** cálculo ao vivo (0,7918 / beta 1,4357) bateu com o registro do checker diário no banco (0,7928 / 1,4380) — ciclo diário íntegro para os tickers da carteira; a falha do AVGO/SKHY é isolada, não sistêmica.

**Veredito (informação, não recomendação):** Posição esticada +24% diante de evento que move ±13% e já gapou -19% duas vezes, com liquidez necessária em outubro. Alternativas clássicas: (a) atravessar inteiro aceitando a distribuição binária; (b) redução parcial antes do dia 26 (trava lucro do run-up, mantém participação); (c) put de proteção — provavelmente caro dado o realizado. O que o histórico desaconselha: decidir na manhã do dia 27 — o gap decide antes.

---

## 5. ULTA — earnings qui 27/08 (AC)

**Preço no estudo:** $493,33 (radar de 13/08 dizia $515,57 — caiu ~4% desde o snapshot) · Run-up: +2,9% (neutro)

**Histórico das 8 reações reais (D+1):**
-4,8% (jun/26) · **-14,2%** (mar/26) · +12,7% (dez/25) · -7,1% (ago/25) · +11,8% (mai/25) · +13,7% (mar/25) · +9,0% (dez/24) · -4,0% (ago/24)

**Números-chave:**
- **Placar 4×4 — moeda ao ar.** Sem viés direcional
- Magnitude média 9,66% vs. implícito 9,64% — **opções precificadas na mosca**; sem mispricing
- Movimento via gap do D+1 (±8-11% na abertura) — sem defesa, e o gap já entrega quase tudo (sem padrão de reversão como XPEV)
- Dia do anúncio fechou negativo em 7 de 8 e não previu nada — não serve de sinal
- As duas últimas reações negativas (radar ✓), mas o histórico longo é equilibrado — duas quedas não fazem tendência
- Confluência de suporte: mínima 52s $443,60 ≈ S1 $445,67 · S2 $392,15 · R1 $540,99

**Veredito:** O evento sem edge da lista. Probabilidade 50/50, vol justa, movimento via gap. Classificação: **assistir, não operar**. EVR 3,9 acerta em sinalizar evento grande; grande ≠ operável.

---

## Achados de engenharia (backlog para depois das análises)

1. **Radar IA 2026 com dados hardcoded** (`radar_ia_2026.py`): EVR, moves implícitos, preços e mínimas de 52s digitados à mão em ~13/08 (fonte OptionSlam). Gerou o dado impossível `PDD preco 84.5 < min52 87.11` — e o snapshot vivo confirmou depois: a mínima real do PDD é **$71,94**, ou seja, o valor hardcoded não só envelheceu, nasceu errado e contaminou a leitura de "papel colado no suporte". O radar não se atualiza sozinho.
2. **PainelCenarios sem ticker livre**: a UI só conhece os tickers da tabela `scenario_params` (populada pelo `scenario-params-checker.ts` a partir das posições ativas). Redesenho pós-investigação: o painel é por design um simulador da carteira; a ponte certa é um campo "Investigar ticker" que consome a rota já existente `GET /api/analysis/ticker-snapshot?ticker=X&benchmark=Y` (validada ao vivo com PDD/KWEB — retorna preço, 52s real, SMA50/200, vol, beta, momentum setorial). Zero backend novo.
3. **Modo earnings nos cenários** (refinado durante a auditoria): quando a janela contém um balanço, exibir as **três vols lado a lado** — modelo (12m), implícita das opções e realizada em earnings (`earnings_reaction_analysis`) — e sinalizar o desalinhamento. Implícita ≪ realizada = vol barata (caso PDD); implícita ≫ realizada = prêmio caro (caso XPEV). Vira um detector de mispricing de vol de earnings. A lognormal de 12m sozinha subestimou o PDD em >50%, errou o centro em 8 p.p., e no MRVL erra a forma bimodal da distribuição.
4. **AVGO e SKHY órfãos no checker diário** — ✅ RESOLVIDO em 17/08: AVGO estava com posição zerada (exclusão por design do filtro `isActivePosition`; linha seed deletada da tabela). SKHY tem só 27 pregões de história (IPO recente) e cruza o filtro `MIN_DAYS=30` em ~20/08, quando passa a atualizar sozinho. Fix estrutural pendente: o checker deletar/marcar stale as linhas de tickers que saíram da lista ativa.

**Infraestrutura mapeada na sessão:** app roda no container `premercado-app-1` (Docker Compose: app + caddy + postgres + dozzle + uptime-kuma) · Python do agente: `/app/.venv/bin/python` · scripts em `/app/artifacts/api-server/src`, invocados como `python -m agent.<script>` · `get_scenario_params` recebe argumentos CLI (`TICKERS BENCHMARK`), não stdin · `earnings_reaction_analysis` aceita `--tickers`, `--lookback`, `--json` · rota on-demand `/api/analysis/ticker-snapshot` já existe com cache 60s e fila exclusiva.

---

## Ressalvas

Amostras de 8 eventos por ticker: estatística sugestiva, não conclusiva. Earnings passados não determinam o próximo resultado. Caudas estruturais (guerra de preços EV, tarifas Temu, risco ADR, guidance surpresa) não aparecem em nenhuma distribuição histórica. Este relatório organiza probabilidades e referências de preço para apoiar decisões — não constitui recomendação de investimento; decisões de posição, tamanho e proteção são do titular da carteira, considerando a necessidade de liquidez de outubro/2026.
