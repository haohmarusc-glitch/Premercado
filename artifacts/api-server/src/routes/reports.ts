import { Router, type IRouter } from "express";
import { and, desc, eq } from "drizzle-orm";
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

router.get("/reports", async (_req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(reportsTable)
    .orderBy(desc(reportsTable.createdAt));
  res.json(ListReportsResponse.parse(rows.map(serializeReport)));
});

// Returns the latest DAILY report (mode=daily) for the dashboard main view.
router.get("/reports/latest", async (_req, res): Promise<void> => {
  const [row] = await db
    .select()
    .from(reportsTable)
    .where(eq(reportsTable.mode, "daily"))
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
    .where(eq(reportsTable.id, params.data.id));
  if (!row) {
    res.status(404).json({ error: "Report not found" });
    return;
  }
  res.json(GetReportResponse.parse(serializeReport(row)));
});

export default router;
