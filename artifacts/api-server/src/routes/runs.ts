import { Router, type IRouter } from "express";
import { desc } from "drizzle-orm";
import { db, agentRunsTable } from "@workspace/db";
import { ListAgentRunsQueryParams, ListAgentRunsResponse } from "@workspace/api-zod";
import { requireAdmin } from "../middleware/require-auth";

const router: IRouter = Router();

function serializeRun(r: typeof agentRunsTable.$inferSelect) {
  return {
    ...r,
    startedAt: r.startedAt.toISOString(),
    finishedAt: r.finishedAt?.toISOString() ?? null,
  };
}

// requireAdmin aplicado só nesta rota -- um router.use() sem path aqui
// vazaria pra TODAS as rotas montadas depois desta no router pai (index.ts
// encadeia vários routers via app.use(subRouter) sem prefixo), bloqueando
// coisas sem relação nenhuma com Runs (visto em teste: POST /portfolio
// passou a exigir admin por causa disso).
router.get("/agent/runs", requireAdmin, async (req, res): Promise<void> => {
  const parsed = ListAgentRunsQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const limit = parsed.data.limit ?? 50;
  const rows = await db
    .select()
    .from(agentRunsTable)
    .orderBy(desc(agentRunsTable.startedAt))
    .limit(limit);

  res.json(ListAgentRunsResponse.parse(rows.map(serializeRun)));
});

export default router;
