import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, settingsTable } from "@workspace/db";
import { GetSettingsResponse, UpdateSettingsBody, UpdateSettingsResponse } from "@workspace/api-zod";
import { applySettings } from "../lib/scheduler";

const router: IRouter = Router();

async function getOrCreateSettings() {
  const [existing] = await db.select().from(settingsTable).limit(1);
  if (existing) return existing;
  const notifyEmail = process.env.NOTIFY_EMAIL ?? "";
  const [created] = await db
    .insert(settingsTable)
    .values({ notifyEmail, scheduleEnabled: true, scheduleHour: 8, scheduleMinute: 30, tickers: ["MU", "SMCI"] })
    .returning();
  return created;
}

function serializeSettings(s: typeof settingsTable.$inferSelect) {
  return { ...s, updatedAt: s.updatedAt.toISOString() };
}

router.get("/settings", async (_req, res): Promise<void> => {
  const settings = await getOrCreateSettings();
  res.json(GetSettingsResponse.parse(serializeSettings(settings)));
});

router.patch("/settings", async (req, res): Promise<void> => {
  const parsed = UpdateSettingsBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const existing = await getOrCreateSettings();
  const [updated] = await db
    .update(settingsTable)
    .set({ ...parsed.data, updatedAt: new Date() })
    .where(eq(settingsTable.id, existing.id))
    .returning();
  applySettings(updated);
  res.json(UpdateSettingsResponse.parse(serializeSettings(updated)));
});

export { getOrCreateSettings };
export default router;
