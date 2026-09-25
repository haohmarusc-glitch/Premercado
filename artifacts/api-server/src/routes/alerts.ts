import { Router, type IRouter } from "express";
import { and, eq, desc, inArray, sql } from "drizzle-orm";
import { db, alertsTable, alertFiringsTable, usersTable } from "@workspace/db";
import {
  ListAlertsResponse,
  ListAlertsResponseItem,
  CreateAlertBody,
  ToggleAlertBody,
  ToggleAlertResponse,
  ListAlertFiringsResponse,
  GetAlertFiringsSummaryResponse,
} from "@workspace/api-zod";
import { logger } from "../lib/logger";
import { startOfTodayBRT } from "../lib/timezone";
import { isAlertIndicator } from "../lib/alert-indicators";
import { validarCondicoes } from "../lib/alert-conditions";

const router: IRouter = Router();

function serializeAlert(a: typeof alertsTable.$inferSelect) {
  return {
    ...a,
    createdAt: a.createdAt.toISOString(),
    lastTriggeredAt: a.lastTriggeredAt?.toISOString() ?? null,
  };
}

router.get("/alerts/firings/summary", async (req, res): Promise<void> => {
  const today = startOfTodayBRT();

  const allAlerts = await db
    .select({ id: alertsTable.id, enabled: alertsTable.enabled })
    .from(alertsTable)
    .where(eq(alertsTable.userId, req.userId!));

  const alertIds = allAlerts.map((a) => a.id);
  const todayFirings = alertIds.length === 0 ? [] : await db
    .select({ id: alertFiringsTable.id })
    .from(alertFiringsTable)
    .where(and(inArray(alertFiringsTable.alertId, alertIds), sql`${alertFiringsTable.firedAt} >= ${today}`));

  res.json(GetAlertFiringsSummaryResponse.parse({
    total: allAlerts.length,
    active: allAlerts.filter((a) => a.enabled).length,
    firingToday: todayFirings.length,
  }));
});

router.get("/alerts", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(alertsTable)
    .where(eq(alertsTable.userId, req.userId!))
    .orderBy(desc(alertsTable.createdAt));
  res.json(ListAlertsResponse.parse(rows.map(serializeAlert)));
});

router.post("/alerts", async (req, res): Promise<void> => {
  const parsed = CreateAlertBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const { symbol, condition, thresholdPct, thresholdPrice, thresholdValue } = parsed.data;
  let notifyEmail = parsed.data.notifyEmail?.trim() || null;
  if (!notifyEmail) {
    const [me] = await db.select({ email: usersTable.email }).from(usersTable).where(eq(usersTable.id, req.userId!)).limit(1);
    notifyEmail = me?.email ?? null;
  }
  const indicator = parsed.data.indicator ?? "price";
  if (!isAlertIndicator(indicator)) {
    res.status(400).json({ error: `indicator must be one of: price, rsi, macd, sma20, sma50` });
    return;
  }
  if (condition !== "above" && condition !== "below") {
    res.status(400).json({ error: "condition must be 'above' or 'below'" });
    return;
  }

  const comuns = {
    confirmAtClose: parsed.data.confirmAtClose ?? false,
    fireOnce: parsed.data.fireOnce ?? false,
    note: parsed.data.note?.trim() || null,
  };

  // Alerta com lista de condições (E). Quando ela vem, substitui
  // indicator/threshold_* -- que continuam gravados porque o schema os exige e
  // porque um alerta composto de uma condição só tem de permanecer legível para
  // quem ler a tabela pelas colunas antigas.
  const conditions = parsed.data.conditions;
  if (conditions !== undefined) {
    const erro = validarCondicoes(conditions);
    if (erro) { res.status(400).json({ error: erro }); return; }
    const [row] = await db.insert(alertsTable)
      .values({
        symbol: symbol.toUpperCase(), indicator, condition,
        thresholdPct, thresholdPrice, thresholdValue,
        conditions, ...comuns, notifyEmail, userId: req.userId!,
      })
      .returning();
    res.status(201).json(ListAlertsResponseItem.parse(serializeAlert(row)));
    return;
  }

  if (indicator === "price") {
    if (thresholdPct == null && thresholdPrice == null) {
      res.status(400).json({ error: "thresholdPct or thresholdPrice is required for indicator 'price'" });
      return;
    }
  } else if (indicator === "rsi") {
    if (thresholdValue == null) {
      res.status(400).json({ error: "thresholdValue (nivel de RSI) is required for indicator 'rsi'" });
      return;
    }
  }
  // macd/sma20/sma50 nao usam threshold: 'above'/'below' ja descreve a condicao
  // (macd: histograma bullish/bearish; sma: preco acima/abaixo da media).

  const [row] = await db.insert(alertsTable)
    .values({ symbol: symbol.toUpperCase(), indicator, condition, thresholdPct, thresholdPrice, thresholdValue, ...comuns, notifyEmail, userId: req.userId! })
    .returning();
  res.status(201).json(ListAlertsResponseItem.parse(serializeAlert(row)));
});

router.delete("/alerts/:id", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

  const deleted = await db.delete(alertsTable)
    .where(and(eq(alertsTable.id, id), eq(alertsTable.userId, req.userId!)))
    .returning();
  if (!deleted.length) { res.status(404).json({ error: "Not found" }); return; }
  res.status(204).end();
});

router.patch("/alerts/:id", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

  const parsed = ToggleAlertBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const [updated] = await db.update(alertsTable)
    .set({ enabled: parsed.data.enabled })
    .where(and(eq(alertsTable.id, id), eq(alertsTable.userId, req.userId!)))
    .returning();
  if (!updated) { res.status(404).json({ error: "Not found" }); return; }
  res.json(ToggleAlertResponse.parse(serializeAlert(updated)));
});

router.get("/alerts/:id/firings", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

  const alert = await db.select().from(alertsTable)
    .where(and(eq(alertsTable.id, id), eq(alertsTable.userId, req.userId!)))
    .limit(1);
  if (!alert.length) { res.status(404).json({ error: "Not found" }); return; }

  const rows = await db
    .select()
    .from(alertFiringsTable)
    .where(eq(alertFiringsTable.alertId, id))
    .orderBy(desc(alertFiringsTable.firedAt))
    .limit(20);

  res.json(ListAlertFiringsResponse.parse(rows.map((r) => ({
    ...r,
    firedAt: r.firedAt.toISOString(),
  }))));
});

export default router;
