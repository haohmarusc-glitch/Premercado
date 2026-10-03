/**
 * Background job that checks portfolio price/holding alerts every 15 minutes.
 * Reads positions and purchases from DB, fetches live prices via yfinance,
 * and fires emails. Fired keys are persisted in portfolio_alert_firings so
 * deduplication survives server restarts.
 *
 * NOTE: o job roda sobre as posições de TODOS os usuários numa varredura só,
 * mas cada e-mail vai pro notify_email salvo NA PRÓPRIA posição (definido na
 * criação), não mais pra um endereço único compartilhado.
 *
 * ## A varredura de 03/10/2026: 98 dos 204 disparos eram falsos
 *
 * Este arquivo era o quarto consumidor de `portfolio_positions` e o único que
 * nunca recebeu o filtro pelos lotes reais (§1 do playbook). Lia `quantity` e
 * `avg_cost` armazenados e percorria TODAS as posições, inclusive as já
 * encerradas. Três defeitos saíram disso:
 *
 * 1. **Divisão por zero -- 69 e-mails falsos.** `recomputePosition` zera
 *    `avg_cost` ao vender tudo, e `((price - 0) / 0) * 100` é `Infinity`, que
 *    passa em TODOS os limiares de ganho de uma vez. O e-mail saía dizendo
 *    "Infinity%". Confirmado seis vezes, sempre no ciclo de 15 min seguinte ao
 *    zeramento: 10/08 14:20 (25 disparos), 25/08, 31/08, 09/09, 21/09 e 02/10
 *    14:05 (21 disparos). O caso que fecha o argumento é `gain:BABA:50` em
 *    02/10 -- BABA comprada a 125,97 e vendida a 105,88, PREJUÍZO de 15,95%.
 *
 * 2. **Marcos de holding em lote vendido -- 29 e-mails falsos.** O laço não
 *    filtrava `sale_date`. `holding:META:2026-03-20:180` disparou em 16/09
 *    sobre um lote vendido em 07/05, 132 dias depois da venda. META foi
 *    importada já vendida e disparou os três primeiros marcos de uma vez.
 *
 * 3. **Chave por ticker, não por posição.** Com ticker repetido (ARM em #12 e
 *    #22, AVGO em #15 e #23, INTC em #3 e #17), a posição morta consumia as
 *    chaves da viva. Em 03/10 as duas únicas posições em ação da carteira
 *    estavam surdas: ARM #22 com `gain:ARM:10..50` consumidos pela ARM #12 em
 *    21/09 e `loss:ARM:10..30` consumidos em junho/julho -- nenhum alerta
 *    podia disparar nunca mais, nem com queda de 30%.
 *
 * As chaves ganharam o prefixo `v2` e o id da posição (ou da compra) em vez de
 * serem apagadas: os 204 disparos antigos são a evidência do defeito e ficam
 * no banco. Uma chave v1 não colide com uma v2, então a ARM #22 volta a
 * disparar sem que nada seja destruído.
 */
import { db, portfolioPositionsTable, portfolioPurchasesTable, portfolioAlertFiringsTable } from "@workspace/db";
import { sql } from "drizzle-orm";
import { agentDir, getPythonBin, state as agentState } from "./runner";
import { sendAlertEmail, sendPortfolioHoldingEmail, sendRecompraEmail } from "./mailer";
import { logger } from "./logger";
import { runExclusive } from "./python-queue";
import { spawnPython } from "./python-spawn";
import {
  loteEmAberto,
  vendaRegistrada,
  totaisDosLotesAbertos,
  custoMedioUtilizavel,
  variacaoContraCusto,
} from "./portfolio-math";

const CHECK_INTERVAL_MS = 15 * 60_000; // 15 min

const HOLDING_MILESTONES = [30, 60, 90, 180, 365];

interface PriceQuote {
  symbol: string;
  price: number | null;
  error: string | null;
}

// 120s. Era 30s, e este era o teto mais apertado de todos: em 04/08 ele
// estourou com o stderr mostrando apenas "[probe] boot +2.71s" e
// "+11.10s" -- ou seja, o processo ainda estava SUBINDO quando foi morto, sem
// ter chegado nem aos imports (que sozinhos levaram de 60s a 110s naquela
// janela). Ver o comentário do QUOTES_TIMEOUT_MS em alert-checker.ts.
//
// Cabe folgado no ciclo: o loop daqui só reagenda depois de terminar, com
// CHECK_INTERVAL_MS de 15 min.
const FETCH_TIMEOUT_MS = 120_000; // 2 min — se o Python travar, rejeita

