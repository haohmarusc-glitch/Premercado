import { Router, type IRouter } from "express";
import { desc, eq } from "drizzle-orm";
import { db, tradeJournalTable } from "@workspace/db";
import {
  ListJournalResponse,
  UpdateJournalEntryResponse as TradeJournalEntrySchema,
  CreateJournalEntryBody,
  UpdateJournalEntryBody,
  UpdateJournalEntryParams as JournalEntryParams,
} from "@workspace/api-zod";

const router: IRouter = Router();

function ser(r: typeof tradeJournalTable.$inferSelect) {
  return {
    ...r,
    entryPrice: r.entryPrice ?? null,
    stopLoss: r.stopLoss ?? null,
    targetPrice: r.targetPrice ?? null,
    exitDate: r.exitDate ?? null,
    exitPrice: r.exitPrice ?? null,
    result: r.result ?? null,
    notes: r.notes ?? null,
    thesis: r.thesis ?? null,
    createdAt: r.createdAt.toISOString(),
    updatedAt: r.updatedAt.toISOString(),
  };
}

// GET /journal
router.get("/journal", async (_req, res): Promise<void> => {
  const rows = await db.select().from(tradeJournalTable).orderBy(desc(tradeJournalTable.createdAt));
  res.json(ListJournalResponse.parse(rows.map(ser)));
});

// POST /journal
router.post("/journal", async (req, res): Promise<void> => {
  const body = CreateJournalEntryBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }
  const [row] = await db.insert(tradeJournalTable).values({ ...body.data, ticker: body.data.ticker.toUpperCase() }).returning();
  res.status(201).json(TradeJournalEntrySchema.parse(ser(row)));
});

// PUT /journal/:id
router.put("/journal/:id", async (req, res): Promise<void> => {
  const p = JournalEntryParams.safeParse(req.params);
  const body = UpdateJournalEntryBody.safeParse(req.body);
  if (!p.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const [row] = await db
    .update(tradeJournalTable)
    .set({ ...body.data, updatedAt: new Date() })
    .where(eq(tradeJournalTable.id, p.data.id))
    .returning();
  if (!row) { res.status(404).json({ error: "Not found" }); return; }
  res.json(TradeJournalEntrySchema.parse(ser(row)));
});

// DELETE /journal/:id
router.delete("/journal/:id", async (req, res): Promise<void> => {
  const p = JournalEntryParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  await db.delete(tradeJournalTable).where(eq(tradeJournalTable.id, p.data.id));
  res.status(204).send();
});

export default router;
