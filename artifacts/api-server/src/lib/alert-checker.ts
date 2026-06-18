/**
 * Background job that checks price alerts every 5 minutes.
 * For each enabled alert, fetches the current changePct and fires an email
 * if the threshold is crossed (with a 4-hour cooldown per alert).
 */
import { spawn } from "child_process";
import { eq, and } from "drizzle-orm";
import { db, alertsTable, alertFiringsTable, settingsTable } from "@workspace/db";
import { agentDir, getPythonBin } from "./runner";
import { sendAlertEmail } from "./mailer";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 5 * 60_000; // 5 min
const COOLDOWN_MS = 4 * 60 * 60_000; // 4 hours

interface Quote {
  symbol: string;
  changePct: number | null;
  price: number | null;
}

function fetchQuotes(tickers: string[]): Promise<Quote[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      if (code !== 0) { reject(new Error(`get_quotes: ${err}`)); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

async function checkAlerts(): Promise<void> {
  // Get all enabled alerts
  const alerts = await db
    .select()
    .from(alertsTable)
    .where(eq(alertsTable.enabled, true));

  if (!alerts.length) return;

  // Get distinct symbols
  const symbols = [...new Set(alerts.map((a) => a.symbol))];

  let quotes: Quote[];
  try {
    quotes = await fetchQuotes(symbols);
  } catch (err) {
    logger.warn({ err }, "Alert checker: failed to fetch quotes");
    return;
  }

  const quoteMap = new Map(quotes.map((q) => [q.symbol, q]));
  const now = new Date();

  for (const alert of alerts) {
    const quote = quoteMap.get(alert.symbol);
    if (!quote || quote.changePct == null) continue;

    let triggered = false;
    if (alert.thresholdPrice != null && quote.price != null) {
      triggered = alert.condition === "above"
        ? quote.price >= alert.thresholdPrice
        : quote.price <= alert.thresholdPrice;
    } else if (alert.thresholdPct != null && quote.changePct != null) {
      triggered = alert.condition === "above"
        ? quote.changePct >= alert.thresholdPct
        : quote.changePct <= alert.thresholdPct;
    }

    if (!triggered) continue;

    // Cooldown check
    if (alert.lastTriggeredAt) {
      const elapsed = now.getTime() - new Date(alert.lastTriggeredAt).getTime();
      if (elapsed < COOLDOWN_MS) continue;
    }

    // Fire notification
    try {
      await sendAlertEmail({
        symbol: alert.symbol,
        condition: alert.condition,
        thresholdPct: alert.thresholdPct,
        currentChangePct: quote.changePct,
        currentPrice: quote.price,
      });

      await db
        .update(alertsTable)
        .set({ lastTriggeredAt: now })
        .where(eq(alertsTable.id, alert.id));

      await db.insert(alertFiringsTable).values({
        alertId: alert.id,
        symbol: alert.symbol,
        condition: alert.condition,
        thresholdPct: alert.thresholdPct,
        thresholdPrice: alert.thresholdPrice,
        changePctAtFiring: quote.changePct,
        priceAtFiring: quote.price,
        firedAt: now,
      });

      logger.info(
        { symbol: alert.symbol, changePct: quote.changePct, threshold: alert.thresholdPct },
        "Price alert triggered",
      );
    } catch (err) {
      logger.error({ err, alertId: alert.id }, "Failed to send alert email");
    }
  }
}

let intervalHandle: ReturnType<typeof setInterval> | null = null;

export function startAlertChecker(): void {
  if (intervalHandle) return;
  // First check after 30s startup grace period
  const firstCheck = setTimeout(() => {
    checkAlerts().catch((e) => logger.error({ e }, "Alert check error"));
  }, 30_000);
  // Then every 5 min
  intervalHandle = setInterval(() => {
    checkAlerts().catch((e) => logger.error({ e }, "Alert check error"));
  }, CHECK_INTERVAL_MS);
  logger.info("Price alert checker started (interval: 5 min)");
}