// Sem prazo de validade (runExclusive, não runExclusiveFresh) de propósito: o
// loop daqui só reagenda DEPOIS de terminar (setTimeout no fim, não
// setInterval), então nunca há mais de uma tarefa deste checker na fila e ele
// não tem como formar backlog. Quem precisa de descarte é o alert-checker, que
// enfileira por setInterval independentemente de o ciclo anterior ter drenado.
function fetchPrices(tickers: string[]): Promise<PriceQuote[]> {
  return runExclusive("get_quotes", () => new Promise((resolve, reject) => {
    const py = spawnPython(getPythonBin(), ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      // AGENT_DEADLINE_TS: o Python deriva o orçamento do bounded_parallel_map
      // do tempo que realmente resta até FETCH_TIMEOUT_MS, em vez de usar uma
      // constante própria que precisava adivinhar o custo de startup (~8s só de
      // import). Aqui a folga era a mais apertada de todas: 20s de budget
      // interno contra 30s de timeout. Ver bounded_parallel.py.
      env: {
        ...process.env,
        PYTHONPATH: agentDir,
        AGENT_DEADLINE_TS: String(Date.now() + FETCH_TIMEOUT_MS),
      },
    });
    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      py.kill("SIGTERM");
      // stderr preservado: sem ele o timeout chega ao log sem nenhuma
      // pista do que o processo estava fazendo (ver startup_probe.py).
      const cauda = err.trim().slice(-2000);
      reject(new Error(cauda
        ? `get_quotes timeout (${FETCH_TIMEOUT_MS}ms). stderr: ${cauda}`
        : `get_quotes timeout (${FETCH_TIMEOUT_MS}ms). Nenhum stderr -- o processo não chegou a imprimir nada.`));
    }, FETCH_TIMEOUT_MS);
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) { reject(new Error(`get_quotes exited ${code}: ${err}`)); return; }
      try { resolve(JSON.parse(out) as PriceQuote[]); } catch { reject(new Error(`Bad JSON from get_quotes: ${out}`)); }
    });
  }));
}

async function loadFiredKeys(): Promise<Set<string>> {
  const rows = await db.select({ alertKey: portfolioAlertFiringsTable.alertKey }).from(portfolioAlertFiringsTable);
  return new Set(rows.map((r) => r.alertKey));
}

async function persistKey(key: string): Promise<void> {
  await db.execute(
    sql`INSERT INTO portfolio_alert_firings (alert_key) VALUES (${key}) ON CONFLICT DO NOTHING`,
  );
}

