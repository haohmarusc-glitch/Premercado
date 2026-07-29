import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, scenarioAlertSettingsTable, usersTable } from "@workspace/db";
import { GetScenarioAlertSettingsResponse, UpdateScenarioAlertSettingsBody, UpdateScenarioAlertSettingsResponse } from "@workspace/api-zod";

const router: IRouter = Router();

function defaultDataAlvo(): string {
  // Sem config salva ainda -- horizonte padrão de 90 dias, só pra popular o
  // formulário com algo razoável (o usuário troca antes de salvar).
  const d = new Date(Date.now() + 90 * 86400000);
  return d.toISOString().slice(0, 10);
}

function serialize(row: typeof scenarioAlertSettingsTable.$inferSelect) {
  return {
    configured: true,
    dataAlvo: row.dataAlvo,
    thresholdPct: Number(row.thresholdPct),
    enabled: row.enabled,
    notifyEmail: row.notifyEmail,
    lastFiredAt: row.lastFiredAt?.toISOString() ?? null,
  };
}

router.get("/scenario-alert-settings", async (req, res): Promise<void> => {
  const [row] = await db
    .select()
    .from(scenarioAlertSettingsTable)
    .where(eq(scenarioAlertSettingsTable.userId, req.userId!));

  if (!row) {
    res.json(GetScenarioAlertSettingsResponse.parse({
      configured: false,
      dataAlvo: defaultDataAlvo(),
      thresholdPct: 50,
      enabled: false,
      notifyEmail: null,
      lastFiredAt: null,
    }));
    return;
  }

  res.json(GetScenarioAlertSettingsResponse.parse(serialize(row)));
});

router.patch("/scenario-alert-settings", async (req, res): Promise<void> => {
  const body = UpdateScenarioAlertSettingsBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }

  const [existing] = await db
    .select()
    .from(scenarioAlertSettingsTable)
    .where(eq(scenarioAlertSettingsTable.userId, req.userId!));

  let notifyEmail = body.data.notifyEmail !== undefined ? body.data.notifyEmail : existing?.notifyEmail ?? null;
  if (!notifyEmail && body.data.notifyEmail === undefined && !existing) {
    const [me] = await db.select({ email: usersTable.email }).from(usersTable).where(eq(usersTable.id, req.userId!)).limit(1);
    notifyEmail = me?.email ?? null;
  }

  if (existing) {
    const [updated] = await db
      .update(scenarioAlertSettingsTable)
      .set({
        dataAlvo: body.data.dataAlvo ?? existing.dataAlvo,
        thresholdPct: body.data.thresholdPct ?? Number(existing.thresholdPct),
        enabled: body.data.enabled ?? existing.enabled,
        notifyEmail,
        updatedAt: new Date(),
      })
      .where(eq(scenarioAlertSettingsTable.userId, req.userId!))
      .returning();
    res.json(UpdateScenarioAlertSettingsResponse.parse(serialize(updated)));
    return;
  }

  const [created] = await db
    .insert(scenarioAlertSettingsTable)
    .values({
      userId: req.userId!,
      dataAlvo: body.data.dataAlvo ?? defaultDataAlvo(),
      thresholdPct: body.data.thresholdPct ?? 50,
      enabled: body.data.enabled ?? true,
      notifyEmail,
    })
    .returning();
  res.json(UpdateScenarioAlertSettingsResponse.parse(serialize(created)));
});

export default router;
