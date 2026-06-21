import { Router, type IRouter } from "express";
import { desc, eq, and, gte, lte, inArray } from "drizzle-orm";
import { db, observationsTable } from "@workspace/db";
import {
  ListObservationsQueryParams,
  ListObservationsResponse,
  GetObservationsSummaryResponse,
} from "@workspace/api-zod";
const router: IRouter = Router();

router.get("/observations", async (req, res): Promise<void> => {
  const parsed = ListObservationsQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const { ticker, tickers, limit } = parsed.data;

  let query = db.select().from(observationsTable).$dynamic();
  if (ticker) {
    query = query.where(eq(observationsTable.ticker, ticker.toUpperCase()));
  } else if (tickers) {
    const tickerList = tickers.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    if (tickerList.length > 0) {
      query = query.where(inArray(observationsTable.ticker, tickerList));
    }
  }
  const rows = await query
    .orderBy(desc(observationsTable.createdAt))
    .limit(limit ?? 50);

  res.json(
    ListObservationsResponse.parse(
      rows.map((row) => ({ ...row, userNotes: row.userNotes ?? null, createdAt: row.createdAt.toISOString() })),
    ),
  );
});

router.get("/observations/summary", async (_req, res): Promise<void> => {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 7);
  const cutoffDate = cutoff.toISOString().split("T")[0]; // YYYY-MM-DD

  const rows = await db
    .select()
    .from(observationsTable)
    .where(gte(observationsTable.date, cutoffDate))
    .orderBy(desc(observationsTable.createdAt));

  const byTicker: Record<string, { bullish: number; bearish: number; neutral: number; lastSentiment: string; lastDate: string }> = {};

  for (const row of rows) {
    if (!byTicker[row.ticker]) {
      byTicker[row.ticker] = { bullish: 0, bearish: 0, neutral: 0, lastSentiment: row.sentiment, lastDate: row.date };
    }
    const s = row.sentiment as "bullish" | "bearish" | "neutral";
    if (s === "bullish") byTicker[row.ticker].bullish++;
    else if (s === "bearish") byTicker[row.ticker].bearish++;
    else byTicker[row.ticker].neutral++;
  }

  const result = Object.entries(byTicker).map(([ticker, counts]) => ({
    ticker,
    ...counts,
  }));

  res.json(GetObservationsSummaryResponse.parse(result));
});

// Delete all observations for a given date (YYYY-MM-DD) — useful to clear bad data
// Must be declared before /:id to avoid Express matching "by-date" as an id
router.delete("/observations/by-date/:date", async (req, res): Promise<void> => {
  const date = req.params.date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { res.status(400).json({ error: "invalid date" }); return; }
  const result = await db
    .delete(observationsTable)
    .where(eq(observationsTable.date, date))
    .returning({ id: observationsTable.id });
  res.json({ deleted: result.length });
});

// PUT /observations/:id — update userNotes
router.put("/observations/:id", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "invalid id" }); return; }
  const { userNotes } = req.body;
  const [row] = await db
    .update(observationsTable)
    .set({ userNotes: userNotes ?? null, updatedAt: new Date() })
    .where(eq(observationsTable.id, id))
    .returning();
  if (!row) { res.status(404).json({ error: "Not found" }); return; }
  res.json({ id: row.id, userNotes: row.userNotes });
});

// Delete a single observation by id
router.delete("/observations/:id", async (req, res): Promise<void> => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ error: "invalid id" }); return; }
  await db.delete(observationsTable).where(eq(observationsTable.id, id));
  res.status(204).send();
});

export default router;