export async function checkPortfolioAlerts(): Promise<void> {
  // O agente diário já satura CPU/rede com dezenas de chamadas Python em
  // paralelo -- rodar fetchPrices (outro subprocesso Python) ao mesmo tempo
  // faz os dois competirem e estourar o timeout de 30s (visto em produção).
  // Pula o ciclo inteiro; o próximo (15 min depois) roda sem a agente por
  // perto na maioria das vezes, já que a run dura poucos minutos.
  if (agentState.running) {
    logger.info("Portfolio alert checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const positions = await db.select().from(portfolioPositionsTable);
  if (!positions.length) return;

  // Os lotes vêm ANTES do laço de preço, não depois. Eram buscados só na
  // seção de holding, lá embaixo -- tarde demais para o bloco de ganho/perda,
  // que por isso decidia tudo pelo `quantity`/`avg_cost` armazenados.
  const purchases = await db.select().from(portfolioPurchasesTable);
  const lotsByPosition = new Map<number, typeof purchases>();
  for (const pu of purchases) {
    const list = lotsByPosition.get(pu.positionId) ?? [];
    list.push(pu);
    lotsByPosition.set(pu.positionId, list);
  }

  // Deduplicado: com ticker repetido entre uma posição encerrada e uma nova
  // (ARM, AVGO e INTC em 03/10), get_quotes recebia o mesmo símbolo duas vezes.
  const tickers = [...new Set(positions.map((p) => p.ticker))];

  let quotes: PriceQuote[];
  try {
    quotes = await fetchPrices(tickers);
  } catch (err) {
    logger.warn({ err }, "Portfolio alert checker: failed to fetch prices");
    return;
  }

  const priceMap = new Map<string, number>(
    quotes.flatMap((q) => (q.price != null ? [[q.symbol, q.price]] : [])),
  );

  // Load persisted fired keys once per run
  const firedKeys = await loadFiredKeys();

  // ── Price threshold alerts ──────────────────────────────────────────────────
  for (const pos of positions) {
    const price = priceMap.get(pos.ticker);
    if (price == null) continue;

    // Só posição com lote efetivamente em aberto. Uma posição encerrada não
    // tem ganho nem perda a avisar -- o que ela pode gerar é alerta de
    // recompra, no bloco próprio lá embaixo.
    const lots = lotsByPosition.get(pos.id) ?? [];
    const abertos = lots.filter(loteEmAberto);
    if (lots.length > 0 && abertos.length === 0) continue;

    // Custo médio dos LOTES, não o campo armazenado. `PUT /portfolio/:id`
    // edita `avg_cost` direto, e `recomputePosition` o zera ao vender tudo.
    // Sem lote nenhum (posição importada por script, SGOV em 03/10) o campo
    // armazenado é a única fonte que existe.
    const avgCost = lots.length > 0
      ? totaisDosLotesAbertos(abertos).avgCost
      : Number(pos.avgCost);

    // A guarda não depende do filtro acima estar certo, de propósito: divisão
    // por zero não pode ser responsabilidade de um `continue` dois passos
    // atrás. `variacaoContraCusto` devolve null em vez de Infinity/NaN.
    const pct = variacaoContraCusto(price, avgCost);
    if (pct == null) {
      if (custoMedioUtilizavel(avgCost) == null) {
        logger.debug(
          { ticker: pos.ticker, positionId: pos.id, avgCost },
          "Portfolio alert checker: custo médio inutilizável, pulando alerta de preço",
        );
      }
      continue;
    }

    for (const thr of pos.upAlertPcts) {
      const key = `gain:v2:${pos.id}:${pos.ticker}:${thr}`;
      if (pct >= thr && !firedKeys.has(key)) {
        try {
          await sendAlertEmail({
            to: pos.notifyEmail,
            symbol: pos.ticker,
            condition: "above",
            thresholdPct: thr,
            thresholdPrice: null,
            currentChangePct: pct,
            currentPrice: price,
          });
          await persistKey(key);
          firedKeys.add(key);
          logger.info({ ticker: pos.ticker, pct: pct.toFixed(2), thr }, "Portfolio gain alert fired");
        } catch (err) {
          logger.error({ err, ticker: pos.ticker, thr }, "Failed to send gain alert email");
        }
      }
    }

    for (const thr of pos.downAlertPcts) {
      const key = `loss:v2:${pos.id}:${pos.ticker}:${thr}`;
      if (pct <= -thr && !firedKeys.has(key)) {
        try {
          await sendAlertEmail({
            to: pos.notifyEmail,
            symbol: pos.ticker,
            condition: "below",
            thresholdPct: -thr,
            thresholdPrice: null,
            currentChangePct: pct,
            currentPrice: price,
          });
          await persistKey(key);
          firedKeys.add(key);
          logger.info({ ticker: pos.ticker, pct: pct.toFixed(2), thr }, "Portfolio loss alert fired");
        } catch (err) {
          logger.error({ err, ticker: pos.ticker, thr }, "Failed to send loss alert email");
        }
      }
    }
  }

  // ── Holding milestone alerts ────────────────────────────────────────────────
  const posMap = new Map(positions.map((p) => [p.id, p]));
  const today = new Date();

  for (const purchase of purchases) {
    const pos = posMap.get(purchase.positionId);
    if (!pos) continue;

    // Lote vendido não acumula tempo de posse. Sem isto, 29 dos 67 marcos
    // registrados eram falsos -- "META: 180 dias de holding" em 16/09 sobre um
    // lote vendido em 07/05.
    if (vendaRegistrada(purchase)) continue;

    const ageDays = Math.floor((today.getTime() - new Date(purchase.purchaseDate).getTime()) / 86_400_000);

    for (const milestone of HOLDING_MILESTONES) {
      if (ageDays >= milestone) {
        // Chaveado pelo id da COMPRA, não por ticker+data: dois lotes do mesmo
        // ticker na mesma data existem de verdade (SMCI 14/05 e 22/07, SKHY
        // 15/07 -- lotes divididos para venda parcial), e com a chave antiga o
        // segundo nunca disparava porque o primeiro já tinha gravado a chave.
        const key = `holding:v2:${purchase.id}:${pos.ticker}:${milestone}`;
        if (!firedKeys.has(key)) {
          try {
            await sendPortfolioHoldingEmail({
              to: pos.notifyEmail,
              ticker: pos.ticker,
              purchaseDate: purchase.purchaseDate,
              milestone,
              amount: purchase.amount,
            });
            await persistKey(key);
            firedKeys.add(key);
            logger.info(
              { ticker: pos.ticker, purchaseDate: purchase.purchaseDate, milestone },
              "Holding milestone alert fired",
            );
          } catch (err) {
            logger.error({ err, ticker: pos.ticker, milestone }, "Failed to send holding alert email");
          }
        }
      }
    }
  }

  // ── Recompra: ações totalmente vendidas que caíram abaixo do preço de venda ──
  // Usa os mesmos limiares de baixa (downAlertPcts) da posição. Dispara quando
  // o preço atual está thr% abaixo do preço médio de venda.
  //
  // Este é o único bloco que QUER posição encerrada, então ele não usa o
  // filtro de "lote em aberto" do bloco de ganho/perda -- usa o oposto. Está
  // dito aqui porque "filtrar pelos lotes abertos" aplicado cegamente aos
  // três blocos desligaria o alerta de recompra inteiro.
  for (const pos of positions) {
    const lots = lotsByPosition.get(pos.id) ?? [];
    if (lots.length === 0) continue;
    // purchasePrice é exigido além da venda: sem ele não há como contar shares.
    const soldLots = lots.filter((p) => vendaRegistrada(p) && p.purchasePrice != null);
    const openLots = lots.filter(loteEmAberto);
    // Só considera posições totalmente encerradas (você não detém mais)
    if (soldLots.length === 0 || openLots.length > 0) continue;

    const shares = (p: typeof lots[number]) => Number(p.amount) / Number(p.purchasePrice);
    const soldQty = soldLots.reduce((s, p) => s + shares(p), 0);
    const revenue = soldLots.reduce((s, p) => s + shares(p) * Number(p.salePrice), 0);
    const avgSalePrice = soldQty > 0 ? revenue / soldQty : null;
    const price = priceMap.get(pos.ticker);
    if (avgSalePrice == null || !Number.isFinite(avgSalePrice) || price == null || avgSalePrice <= 0) continue;

    const dropPct = ((avgSalePrice - price) / avgSalePrice) * 100;
    if (dropPct <= 0) continue;

    // dispara o MAIOR limiar cruzado (evita e-mails redundantes do mesmo nível)
    const crossed = pos.downAlertPcts.filter((thr) => dropPct >= thr);
    if (crossed.length === 0) continue;
    const thr = Math.max(...crossed);
    // v2 + id da posição pelo mesmo motivo dos outros dois: `recompra:ARM:30`
    // foi gravado em 14/07 pelo primeiro encerramento da ARM #12, e o segundo
    // encerramento (21/09, a um preço médio de venda diferente) não tinha como
    // avisar nada.
    const key = `recompra:v2:${pos.id}:${pos.ticker}:${thr}`;
    if (firedKeys.has(key)) continue;

    try {
      await sendRecompraEmail({ to: pos.notifyEmail, ticker: pos.ticker, salePrice: avgSalePrice, currentPrice: price, dropPct, thresholdPct: thr });
      await persistKey(key);
      firedKeys.add(key);
      logger.info({ ticker: pos.ticker, dropPct: dropPct.toFixed(2), thr }, "Recompra alert fired");
    } catch (err) {
      logger.error({ err, ticker: pos.ticker, thr }, "Failed to send recompra alert email");
    }
  }
}

let checkerStarted = false;

export function startPortfolioAlertChecker(): void {
  if (checkerStarted) return;
  checkerStarted = true;

  async function loop(): Promise<void> {
    try {
      await checkPortfolioAlerts();
    } catch (err) {
      // `err`, não `e` -- ver comentário em alert-checker.ts::dispararCiclo.
      logger.error({ err }, "Portfolio alert check error");
    }
    setTimeout(loop, CHECK_INTERVAL_MS);
  }

  // primeira execução após 60 s para o servidor estabilizar
  setTimeout(loop, 60_000);
  logger.info("Portfolio alert checker started (interval: 15 min)");
}
