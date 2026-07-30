import { Router, type IRouter } from "express";
import { and, desc, eq, isNull, or } from "drizzle-orm";
import { db, reportsTable } from "@workspace/db";
import {
  GetReportParams,
  GetReportResponse,
  GetLatestReportResponse,
  ListReportsResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();

type ReportRow = typeof reportsTable.$inferSelect;

function serializeReport(row: ReportRow) {
  return { ...row, createdAt: row.createdAt.toISOString() };
}

// Visível pra um usuário: relatório "de casa" (userId null -- daily/premarket/
// coal/ai/news/alerts/manual/scheduled, compartilhados desde sempre) OU
// relatório gerado a partir da PRÓPRIA carteira dele (portfolio/veredito, ver
// reportsTable.userId e runner.ts). Sem isso, /reports devolvia a tabela
// inteira pra qualquer usuário logado -- incluindo o veredito/análise de
// carteira gerado por OUTRO usuário.
function visibleToUser(userId: number) {
  return or(isNull(reportsTable.userId), eq(reportsTable.userId, userId));
}

router.get("/reports", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(reportsTable)
    .where(visibleToUser(req.userId!))
    .orderBy(desc(reportsTable.createdAt));
  res.json(ListReportsResponse.parse(rows.map(serializeReport)));
});

// Returns the latest report of a given mode (default "daily", used by the
// dashboard main view; "veredito" is used by the tela Veredito do Dia).
router.get("/reports/latest", async (req, res): Promise<void> => {
  const mode = typeof req.query.mode === "string" && req.query.mode ? req.query.mode : "daily";
  const [row] = await db
    .select()
    .from(reportsTable)
    .where(and(eq(reportsTable.mode, mode), visibleToUser(req.userId!)))
    .orderBy(desc(reportsTable.createdAt))
    .limit(1);
  if (!row) {
    res.status(404).json({ error: "No reports yet" });
    return;
  }
  res.json(GetLatestReportResponse.parse(serializeReport(row)));
});

router.get("/reports/:id", async (req, res): Promise<void> => {
  const params = GetReportParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }
  const [row] = await db
    .select()
    .from(reportsTable)
    .where(and(eq(reportsTable.id, params.data.id), visibleToUser(req.userId!)));
  if (!row) {
    res.status(404).json({ error: "Report not found" });
    return;
  }
  res.json(GetReportResponse.parse(serializeReport(row)));
});

export default router;
