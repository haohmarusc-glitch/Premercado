import { Router, type IRouter } from "express";
import { eq, gte } from "drizzle-orm";
import { db, settingsTable, agentRunsTable } from "@workspace/db";
import { GetSettingsResponse, UpdateSettingsBody, UpdateSettingsResponse, GetAgentSpendResponse } from "@workspace/api-zod";
import { applySettings } from "../lib/scheduler";
import { startOfTodayBRT, todayBRTDateString } from "../lib/timezone";

const router: IRouter = Router();

async function getOrCreateSettings() {
  const [existing] = await db.select().from(settingsTable).limit(1);
  if (existing) return existing;
  const notifyEmail = process.env.NOTIFY_EMAIL ?? "";
  const [created] = await db
    .insert(settingsTable)
    .values({
      notifyEmail,
      scheduleEnabled: true,
      scheduleHour: 8,
      scheduleMinute: 30,
      tickers: ["NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA", "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML"],
      premarketEnabled: false,
      premarketIntervalMin: 30,
      premarketWindowStartHour: 6,
      premarketWindowEndHour: 9,
      agentProvider: null,
      dailyBudgetUsd: null,
      cheapProvider: "gemini",
    })
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
  const data = { ...parsed.data };
  if (data.tickers) {
    data.tickers = [...new Set(data.tickers.map((t) => t.trim().toUpperCase()).filter(Boolean))];
  }
  const [updated] = await db
    .update(settingsTable)
    .set({ ...data, updatedAt: new Date() })
    .where(eq(settingsTable.id, existing.id))
    .returning();
  applySettings(updated);
  res.json(UpdateSettingsResponse.parse(serializeSettings(updated)));
});

router.get("/agent/spend", async (_req, res): Promise<void> => {
  const settings = await getOrCreateSettings();
  const rawRuns = await db
    .select({ costUsd: agentRunsTable.costUsd, llmProvider: agentRunsTable.llmProvider })
    .from(agentRunsTable)
    .where(gte(agentRunsTable.startedAt, startOfTodayBRT()));
  // O driver pg devolve colunas `numeric` como string — converter antes de somar
  // para não cair em concatenação de string (ex.: 0 + "0.60" + "0.55" = "00.600.55").
  const runs = rawRuns.map((r) => ({
    ...r,
    costUsd: r.costUsd === null ? null : Number(r.costUsd),
  }));

  // costUsd null em uma run = custo indeterminado (modelo sem preço conhecido,
  // ver MODEL_PRICING em provider.py) — contamina o total do grupo/geral para null.
  const byProviderMap = new Map<string, { costUsd: number | null; runs: number; calls: number }>();
  for (const run of runs) {
    const provider = run.llmProvider ?? "desconhecido";
    const entry = byProviderMap.get(provider) ?? { costUsd: 0, runs: 0, calls: 0 };
    entry.runs += 1;
    entry.calls += 1;
    entry.costUsd = entry.costUsd === null || run.costUsd === null ? null : entry.costUsd + run.costUsd;
    byProviderMap.set(provider, entry);
  }

  const byProvider = [...byProviderMap.entries()].map(([provider, v]) => ({
    provider,
    costUsd: v.costUsd,
    runs: v.runs,
    calls: v.calls,
  }));

  const totalCostUsd = runs.some((r) => r.costUsd === null)
    ? null
    : runs.reduce((sum, r) => sum + (r.costUsd ?? 0), 0);

  const primaryProvider = settings.agentProvider || "anthropic";
  const primarySpend = byProvider
    .filter((p) => p.provider.split(",").includes(primaryProvider))
    .reduce((sum, p) => sum + (p.costUsd ?? 0), 0);
  const dailyBudgetUsd = settings.dailyBudgetUsd === null || settings.dailyBudgetUsd === undefined ? null : Number(settings.dailyBudgetUsd);
  const budgetRemainingUsd = dailyBudgetUsd !== null ? Math.max(0, dailyBudgetUsd - primarySpend) : null;
  const budgetExceeded = dailyBudgetUsd !== null && primarySpend >= dailyBudgetUsd;

  res.json(
    GetAgentSpendResponse.parse({
      date: todayBRTDateString(),
      byProvider,
      totalCostUsd,
      primaryProvider,
      dailyBudgetUsd,
      budgetRemainingUsd,
      budgetExceeded,
    }),
  );
});

export { getOrCreateSettings };
export default router;
