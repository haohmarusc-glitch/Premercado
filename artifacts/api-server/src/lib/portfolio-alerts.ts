/**
 * Background job that checks portfolio price/holding alerts every 15 minutes.
 * Reads positions and purchases from DB, fetches live prices via yfinance,
 * and fires emails. Fired keys are persisted in portfolio_alert_firings so
 * deduplication survives server restarts.
 *
 * NOTE: o job roda sobre as posições de TODOS os usuários numa varredura só,
 * mas cada e-mail vai pro notify_email salvo NA PRÓPRIA posição (definido na
 * criação), não mais pra um endereço único compartilhado.
 */
import { spawn } from "child_process";
import { db, portfolioPositionsTable, portfolioPurchasesTable, portfolioAlertFiringsTable } from "@workspace/db";
import { sql } from "drizzle-orm";
import { agentDir, getPythonBin } from "./runner";
import { sendAlertEmail, sendPortfolioHoldingEmail, sendRecompraEmail } from "./mailer";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 15 * 60_000; // 15 min

const HOLDING_MILESTONES = [30, 60, 90, 180, 365];

interface PriceQuote {
  symbol: string;
  price: number | null;
  error: string | null;
}

const FETCH_TIMEOUT_MS = 30_000; // 30 s — se o Python travar, rejeita

function fetchPrices(tickers: string[]): Promise<PriceQuote[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    let out = "";
    let err = "";
    const timer = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("get_quotes timeout")); }, FETCH_TIMEOUT_MS);
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) { reject(new Error(`get_quotes exited ${code}: ${err}`)); return; }
      try { resolve(JSON.parse(out) as PriceQuote[]); } catch { reject(new Error(`Bad JSON from get_quotes: ${out}`)); }
    });
  });
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

async function checkPortfolioAlerts(): Promise<void> {
  const positions = await db.select().from(portfolioPositionsTable);
  if (!positions.length) return;

  const tickers = positions.map((p) => p.ticker);

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

    const pct = ((price - pos.avgCost) / pos.avgCost) * 100.0;

    for (const thr of pos.upAlertPcts) {
      const key = `gain:${pos.ticker}:${thr}`;
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
      const key = `loss:${pos.ticker}:${thr}`;
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
  const purchases = await db.select().from(portfolioPurchasesTable);
  const posMap = new Map(positions.map((p) => [p.id, p]));
  const today = new Date();

  for (const purchase of purchases) {
    const pos = posMap.get(purchase.positionId);
    if (!pos) continue;

    const ageDays = Math.floor((today.getTime() - new Date(purchase.purchaseDate).getTime()) / 86_400_000);

    for (const milestone of HOLDING_MILESTONES) {
      if (ageDays >= milestone) {
        const key = `holding:${pos.ticker}:${purchase.purchaseDate}:${milestone}`;
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
  const lotsByPos = new Map<number, typeof purchases>();
  for (const pu of purchases) {
    const arr = lotsByPos.get(pu.positionId) ?? [];
    arr.push(pu);
    lotsByPos.set(pu.positionId, arr);
  }

  for (const pos of positions) {
    const lots = lotsByPos.get(pos.id) ?? [];
    if (lots.length === 0) continue;
    const soldLots = lots.filter((p) => p.saleDate && p.salePrice != null && p.purchasePrice != null);
    const openLots = lots.filter((p) => !(p.saleDate && p.salePrice != null));
    // Só considera posições totalmente encerradas (você não detém mais)
    if (soldLots.length === 0 || openLots.length > 0) continue;

    const soldQty = soldLots.reduce((s, p) => s + p.amount / (p.purchasePrice as number), 0);
    const revenue = soldLots.reduce((s, p) => s + (p.amount / (p.purchasePrice as number)) * (p.salePrice as number), 0);
    const avgSalePrice = soldQty > 0 ? revenue / soldQty : null;
    const price = priceMap.get(pos.ticker);
    if (avgSalePrice == null || price == null || avgSalePrice <= 0) continue;

    const dropPct = ((avgSalePrice - price) / avgSalePrice) * 100;
    if (dropPct <= 0) continue;

    // dispara o MAIOR limiar cruzado (evita e-mails redundantes do mesmo nível)
    const crossed = pos.downAlertPcts.filter((thr) => dropPct >= thr);
    if (crossed.length === 0) continue;
    const thr = Math.max(...crossed);
    const key = `recompra:${pos.ticker}:${thr}`;
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
    } catch (e) {
      logger.error({ e }, "Portfolio alert check error");
    }
    setTimeout(loop, CHECK_INTERVAL_MS);
  }

  // primeira execução após 60 s para o servidor estabilizar
  setTimeout(loop, 60_000);
  logger.info("Portfolio alert checker started (interval: 15 min)");
}
