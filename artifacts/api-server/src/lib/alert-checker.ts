/**
 * Background job that checks price alerts every 5 minutes.
 * For each enabled alert, fetches the current changePct/price (indicator
 * 'price') or RSI/MACD/SMA (demais indicadores) and fires an email if the
 * condition is met (with a 4-hour cooldown per alert).
 *
 * NOTE: intencionalmente NÃO escopado por usuário -- checa/notifica os
 * alertas de TODOS os usuários e manda pro único notifyEmail compartilhado
 * (settings). Alertas passaram a ter dono (user_id) só pra separar os DADOS
 * entre contas; esse job de sistema continua rodando sobre a tabela inteira.
 */
import { spawn } from "child_process";
import path from "path";
import { eq } from "drizzle-orm";
import { db, alertsTable, alertFiringsTable, type Alert } from "@workspace/db";
import { agentDir, getPythonBin } from "./runner";
import { sendAlertEmail } from "./mailer";
import { logger } from "./logger";
import { evalTechnical, type Technicals } from "./alert-technical-eval";

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

function fetchTechnicals(tickers: string[]): Promise<Technicals[]> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_technicals.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_technicals: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { items?: Technicals[] };
        resolve(parsed.items ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

async function fireAlert(
  alert: Alert,
  now: Date,
  opts: {
    currentPrice: number | null;
    currentChangePct: number | null;
    valueAtFiring: number | null;
  },
): Promise<void> {
  try {
    await sendAlertEmail({
      symbol: alert.symbol,
      indicator: alert.indicator,
      condition: alert.condition,
      thresholdPct: alert.thresholdPct,
      thresholdPrice: alert.thresholdPrice,
      thresholdValue: alert.thresholdValue,
      valueAtFiring: opts.valueAtFiring,
      currentChangePct: opts.currentChangePct,
      currentPrice: opts.currentPrice,
    });

    await db
      .update(alertsTable)
      .set({ lastTriggeredAt: now })
      .where(eq(alertsTable.id, alert.id));

    await db.insert(alertFiringsTable).values({
      alertId: alert.id,
      symbol: alert.symbol,
      indicator: alert.indicator,
      condition: alert.condition,
      thresholdPct: alert.thresholdPct,
      thresholdPrice: alert.thresholdPrice,
      thresholdValue: alert.thresholdValue,
      valueAtFiring: opts.valueAtFiring,
      changePctAtFiring: opts.currentChangePct,
      priceAtFiring: opts.currentPrice,
      firedAt: now,
    });

    logger.info(
      { symbol: alert.symbol, indicator: alert.indicator, condition: alert.condition },
      "Alert triggered",
    );
  } catch (err) {
    logger.error({ err, alertId: alert.id }, "Failed to send alert email");
  }
}

async function checkAlerts(): Promise<void> {
  // Get all enabled alerts
  const alerts = await db
    .select()
    .from(alertsTable)
    .where(eq(alertsTable.enabled, true));

  if (!alerts.length) return;

  const priceAlerts = alerts.filter((a) => a.indicator === "price");
  const technicalAlerts = alerts.filter((a) => a.indicator !== "price");
  const now = new Date();

  const withinCooldown = (alert: Alert): boolean => {
    if (!alert.lastTriggeredAt) return false;
    return now.getTime() - new Date(alert.lastTriggeredAt).getTime() < COOLDOWN_MS;
  };

  // ── Alertas de preco/variacao (comportamento original) ──────────────────
  if (priceAlerts.length) {
    const symbols = [...new Set(priceAlerts.map((a) => a.symbol))];
    let quotes: Quote[] = [];
    try {
      quotes = await fetchQuotes(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch quotes");
    }
    const quoteMap = new Map(quotes.map((q) => [q.symbol, q]));

    for (const alert of priceAlerts) {
      const quote = quoteMap.get(alert.symbol);
      if (!quote) continue;

      // Alerta por preço só precisa do preço; por variação, só do changePct
      // (no pré-mercado o changePct costuma vir nulo — não pode barrar o de preço)
      let triggered = false;
      let valueAtFiring: number | null = null;
      if (alert.thresholdPrice != null) {
        if (quote.price == null) continue;
        triggered = alert.condition === "above"
          ? quote.price >= alert.thresholdPrice
          : quote.price <= alert.thresholdPrice;
        valueAtFiring = quote.price;
      } else if (alert.thresholdPct != null) {
        if (quote.changePct == null) continue;
        triggered = alert.condition === "above"
          ? quote.changePct >= alert.thresholdPct
          : quote.changePct <= alert.thresholdPct;
        valueAtFiring = quote.changePct;
      }

      if (!triggered || withinCooldown(alert)) continue;
      await fireAlert(alert, now, { currentPrice: quote.price, currentChangePct: quote.changePct, valueAtFiring });
    }
  }

  // ── Alertas por condicao tecnica (RSI/MACD/SMA) ──────────────────────────
  if (technicalAlerts.length) {
    const symbols = [...new Set(technicalAlerts.map((a) => a.symbol))];
    let technicals: Technicals[] = [];
    try {
      technicals = await fetchTechnicals(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch technicals");
    }
    const techMap = new Map(technicals.map((t) => [t.ticker, t]));

    for (const alert of technicalAlerts) {
      const t = techMap.get(alert.symbol);
      if (!t || t.error) continue;

      const valueAtFiring = evalTechnical(alert, t);
      if (valueAtFiring == null || withinCooldown(alert)) continue;
      await fireAlert(alert, now, { currentPrice: t.price ?? null, currentChangePct: null, valueAtFiring });
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
