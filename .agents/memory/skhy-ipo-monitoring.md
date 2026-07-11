---
name: SKHY IPO — sem histórico de candles
description: SK Hynix (SKHY) estreou em 10/jul/2026 sem histórico de preços; SMA/RSI/MACD retornam null até haver dados suficientes
---

# SKHY (SK Hynix) — fase de descoberta de preço pós-IPO

## Contexto

- IPO/estreia: 10/jul/2026, ticker temporário SKHYV até 13/jul/2026 (regular trading passa a ser sob SKHY)
- Preço de oferta do ADR: $149; fechou o 1º dia em $168,01 (+13%), máxima intraday ~$177
- Liquidação do ADR: 14/jul/2026. Listagem das ações ordinárias na KOSPI: 29/jul/2026 (mesmo dia dos resultados do Q2 2026)
- Cada ADR representa fração da ação coreana (~1:10)

## Por que os indicadores técnicos ficam null

sma20/sma50/rsi/macd exigem 14-20 períodos mínimos de histórico. Sem candles anteriores ao IPO, essas funções simplesmente retornam null (sem erro, sem sinal) até acumular dados suficientes — não trate isso como bug.

## Duas fases

1. **Fase 1 — range de descoberta (10/jul a ~22/jul/2026, ~5-8 pregões)**: sem indicadores confiáveis. Monitorar só por rompimento do range absoluto $149 (piso, preço de oferta) – $177 (teto, máxima do dia 1). Não abrir posição nova nesta fase; tratar como observação, não entrada.
2. **Fase 2 — pós ~22/jul/2026**: SMA20/RSI/MACD começam a ter base mínima de dados para gatilhos técnicos normais. Mesmo assim, evitar abrir posição nova em 28-29/jul/2026 (véspera/dia dos resultados do Q2 + listagem KOSPI simultânea) por risco de gap duplo.

## Catalisadores no calendário

- 29/jul/2026: resultados Q2 2026 (receita esperada ~82,46 tri won vs 52,58 tri no Q1) + listagem das ordinárias na KOSPI no mesmo dia
- **~4/ago/2026 (estimado): fim do quiet period** (~25 dias corridos após a precificação de 10/jul). Historicamente 76-87% das coberturas de analistas iniciadas nessa janela são "compra"/"compra forte" e podem mover o papel +5% a +10% — mas é viés estrutural conhecido (os próprios bancos coordenadores do IPO iniciam cobertura otimista), não confirmação técnica real. Ver seção de pesquisa abaixo.
- **Expiração do lock-up — data exata não confirmada, checar prospecto F-1/424B4**: padrão de mercado é 90 ou 180 dias após o IPO, ou seja, entre **~out/2026 e ~jan/2027**. É quando Baillie Gifford, Coatue, Situational Awareness Partners e demais cornerstone investors ficam livres para vender — tende a criar pressão de venda antes/durante e, historicamente, um ponto de entrada melhor 4-7 meses após o IPO (depois que essa pressão já ocorreu).
- dez/2026: possível inclusão no Nasdaq 100 (rebalanceamento, fluxo passivo)
- set/2027: elegibilidade para o índice SOX (exige 3 meses listado)

## O que a pesquisa acadêmica diz sobre estratégia de IPO (aplicado à SKHY)

Pesquisa de mercado (Renaissance Capital, Ritter/Bradley, Michaely & Womack) reforça a tese conservadora já documentada acima, com dados concretos:

- **Comprar no pop de abertura tem histórico ruim**: o pop do dia 1 (SKHY: +13%, ~+18,8% é a média histórica de IPOs) beneficia quase só quem recebeu ações no preço de oferta (cornerstone investors), não quem compra no mercado aberto. Ritter (1.526 IPOs, 1975-1984) mediu underperformance de ~27% em 3 anos vs. empresas comparáveis do mesmo setor/tamanho — ou seja, o retail que compra no pop historicamente compra no pior ponto relativo.
- **O rali do fim do quiet period é enviesado, não é sinal técnico**: 87% das primeiras recomendações de analistas no fim do quiet period são "compra"/"compra forte" (Michaely & Womack, 1999) — parcialmente porque os bancos que fizeram o underwriting do IPO iniciam cobertura via seus próprios analistas. Um rali nessa janela não deve ser tratado como confirmação de tese, só como ruído de fluxo esperado.
- **A melhor janela de entrada, segundo esses dados, é pós-lock-up (4-7 meses após o IPO)**, não a primeira semana — reforça a Fase 1 (observação) e sugere que, mesmo depois da Fase 2, vale reavaliar a tese com mais rigor perto da expiração do lock-up em vez de assumir que "mais dados técnicos" = "mais seguro".
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

- **Gatilho de entrada**: defesa da VWAP intradiária ou rompimento da máxima da 1ª hora, após os primeiros 60-90 min de negociação. `ALERT_INDICATORS` (`artifacts/api-server/src/lib/alert-indicators.ts`) só suporta `price`, `rsi`, `macd`, `sma20`, `sma50` — **VWAP não é automatizável pela tela de Alerts**; esse gatilho exige checagem manual.
- **Regra de invalidação**: se nenhum gatilho válido se formar até o fechamento de segunda, não forçar entrada — reavaliar terça.
- **Stop**: usar ATR curto (3-5 candles, proxy provisório) × 1,5-2, não percentual fixo arbitrário — o range do dia 1 ($149-$177) já sugere volatilidade real acima de 8%.
- **R:R travado antes de entrar**: fixar os dois números (ex.: stop -7% / alvo parcial +14% = 2:1), não faixas soltas.
- **Position sizing escalonado**: não alocar os US$1.000 inteiros no gatilho de segunda; entrar com ~50% e reservar o resto para confirmação num pullback/rompimento subsequente.
- **Confirmação setorial**: além de NVDA/SMCI (lado da demanda), incluir MU (Micron) como referência — par mais direto do lado da oferta (mesma tese de memória/HBM) e com histórico completo.
- **Gate obrigatório de calendário**: reduzir ou zerar a posição remanescente até o fechamento de 28/jul/2026, véspera dos resultados do Q2 + listagem KOSPI simultânea (29/jul) — gap risk duplo que o trailing stop por mínima de 2 pregões não cobre (gap ocorre fora do pregão).
