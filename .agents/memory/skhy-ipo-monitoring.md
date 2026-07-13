---
name: SKHY IPO — sem histórico de candles
description: SK Hynix (SKHY) estreou em 10/jul/2026 sem histórico de preços; SMA/RSI/MACD retornam null até haver dados suficientes
---

# SKHY (SK Hynix) — fase de descoberta de preço pós-IPO

## Contexto

- IPO/estreia: 10/jul/2026, ticker temporário SKHYV até 13/jul/2026 (regular trading passa a ser sob SKHY)
- Preço de oferta do ADR: $149; abriu a $170, fechou o 1º dia em $168,01 (+13%). Range intraday real do dia 1: **mínima $166,19 / máxima $177,00** — nunca testou o preço de oferta ($149) intraday.
- Liquidação do ADR: 14/jul/2026. Listagem das ações ordinárias na KOSPI: 29/jul/2026 (mesmo dia dos resultados do Q2 2026)
- Cada ADR representa fração da ação coreana (~1:10). HSBC espera um prêmio permanente do ADR de **~20% sobre a ação doméstica coreana** (000660.KS) — útil pra calibrar se o preço "faz sentido" vs. o ativo original.
- Market share de HBM confirmado no filing da SEC: **56,4%** (não os 58-62% estimados antes — esse número é mais preciso, direto do documento oficial)
- Opções sobre SKHY começam a negociar **terça 14/jul/2026** (2 dias úteis após o debut de sexta 10/jul) — nenhuma opção disponível no dia 1 nem no dia 2 (13/jul)

## Por que os indicadores técnicos ficam null

sma20/sma50/rsi/macd exigem 14-20 períodos mínimos de histórico. Sem candles anteriores ao IPO, essas funções simplesmente retornam null (sem erro, sem sinal) até acumular dados suficientes — não trate isso como bug.

## Duas fases

1. **Fase 1 — range de descoberta (10/jul a ~22/jul/2026, ~5-8 pregões)**: sem indicadores confiáveis. Monitorar só por rompimento do range absoluto $149 (piso, preço de oferta) – $177 (teto, máxima do dia 1). Não abrir posição nova nesta fase; tratar como observação, não entrada.
2. **Fase 2 — pós ~22/jul/2026**: SMA20/RSI/MACD começam a ter base mínima de dados para gatilhos técnicos normais. Mesmo assim, evitar abrir posição nova em 28-29/jul/2026 (véspera/dia dos resultados do Q2 + listagem KOSPI simultânea) por risco de gap duplo.

## Catalisadores no calendário

