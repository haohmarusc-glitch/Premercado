import { Router, type IRouter } from "express";
import { eq, desc } from "drizzle-orm";
import { db, alertsTable } from "@workspace/db";
import {
  ListAlertsResponse,
  ListAlertsResponseItem,
  CreateAlertBody,
  ToggleAlertBody,
  ToggleAlertResponse,
} from "@workspace/api-zod";
import { logger } from "../lib/logger";

const router: IRouter = Router();

function serializeAlert(a: typeof alertsTable.$inferSelect) {
  return {
    ...a,
    createdAt: a.createdAt.toISOString(),
    lastTriggeredAt: a.lastTriggeredAt?.toISOString() ?? null,
  };
}

router.get("/alerts", async (_req, res): Promise<void> => {
  const rows = await db.select().from(alertsTable).orderBy(desc(alertsTable.createdAt));
  res.json(ListAlertsResponse.parse(rows.map(serializeAlert)));
});

router.post("/alerts", async (req, res): Promise<void> => {
  const parsed = CreateAlertBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const { symbol, condition, thresholdPct } = parsed.data;
  if (condition !== "above" && condition !== "below") {
    res.status(400).json({ error: "condition must be 'above' or 'below'" });
    return;
  }

  const [row] = await db.insert(alertsTable)
    .values({ symbol: symbol.toUpperCase(), condition, thresholdPct })
    .returning();
  res.status(201).json(ListAlertsResponseItem.parse(serializeAlert(row)));
});

router.delete("/alerts/:id", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

  const deleted = await db.delete(alertsTable).where(eq(alertsTable.id, id)).returning();
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
    .where(eq(alertsTable.id, id))
    .returning();
  if (!updated) { res.status(404).json({ error: "Not found" }); return; }
  res.json(ToggleAlertResponse.parse(serializeAlert(updated)));
});

export default router;
