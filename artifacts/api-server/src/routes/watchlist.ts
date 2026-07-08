import { Router, type IRouter } from "express";
import { and, asc, eq } from "drizzle-orm";
import { db, watchlistTable } from "@workspace/db";
import {
  ListWatchlistResponse,
  ListWatchlistResponseItem as WatchlistItemSchema,
  CreateWatchlistBody,
  DeleteWatchlistItemParams as WatchlistItemParams,
} from "@workspace/api-zod";

const router: IRouter = Router();

function ser(r: typeof watchlistTable.$inferSelect) {
  return { ...r, addedAt: r.addedAt.toISOString() };
}

// GET /watchlist
router.get("/watchlist", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(watchlistTable)
    .where(eq(watchlistTable.userId, req.userId!))
    .orderBy(asc(watchlistTable.addedAt));
  res.json(ListWatchlistResponse.parse(rows.map(ser)));
});

// POST /watchlist
router.post("/watchlist", async (req, res): Promise<void> => {
  const body = CreateWatchlistBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }
  const [row] = await db
    .insert(watchlistTable)
    .values({ ticker: body.data.ticker.toUpperCase(), notes: body.data.notes ?? null, userId: req.userId! })
    .returning();
  res.status(201).json(WatchlistItemSchema.parse(ser(row)));
});

// DELETE /watchlist/:id
router.delete("/watchlist/:id", async (req, res): Promise<void> => {
  const p = WatchlistItemParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  const deleted = await db
    .delete(watchlistTable)
    .where(and(eq(watchlistTable.id, p.data.id), eq(watchlistTable.userId, req.userId!)))
    .returning({ id: watchlistTable.id });
  if (!deleted.length) { res.status(404).json({ error: "Not found" }); return; }
  res.status(204).send();
});

export default router;
