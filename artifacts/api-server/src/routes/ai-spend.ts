import { Router, type IRouter } from "express";
import { and, desc, eq, gte } from "drizzle-orm";
import { db, agentRunsTable, chatMessagesTable, chatSessionsTable } from "@workspace/db";
import { GetAgentSpendHistoryQueryParams, GetAgentSpendHistoryResponse } from "@workspace/api-zod";
import { requireAdmin } from "../middleware/require-auth";
import { todayBRTDateString } from "../lib/timezone";

const router: IRouter = Router();

interface SpendItem {
  id: string;
  source: "run" | "chat";
  timestamp: string;
  costUsd: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  llmProvider: string | null;
  llmModel: string | null;
  trigger?: string | null;
  mode?: string | null;
  status?: string | null;
  durationMs?: number | null;
  chatSessionTitle?: string | null;
}

// requireAdmin aplicado só nesta rota (mesmo motivo do comentário em runs.ts):
// um router.use() sem path vazaria pra tudo que for montado depois em
// index.ts. Gasto agregado de TODOS os usuários é uma visão administrativa,
// igual /agent/runs.
router.get("/agent/spend-history", requireAdmin, async (req, res): Promise<void> => {
  const parsed = GetAgentSpendHistoryQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const days = parsed.data.days ?? 30;
  const windowStart = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

  // Total "todos os dias juntos" precisa ser exato mesmo fora da janela
  // itemizada -- busca só a coluna de custo (sem limitar por data) nas duas
  // fontes, em vez de re-somar a lista já paginada.
  const [runsInWindow, allRunCosts, chatInWindow, allChatCosts] = await Promise.all([
    db
      .select()
      .from(agentRunsTable)
      .where(gte(agentRunsTable.startedAt, windowStart))
      .orderBy(desc(agentRunsTable.startedAt)),
    db.select({ costUsd: agentRunsTable.costUsd }).from(agentRunsTable),
    db
      .select({
        id: chatMessagesTable.id,
        createdAt: chatMessagesTable.createdAt,
        costUsd: chatMessagesTable.costUsd,
        inputTokens: chatMessagesTable.inputTokens,
        outputTokens: chatMessagesTable.outputTokens,
        cacheReadTokens: chatMessagesTable.cacheReadTokens,
        cacheWriteTokens: chatMessagesTable.cacheWriteTokens,
        llmProvider: chatMessagesTable.llmProvider,
        llmModel: chatMessagesTable.llmModel,
        sessionTitle: chatSessionsTable.title,
      })
      .from(chatMessagesTable)
      .leftJoin(chatSessionsTable, eq(chatSessionsTable.id, chatMessagesTable.sessionId))
      .where(and(eq(chatMessagesTable.role, "assistant"), gte(chatMessagesTable.createdAt, windowStart)))
      .orderBy(desc(chatMessagesTable.createdAt)),
    db
      .select({ costUsd: chatMessagesTable.costUsd })
      .from(chatMessagesTable)
      .where(eq(chatMessagesTable.role, "assistant")),
  ]);

  const items: SpendItem[] = [];

  for (const r of runsInWindow) {
    items.push({
      id: `run-${r.id}`,
      source: "run",
      timestamp: r.startedAt.toISOString(),
      // Driver pg devolve `numeric` como string -- converter antes de somar
      // (mesmo cuidado já tomado em runner.ts/settings.ts).
      costUsd: r.costUsd === null ? null : Number(r.costUsd),
      inputTokens: r.inputTokens,
      outputTokens: r.outputTokens,
      cacheReadTokens: r.cacheReadTokens,
      cacheWriteTokens: r.cacheWriteTokens,
      llmProvider: r.llmProvider,
      llmModel: r.llmModel,
      trigger: r.trigger,
      mode: r.mode,
      status: r.status,
      durationMs: r.durationMs,
    });
  }

  for (const m of chatInWindow) {
    items.push({
      id: `chat-${m.id}`,
      source: "chat",
      timestamp: m.createdAt.toISOString(),
      costUsd: m.costUsd === null ? null : Number(m.costUsd),
      inputTokens: m.inputTokens,
      outputTokens: m.outputTokens,
      cacheReadTokens: m.cacheReadTokens,
      cacheWriteTokens: m.cacheWriteTokens,
      llmProvider: m.llmProvider,
      llmModel: m.llmModel,
      chatSessionTitle: m.sessionTitle,
    });
  }

  items.sort((a, b) => b.timestamp.localeCompare(a.timestamp));

  const dayMap = new Map<string, SpendItem[]>();
  for (const item of items) {
    const date = todayBRTDateString(new Date(item.timestamp));
    const arr = dayMap.get(date) ?? [];
    arr.push(item);
    dayMap.set(date, arr);
  }

  const spendDays = [...dayMap.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, dayItems]) => ({
      date,
      totalCostUsd: dayItems.reduce((sum, i) => sum + (i.costUsd ?? 0), 0),
      items: dayItems,
    }));

  const windowTotalCostUsd = items.reduce((sum, i) => sum + (i.costUsd ?? 0), 0);

  const allCosts = [...allRunCosts, ...allChatCosts].map((r) =>
    r.costUsd === null ? null : Number(r.costUsd),
  );
  const hasCostData = allCosts.some((c) => c !== null);
  const allTimeTotalCostUsd = allCosts.reduce((sum: number, c) => sum + (c ?? 0), 0);

  res.json(
    GetAgentSpendHistoryResponse.parse({
      days: spendDays,
      windowTotalCostUsd,
      allTimeTotalCostUsd,
      hasCostData,
    }),
  );
});

export default router;