- 29/jul/2026: resultados Q2 2026 (receita esperada ~82,46 tri won vs 52,58 tri no Q1) + listagem das ordinárias na KOSPI no mesmo dia
- **~4/ago/2026 (estimado): fim do quiet period** (~25 dias corridos após a precificação de 10/jul). Historicamente 76-87% das coberturas de analistas iniciadas nessa janela são "compra"/"compra forte" e podem mover o papel +5% a +10% — mas é viés estrutural conhecido (os próprios bancos coordenadores do IPO iniciam cobertura otimista), não confirmação técnica real. Ver seção de pesquisa abaixo.
- **Expiração do lock-up: confirmado em 90 dias a partir da data do prospecto** (Form 424B4 definitivo, não só o F-1 inicial): *"During a period of 90 days from the date of the prospectus (the 'restricted period')..."* — cobre a companhia e as "lock-up parties" (afiliadas/cornerstone investors). Como a data do prospecto coincide com a precificação (~10/jul/2026), a expiração cai em **~8/out/2026**. É quando Baillie Gifford, Coatue, Situational Awareness Partners e demais cornerstone investors ficam livres para vender — tende a criar pressão de venda antes/durante e, historicamente, um ponto de entrada melhor alguns meses após o IPO (depois que essa pressão já ocorreu). Fonte: [Form 424B4 — SK hynix Inc.](https://www.sec.gov/Archives/edgar/data/0002120882/000119312526299963/d32785d424b4.htm)
- dez/2026: possível inclusão no Nasdaq 100 (rebalanceamento, fluxo passivo)
- set/2027: elegibilidade para o índice SOX (exige 3 meses listado)

## Sinais de pré-mercado: KRX (000660.KS) e correlação com NVDA

- **A ação original negocia na Korea Exchange sob o ticker `000660.KS`** (fetchável via yfinance, mesma lib já usada no projeto — `yf.Ticker("000660.KS")`). O pregão coreano funciona enquanto os EUA dormem, então o movimento overnight de `000660.KS` tende a antecipar o gap de abertura do ADR na Nasdaq — vale checar o fechamento coreano mais recente antes de decidir sobre o gatilho de entrada do dia.
- **Correlação com NVDA**: SK Hynix detém 56,4% do mercado global de HBM (filing da SEC) (memória usada nos aceleradores de IA da Nvidia) — a companhia tende a negociar como "satélite" da NVDA. Reforça o que já está na seção de confirmação setorial abaixo: usar NVDA (além de MU/SMCI) como confirmação de fluxo antes de entrar, não só o gráfico isolado da SKHY.
- **Risco de "sell the news"**: a ação subiu fortemente na Coreia antes da listagem (dado de mercado: ~640% em 12 meses até o pico de 25/jun/2026, ~₩2.987.000; fechou em ~₩2.180.000 em 10/jul/2026 — já em correção desde o pico, consistente com a queda de 18% nas duas semanas pré-IPO já documentada no Contexto). A listagem nos EUA traz liquidez extra que pode ser justamente a janela que fundos asiáticos usam para realizar lucro, não necessariamente sinal de confiança no ADR.
- **Refinamento da regra de horário**: além de aguardar os 60-90 min iniciais (já documentado no plano de swing trade), **evitar especificamente ordens a mercado nos primeiros 15 minutos do pregão** — é quando a volatilidade de saída de posições (se houver realização de lucro dos fundos asiáticos) é mais agressiva. Usar ordem limitada mesmo depois da janela de observação.

## ETFs alavancados sobre SKHY — pelo menos 6 produtos, 3 emissoras, na 1ª semana

Pelo menos 6 ETFs alavancados/inversos sobre a ADR da SKHY estão sendo lançados por 3 emissoras diferentes na primeira semana pós-IPO — um volume de lançamentos incomum pra uma ação com 2-3 dias de histórico:

- **SKUU (2x long) / SKDD (-2x short)** — GraniteShares, estreiam **segunda 13/jul/2026**
- **SKHX (2x long) / SKHZ (1x short)** — Leverage Shares, estreiam **terça 14/jul/2026**
- Produtos adicionais de ProShares/Rex Shares na mesma semana (tickers ainda não confirmados)

**Por que isso importa pro day trade**: um estudo (ETF Stream) mediu que o rebalanceamento diário desses ETFs alavancados de ação única contribui com **14,0 pontos percentuais de volatilidade pra SK Hynix**, contra 7,9 pontos pra Samsung — a diferença é liquidez mais rasa da SK Hynix. Esse rebalanceamento se concentra tipicamente nos **últimos 15-40 minutos do pregão** (padrão conhecido de produtos como TQQQ/SQQQ, TSLL/TSLQ). Com 4-6 produtos desses estreando na mesma semana que a própria ação ainda está no dia 2-3 de negociação, é fluxo mecânico extra empilhado em cima de um book já raso e sem profundidade histórica.

**Regra prática**: zerar qualquer posição de day trade até **15h30-15h45 ET**, antes da janela de rebalanceamento — não segurar posição até o fechamento nos primeiros dias enquanto esses ETFs ainda não têm padrão conhecido de comportamento na SKHY especificamente. Também vale desconfiar de sinais de volume nos primeiros dias desses ETFs: parte do volume vem de criação/resgate de cotas e hedge inicial dos market makers, não de convicção direcional.

Fontes: [TradingKey — SK Hynix US Listing Sparks Leveraged ETF Boom](https://www.tradingkey.com/analysis/stocks/us-stocks/262022210-skhynix-samsung-mu-sdnk-tradingkey), [GraniteShares — SK Hynix Leveraged ETFs](https://graniteshares.com/sk-hynix-leveraged-etfs/), [ETF Stream — Leveraged single stock chipmaker ETFs create volatility feedback loop](https://www.etfstream.com/articles/leveraged-single-stock-chipmaker-etfs-mechanically-create-market-volatility)

## Risco de desvio de capital: MU → SKHY

Parte do capital institucional alocado no setor de memória pode migrar de MU pra SKHY agora que ela lista direto nos EUA (fluxo antes represado por acesso só via ADR de balcão ou ação coreana). Isso pode **enfraquecer ou até inverter** a correlação MU-SKHY que o plano de swing trade usa como confirmação setorial — vale checar se MU passa a andar *contra* SKHY em vez de junto, especialmente nas primeiras semanas, antes de confiar cegamente em MU como sinal de confirmação.

## Day trade — padrões históricos de IPO aplicados à SKHY

Pesquisa sobre os primeiros dias de negociação de IPOs em geral:

- **Preço de abertura costuma ser mais forte nos dias 1 e 2**, com declínio consistente só a partir do dia 3 — o breakeven médio contra o preço de oferta leva ~11 dias. Ou seja, o dia 2 (13/jul) ainda está, estatisticamente, dentro da janela "forte" do IPO, não da fase de enfraquecimento típica.
- **Flippers (quem vendeu no 1º dia) respondem por só ~15% do volume** — menos do que o senso comum sugere; a maior parte do volume vem de reposicionamento normal de mercado.
- Estrutura recomendada pra day trade num ativo assim (metodologia ORB — Opening Range Breakout, padrão de mercado, não específico da SKHY): definir o range dos primeiros 15-30 min do pregão, não operar dentro dessa janela, entrar só no rompimento do range com volume ≥1,5x a média do próprio range e alinhado com o VWAP intradiário, stop no lado oposto do range.

Fontes: [Who trades IPOs? A close look at the first days of trading](https://www.sciencedirect.com/science/article/abs/pii/S0304405X05001200), [Post-IPO Flipping and Turnover](https://uweb.engr.arizona.edu/~boulat/pdf/IPOs.pdf), [Warrior Trading — Opening Range Breakout](https://www.warriortrading.com/opening-range-breakout/)

## O que a pesquisa acadêmica diz sobre estratégia de IPO (aplicado à SKHY)

Pesquisa de mercado (Renaissance Capital, Ritter/Bradley, Michaely & Womack) reforça a tese conservadora já documentada acima, com dados concretos:

- **Comprar no pop de abertura tem histórico ruim**: o pop do dia 1 (SKHY: +13%, ~+18,8% é a média histórica de IPOs) beneficia quase só quem recebeu ações no preço de oferta (cornerstone investors), não quem compra no mercado aberto. Ritter (1.526 IPOs, 1975-1984) mediu underperformance de ~27% em 3 anos vs. empresas comparáveis do mesmo setor/tamanho — ou seja, o retail que compra no pop historicamente compra no pior ponto relativo.
- **O rali do fim do quiet period é enviesado, não é sinal técnico**: 87% das primeiras recomendações de analistas no fim do quiet period são "compra"/"compra forte" (Michaely & Womack, 1999) — parcialmente porque os bancos que fizeram o underwriting do IPO iniciam cobertura via seus próprios analistas. Um rali nessa janela não deve ser tratado como confirmação de tese, só como ruído de fluxo esperado.
- **A melhor janela de entrada, segundo esses dados, costuma ser pós-lock-up** — o benchmark geral da pesquisa é 4-7 meses após o IPO (baseado em lock-ups típicos de 180 dias), mas a SKHY tem lock-up confirmado em só 90 dias (~8/out/2026), então essa janela de reavaliação chega mais cedo pra ela do que a média do mercado. Reforça a Fase 1 (observação) e sugere que, mesmo depois da Fase 2, vale reavaliar a tese com mais rigor perto de 8/out em vez de assumir que "mais dados técnicos" = "mais seguro".
- Recomendação recorrente da pesquisa, independente do ativo: **posição pequena, entender a mecânica de quiet period/lock-up, e dizer não com mais frequência do que sim** — já alinhado com o position sizing escalonado e os gates de calendário documentados no plano de swing trade abaixo.

Fontes: [Renaissance Capital — IPO Timing](https://ipopro.renaissancecapital.com/IPO-University/IPO-Timing), [LongYield — Should You Buy a Stock on IPO Day?](https://longyield.substack.com/p/should-you-buy-a-stock-on-ipo-day), [The IPO Quiet Period Revisited (Bradley)](https://site.warrington.ufl.edu/ritter/files/2016/01/The-IPO-Quiet-Period-Revisited-2004-02.pdf), [NBER — A Review of IPO Activity, Pricing, and Allocations](https://www.nber.org/system/files/working_papers/w8805/w8805.pdf)

## Adicionar SKHY a settings.tickers

SKHY é "foreign private issuer" — arquiva 20-F/F-6 em vez de 10-K na SEC EDGAR. CIK confirmado e já adicionado a `TICKER_TO_CIK` (`artifacts/api-server/src/agent/tools.py`): `"SKHY": "0002120882"` (fonte: F-6 da SK hynix Inc. em sec.gov/Archives/edgar/data/2120882/...). Com isso, `search_edgar_filings` já resolve o CIK certo — o único filing esperado por enquanto é o F-6 de registro do ADR, não um 20-F anual ainda. `get_fundamentals.py` (short interest/analyst ratings via yfinance) não depende de CIK, então já funcionava independente disso.

Para adicionar de fato o ticker: `settings.tickers` **não é uma tabela** — é uma coluna array de texto na linha única de `settingsTable` (`lib/db/src/schema/premarket.ts`). Não crie uma tabela `tickers`; edite via `PATCH /settings` (tela de Settings) ou a query `UPDATE settings SET tickers = array_append(tickers, 'SKHY')`.

## Alertas de preço sugeridos (via tela de Alerts / POST /alerts)

`alertsTable` só tem `symbol`, `indicator` (`price`/`rsi`/`macd`/`sma20`/`sma50`, minúsculo), `condition` (`above`/`below`, minúsculo), `thresholdPct`/`thresholdPrice`/`thresholdValue` e `notifyEmail` — **não existe campo de label/descrição livre**; o disparo aparece só como símbolo+condição+valor. Os 4 alertas de referência do plano de swing trade (ver seção acima), traduzidos pro schema real:

| symbol | indicator | condition | thresholdPrice | significado (não fica salvo, só pra referência) |
|---|---|---|---|---|
| SKHY | price | below | 149.00 | invalidação de tese — perda do preço de oferta |
| SKHY | price | above | 177.00 | rompimento da máxima do dia 1 |
| SKHY | price | below | 163.00 | zona de entrada primária (pullback) — confirmar volume manualmente |
| SKHY | price | above | 185.00 | alvo parcial — cenário bull |

Criar direto na tela de Alerts (ou `POST /alerts` com esse payload) é mais simples e seguro do que um script de seed: evita duplicar a lógica de resolução de `notifyEmail`/`userId` que a API já faz, e não depende de acesso direto ao Postgres de produção (que este ambiente de código não tem — só o Replit do usuário tem `DATABASE_URL`).

**Não existe tabela `tickerEvents`** no schema — o marcador de conversão SKHYV→SKHY em 13/jul/2026 fica só documentado aqui mesmo (seção "Contexto" acima); criar uma tabela nova só para esse marcador único seria escopo desnecessário.

## Plano de swing trade discutido (referência US$1.000, janela 2-4 semanas)

Este plano assume entrada logo na 2ª sessão (segunda-feira, 13/jul), o que **contraria** a recomendação da Fase 1 acima (esperar 5-8 pregões). É uma escolha consciente de tese — documentar aqui para não confundir com a tese de "só observar", que continua sendo a mais conservadora.

- **Checagem pré-abertura**: antes do gatilho, checar o fechamento mais recente de `000660.KS` (SK Hynix na Korea Exchange, ver seção acima) — o overnight coreano tende a antecipar o gap de abertura do ADR.
- **Gatilho de entrada**: defesa da VWAP intradiária ou rompimento da máxima da 1ª hora, após os primeiros 60-90 min de negociação, **usando ordem limitada** — evitar especificamente ordens a mercado nos primeiros 15 minutos do pregão (risco de volatilidade de saída de posições/realização de lucro dos fundos asiáticos, ver seção acima). `ALERT_INDICATORS` (`artifacts/api-server/src/lib/alert-indicators.ts`) só suporta `price`, `rsi`, `macd`, `sma20`, `sma50` — **VWAP não é automatizável pela tela de Alerts**; esse gatilho exige checagem manual.
- **Regra de invalidação**: se nenhum gatilho válido se formar até o fechamento de segunda, não forçar entrada — reavaliar terça.
- **Stop**: usar ATR curto (3-5 candles, proxy provisório) × 1,5-2, não percentual fixo arbitrário — o range do dia 1 ($149-$177) já sugere volatilidade real acima de 8%.
- **R:R travado antes de entrar**: fixar os dois números (ex.: stop -7% / alvo parcial +14% = 2:1), não faixas soltas.
- **Position sizing escalonado**: não alocar os US$1.000 inteiros no gatilho de segunda; entrar com ~50% e reservar o resto para confirmação num pullback/rompimento subsequente.
- **Confirmação setorial**: NVDA como referência principal (SK Hynix ~58-62% do mercado de HBM, negocia como satélite da NVDA), além de SMCI (lado da demanda) e MU/Micron (par mais direto do lado da oferta, mesma tese de memória/HBM, com histórico completo).
- **Gate obrigatório de calendário**: reduzir ou zerar a posição remanescente até o fechamento de 28/jul/2026, véspera dos resultados do Q2 + listagem KOSPI simultânea (29/jul) — gap risk duplo que o trailing stop por mínima de 2 pregões não cobre (gap ocorre fora do pregão).

## ConfluenceEngine — resultados reais do backtest (MU/AVGO/MRVL) e o que isso significa pra SKHY

`ConfluenceEngine` (`artifacts/api-server/src/agent/confluence_engine.py`, endpoint `POST /api/confluence`) **ainda não deve ser aplicado à SKHY** — histórico curto demais pra EMA50/Bollinger de 20 períodos estabilizarem (mesma razão da Fase 1 acima). Mas o backtest real rodado em MU/AVGO/MRVL (`scripts/backtest_confluence.py`) já deixou claro o que esperar quando a SKHY tiver histórico suficiente pra rodar isso:

- **Em regime de rali forte e sustentado (2024-2026, supercycle de HBM/IA), a estratégia perde feio pro buy & hold** — em retorno absoluto E em Sharpe. Ex.: MU teve 656,71% de buy&hold contra só 3,36% da estratégia; em AVGO e MRVL a estratégia chegou a perder dinheiro enquanto o ativo subia 138-228%. `min_votes` também não generalizou entre os 3 tickers (o melhor valor mudou de ativo pra ativo).
- **Em regime de correção/lateralização (memory chip downcycle de 2022-2023), o quadro muda**: pra MRVL, a estratégia (`min_votes=5`, com confirmação setorial) bateu o buy&hold nos dois critérios — retorno de +4,61% (pós recalibração de Kelly) contra -34,25% do buy&hold, Sharpe +0,743 contra -0,122, drawdown de -0,65% contra -60,99%. Pra MU, a estratégia não gerou lucro mas preservou capital muito melhor que o buy&hold (-1,17% e -1,44% de drawdown contra -32,16% e -49,63%). `min_votes=5` foi o melhor pra MRVL **nos dois regimes testados** — o único caso de consistência real entre regimes até agora.
- **Conclusão prática**: essa estratégia de confluência se comporta como um **filtro defensivo de redução de exposição em mercado sem direção clara**, não como gerador de alfa em tendência forte. Quando a SKHY tiver histórico suficiente pra habilitar o ConfluenceEngine, não esperar que ele "capture" um rali sustentado (ex.: um upgrade estrutural de tese por causa do HBM) — o valor real dela é evitar ficar exposto durante quedas/correções sem direção, e mesmo assim precisa ser validada regime a regime e ticker a ticker, não assumida como universal.

Ver PRs #41-#45 no repositório pro histórico completo dessa validação (código, testes com dados sintéticos, e os números reais rodados no Replit).

## Correlação mercados globais x gap de abertura da Nasdaq — resultados reais (PRs #52-#53)

`get_global_market_snapshot()` (`market_alerts.py`, tool do agente) busca a variação do último pregão de Nikkei 225, KOSPI, Hang Seng, DAX, FTSE 100, CAC 40, EUR/USD e futuros Nasdaq/S&P — dado bruto, sem pontuação/composite embutido. Antes de conectar isso a qualquer sinal, rodamos um estudo de correlação (`scripts/backtest_global_market_pulse.py`) contra o gap de abertura da Nasdaq (^IXIC), nos mesmos dois regimes do ConfluenceEngine (rali 2024-2026 vs. correção 2022-2023). Números reais (Replit):

- **Validação do método**: `NQ=F` (futuros Nasdaq) teve corr_day de 0,97/0,99 com ^IXIC — confirma que o alinhamento de datas entre mercados está correto; é um sanity check, não um achado novo.
- **Europa é o sinal mais forte**: DAX (corr_gap 0,48/0,60, hit-rate 66%/73%) e CAC 40 (0,48/0,57, hit-rate 67%/72%) lideram — mas é mais "leitura simultânea da manhã" que previsão overnight, já que a Europa está operando durante o próprio pré-mercado da Nasdaq.
- **Ásia tem sinal real, mais fraco**: Nikkei (corr_gap 0,42/0,15, hit-rate 62%/55%), KOSPI (0,32/0,20, hit-rate 61%/60%), Hang Seng (0,27/0,25, hit-rate 59%/60%) — mais fraco que a Europa, mas consistente nos dois regimes e sempre acima de 50%.
- **EUR/USD não tem sinal nenhum**: corr_gap -0,09/-0,04, hit-rate 49%/49% nos dois regimes (pior que cara-ou-coroa) — descartar essa variável de qualquer sinal futuro.
- **Ressalva antes de combinar**: DAX/FTSE/CAC provavelmente têm forte correlação entre si (mesmo "humor europeu"), então um composite com pesos fixos nas três infla o mesmo sinal em vez de agregar informação independente — checar multicolinearidade antes de somar.
- **Isso ainda é só correlação, não uma estratégia testada** — o próximo passo é testar uma regra de entrada/saída de verdade (usando o sinal europeu e/ou asiático) contra buy&hold, do mesmo jeito que foi feito com o ConfluenceEngine, antes de conectar isso a qualquer decisão de compra/venda real.

Ver PRs #52-#53 pro histórico completo (tool, script de correlação, números reais rodados no Replit).

## Backtest da regra de sinal global (Europa/Ásia) vs. buy&hold — resultados reais (PR #54)

`scripts/backtest_global_signal_strategy.py` converteu a correlação acima numa regra de verdade: posição long/flat ou long/short na Nasdaq (^IXIC) baseada no sinal do dia (Europa = média DAX+CAC+FTSE, Ásia = média Nikkei+KOSPI+HSI), com retorno medido abertura→fechamento do mesmo dia (não fechamento→fechamento, pra não reusar o próprio gap já testado na correlação). Números reais (Replit), nos mesmos dois regimes do ConfluenceEngine:

- **Europa é o achado real e consistente**: bate o Sharpe do buy&hold nos dois regimes, com drawdown bem menor (rali: -11,69% vs -24,32% do buy&hold; correção: -12,40% vs -35,49%). Na correção o resultado é dramático — long/flat +81,46% contra -14,16% do buy&hold; long/short chega a +199,70% (Sharpe 3,306). Win-rate ficou em 54-62,5% nos dois regimes, batendo com o hit-rate de 59-73% já visto no estudo de correlação — a correlação virou retorno de verdade.
- **Ásia NÃO se sustenta como retorno**, mesmo tendo tido correlação positiva com o gap: no rali, long/flat perde -9,01% (Sharpe -0,356) e long/short perde -26,64% (Sharpe -0,755); só ficou positiva (modesta) na correção. Interpretação provável: a Ásia acerta a direção do gap, mas o resto do pregão frequentemente reverte esse movimento — o sinal se esgota na abertura.
- **Combinar Europa+Ásia piora o resultado da Europa isolada** nos dois regimes (rali: -0,21%/-11,80% contra os 25-37% da Europa sozinha) — confirma a ressalva de multicolinearidade acima: **Ásia deveria sair de qualquer composite**, não é só redundante, é contraprodutiva no rali.
- **Long/short vs. long/flat depende do regime**: dispara na correção (mercado bidirecional, lucra nos dias de queda também), mas piora o drawdown no rali (-25,60% vs -11,69% do long/flat) sem ganho de Sharpe — brigar contra a tendência custa caro.
- **Ressalva crítica — nenhum custo de transação foi modelado** (comissão/slippage/spread). A estratégia opera quase todo dia (190-500 trades no período testado), exatamente onde custo por operação mais corrói o resultado. Antes de considerar isso validado pra uso real, precisa rodar de novo com custo realista aplicado a cada troca de posição (o `backtest.py` já tem `commissionPct`/`slippagePct` prontos pra isso).

**Atualização (PR #55) — resultado líquido de custo real (mesmos defaults do `backtest.py`: commission_pct=0,001, slippage_pct=0,0005 por perna, cobrado a cada troca de posição):**

- **No rali, o custo destrói a estratégia inteira** — Europa (o único sinal bom até aqui) vira negativa: long/flat de +25,05%/Sharpe 1,033 (bruto) pra -13,56%/Sharpe -0,570 (líquido); long/short de +37,21% pra -34,48%. Com 271-501 trades no período, um sinal com edge pequeno não sobrevive ao custo por operação num mercado que só sobe.
- **Na correção, a Europa sobrevive ao custo e continua batendo o buy&hold de forma decisiva**: long/flat +37,55%/Sharpe 1,357 líquido (bruto era +81,46%/2,456) contra -14,16%/Sharpe -0,220 do buy&hold; long/short +72,24%/Sharpe 1,695 líquido (bruto era +199,70%/3,306). O custo corta o retorno pela metade ou mais, mas o resultado continua real.
- **Ásia morre nos dois regimes depois do custo** — fica negativa até no regime de correção (-10,41% e -27,92%), onde antes (sem custo) parecia positiva. Confirma de vez: fora de qualquer versão dessa estratégia.
- **Combinado continua perdendo pra Europa isolada** mesmo líquido de custo (correção: combinado +26,07%/+42,62% contra +37,55%/+72,24% da Europa sozinha).
- **Conclusão prática — mesmo padrão do ConfluenceEngine, confirmado com um sinal independente**: essa estratégia (Europa isolada, sem Ásia) só sobrevive ao custo real como **filtro/gerador de alfa em mercado de correção/lateral**, não em rali forte — exatamente a mesma conclusão qualitativa da seção do ConfluenceEngine acima, agora replicada com um sinal completamente diferente (mercado europeu, não indicador técnico) e já líquido de custo. Não é coincidência de um teste isolado; reforça que "funciona só em correção" pode ser um padrão real do próprio ativo/mercado, não um artefato de uma estratégia específica.

Ver PR #54 pro histórico completo (código, teste de vazamento de look-ahead com dados sintéticos em múltiplas seeds, números reais rodados no Replit).

**Atualização (PR #57) — filtro de regime automático (SMA100) pra ligar/desligar a estratégia:**

`scripts/backtest_regime_switch_strategy.py` testou se dá pra detectar o regime automaticamente (Close do Nasdaq vs. SMA100 própria) em vez de rotular "rali" vs "correção" na mão, e usar isso pra alternar entre buy&hold passivo (em alta) e o sinal europeu (fora de alta). Números reais (Replit):

- **O filtro discrimina os regimes de verdade**: 78,4% dos dias classificados "alta" no regime de rali, contra só 36,9% no de correção — diferença grande o suficiente pra mostrar que a SMA100 capta algo real, não é ruído.
- **Na correção, o híbrido preserva quase todo o edge**: long/flat líquido 35,60% (vs. 35,15% do sinal sempre ligado); long/short líquido 69,22% (vs. 69,75% sempre ligado) — praticamente empatado, mesmo com mais trades por causa das trocas de regime.
- **No rali, o híbrido melhora mas NÃO resolve o problema**: long/flat líquido vai de -14,13% (sempre ligado) pra -8,01% (híbrido) — menos ruim, mas ainda muito longe dos +42,27% do buy&hold puro. Dois motivos: (1) os ~22% de dias "fora de alta" DENTRO do próprio rali ainda ativam o sinal europeu, que perde dinheiro nesse regime; (2) a própria troca de regime é uma operação com custo — o híbrido fez 447 trades contra 269 do sempre-ligado, então parte do que se ganharia ficando parado se perde na troca em si.
- **Conclusão prática**: a SMA100 é rápida/ruidosa demais — fica alternando "alta"/"correção" dentro do próprio rali, e cada troca custa dinheiro. Hipótese não testada ainda: um filtro mais "pegajoso" (SMA200, exigir vários dias seguidos abaixo da média, ou um gatilho de drawdown mínimo desde a máxima) provavelmente reduziria esses falsos positivos de "correção" durante um rali genuíno.

Ver PR #57 pro histórico completo (código, números reais rodados no Replit).

**Atualização (PR #60) — SMA200 testada lado a lado com SMA100: confirma a hipótese, e vem com bônus:**

`scripts/backtest_regime_switch_strategy.py` foi estendido pra testar SMA100 e SMA200 no mesmo run, testando a hipótese levantada acima (SMA mais lenta = menos trocas falsas de regime dentro do rali). Números reais (Replit):

- **No rali, SMA200 reduz o dano, confirmando a hipótese**: híbrido long/flat líquido melhora de -8,01% (SMA100) para **-5,00%** (SMA200), Sharpe de -0,224 para -0,095. Long/short: -24,44% → -19,01%. Ainda muito longe dos +42,27% do buy&hold, mas é uma melhora real e consistente.
- **Bônus não esperado — na correção, SMA200 supera tanto a SMA100 quanto o sinal sempre ligado**: híbrido long/flat líquido 41,94%/Sharpe 1,414 (vs. 35,60%/1,217 da SMA100 e 35,15%/1,276 do sinal sempre ligado); long/short 85,07%/Sharpe 1,889 — o melhor número registrado até agora nessa linha de teste, batendo os 69,75%/1,648 do sinal sempre ligado. Motivo: a SMA200 classificou só 29,4% dos dias como "alta" na correção (contra 36,9% da SMA100), mantendo o sinal europeu ligado por mais tempo justamente no regime em que ele funciona.
- **SMA200 domina SMA100 nos dois regimes e nos dois modos de posição** — não é uma troca de um lado pelo outro, é estritamente melhor em todas as 8 combinações testadas (2 regimes x 2 janelas x 2 modos).
- **Conclusão prática final desta linha de investigação**: mesmo com o melhor filtro testado até agora, a estratégia (Europa + regime) não resolve o rali — buy&hold puro continua ganhando disparado em tendência forte e sustentada. O pragmatismo real: usar buy&hold como default, e ligar o sinal europeu com filtro SMA200 só como camada adicional quando houver confirmação de regime de correção/lateralização — não como substituto do buy&hold em todos os cenários.

Ver PR #60 pro histórico completo (código, números reais rodados no Replit).
