/**
 * Routes called by the Python agent subprocess (localhost only).
 * NOT protected by requireAuth — the agent process runs on the same host
 * and cannot hold a session cookie. Access is restricted to loopback.
 */
import { Router, type IRouter, type Request, type Response, type NextFunction } from "express";
import { desc } from "drizzle-orm";
import { db, observationsTable } from "@workspace/db";
import { z } from "zod/v4";

const router: IRouter = Router();

function localhostOnly(req: Request, res: Response, next: NextFunction): void {
  const ip = req.socket.remoteAddress ?? "";
  if (ip === "127.0.0.1" || ip === "::1" || ip === "::ffff:127.0.0.1") {
    return next();
  }
  res.status(403).json({ error: "Forbidden" });
}

router.use(localhostOnly);

const InternalObservationInput = z.object({
  ticker: z.string(),
  date: z.string(),
  summary: z.string(),
  sentiment: z.string(),
  priceAtObservation: z.number().optional().nullable(),
});

// Save observation from Python agent
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

// Read recent observations for Python agent memory
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
