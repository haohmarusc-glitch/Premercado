import { Router, type IRouter } from "express";
import { desc, eq, and, gte } from "drizzle-orm";
import { db, observationsTable } from "@workspace/db";
import {
  ListObservationsQueryParams,
  ListObservationsResponse,
  GetObservationsSummaryResponse,
} from "@workspace/api-zod";
import { z } from "zod/v4";

const router: IRouter = Router();

const InternalObservationInput = z.object({
  ticker: z.string(),
  date: z.string(),
  summary: z.string(),
  sentiment: z.string(),
  priceAtObservation: z.number().optional().nullable(),
});

router.get("/observations", async (req, res): Promise<void> => {
  const parsed = ListObservationsQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const { ticker, limit } = parsed.data;

  let query = db.select().from(observationsTable).$dynamic();
  if (ticker) {
    query = query.where(eq(observationsTable.ticker, ticker.toUpperCase()));
  }
  const rows = await query
    .orderBy(desc(observationsTable.createdAt))
    .limit(limit ?? 50);

  res.json(
    ListObservationsResponse.parse(
      rows.map((row) => ({ ...row, createdAt: row.createdAt.toISOString() })),
    ),
  );
});

router.get("/observations/summary", async (_req, res): Promise<void> => {
  const rows = await db.select().from(observationsTable).orderBy(desc(observationsTable.createdAt));

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

// Internal route used by Python agent to save observations
router.post("/observations/internal", async (req, res): Promise<void> => {
  const parsed = InternalObservationInput.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const [row] = await db
    .insert(observationsTable)
    .values({
      ticker: parsed.data.ticker,
      date: parsed.data.date,
      summary: parsed.data.summary,
      sentiment: parsed.data.sentiment,
      priceAtObservation: parsed.data.priceAtObservation ?? undefined,
    })
    .returning();
  res.status(201).json(row);
});

// Internal route used by Python memory module to read observations
router.get("/observations/internal", async (req, res): Promise<void> => {
  const limit = parseInt(String(req.query.limit ?? "30"), 10);
  const rows = await db
    .select()
    .from(observationsTable)
    .orderBy(desc(observationsTable.createdAt))
    .limit(limit);
  res.json(rows);
});

export default router;
