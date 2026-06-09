/**
 * Background job that checks portfolio price/holding alerts every 15 minutes.
 * Reads positions and purchases from DB, fetches live prices via yfinance,
 * and fires emails using per-process deduplication.
 */
import { spawn } from "child_process";
import { db, portfolioPositionsTable, portfolioPurchasesTable } from "@workspace/db";
import { agentDir } from "./runner";
import { sendAlertEmail, sendPortfolioHoldingEmail } from "./mailer";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 15 * 60_000; // 15 min

// Milestones in days for holding alerts (matches portfolio.py)
const HOLDING_MILESTONES = [30, 60, 90, 180, 365];

// In-memory dedup: keys survive for the lifetime of this process
const firedKeys = new Set<string>();

interface PriceQuote {
  symbol: string;
  price: number | null;
  error: string | null;
}

function fetchPrices(tickers: string[]): Promise<PriceQuote[]> {
  return new Promise((resolve, reject) => {
    const py = spawn("python3", ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      if (code !== 0) { reject(new Error(`get_quotes exited ${code}: ${err}`)); return; }
      try { resolve(JSON.parse(out) as PriceQuote[]); } catch { reject(new Error(`Bad JSON from get_quotes: ${out}`)); }
    });
  });
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

  // ── Price threshold alerts ──────────────────────────────────────────────────
  for (const pos of positions) {
    const price = priceMap.get(pos.ticker);
    if (price == null) continue;

    const pct = ((price - pos.avgCost) / pos.avgCost) * 100.0;

    for (const thr of pos.upAlertPcts) {
      const key = `gain:${pos.ticker}:${thr}`;
      if (pct >= thr && !firedKeys.has(key)) {
        firedKeys.add(key);
        try {
          await sendAlertEmail({
            symbol: pos.ticker,
            condition: "above",
            thresholdPct: thr,
            currentChangePct: pct,
            currentPrice: price,
          });
          logger.info({ ticker: pos.ticker, pct: pct.toFixed(2), thr }, "Portfolio gain alert fired");
        } catch (err) {
          logger.error({ err, ticker: pos.ticker, thr }, "Failed to send gain alert email");
        }
      }
    }

    for (const thr of pos.downAlertPcts) {
      const key = `loss:${pos.ticker}:${thr}`;
      if (pct <= -thr && !firedKeys.has(key)) {
        firedKeys.add(key);
        try {
          await sendAlertEmail({
            symbol: pos.ticker,
            condition: "below",
            thresholdPct: -thr,
            currentChangePct: pct,
            currentPrice: price,
          });
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

    const purchaseMs = new Date(purchase.purchaseDate).getTime();
    const ageDays = Math.floor((today.getTime() - purchaseMs) / 86_400_000);

    for (const milestone of HOLDING_MILESTONES) {
      if (ageDays >= milestone) {
        const key = `holding:${pos.ticker}:${purchase.purchaseDate}:${milestone}`;
        if (!firedKeys.has(key)) {
          firedKeys.add(key);
          try {
            await sendPortfolioHoldingEmail({
              ticker: pos.ticker,
              purchaseDate: purchase.purchaseDate,
              milestone,
              amount: purchase.amount,
            });
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
}

let intervalHandle: ReturnType<typeof setInterval> | null = null;

export function startPortfolioAlertChecker(): void {
  if (intervalHandle) return;
  // First check after 60s startup grace period (after price alert checker's 30s)
  setTimeout(() => {
    checkPortfolioAlerts().catch((e) => logger.error({ e }, "Portfolio alert check error"));
  }, 60_000);
  intervalHandle = setInterval(() => {
    checkPortfolioAlerts().catch((e) => logger.error({ e }, "Portfolio alert check error"));
  }, CHECK_INTERVAL_MS);
  logger.info("Portfolio alert checker started (interval: 15 min)");
}
