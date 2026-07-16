import { Router, type IRouter } from "express";
import { and, asc, eq } from "drizzle-orm";
import { db, exitPlanItemsTable } from "@workspace/db";
import {
  ListExitPlanResponse,
  UpdateExitPlanItemResponse as ExitPlanItemSchema,
  CreateExitPlanItemBody,
  UpdateExitPlanItemBody,
  UpdateExitPlanItemParams as ExitPlanItemParams,
  DeleteExitPlanItemParams,
} from "@workspace/api-zod";

const router: IRouter = Router();

function ser(r: typeof exitPlanItemsTable.$inferSelect) {
  return {
    ...r,
    eventDate: r.eventDate ?? null,
    soldAt: r.soldAt ?? null,
    soldPrice: r.soldPrice ?? null,
    createdAt: r.createdAt.toISOString(),
    updatedAt: r.updatedAt.toISOString(),
  };
}

// GET /exit-plan
router.get("/exit-plan", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(exitPlanItemsTable)
    .where(eq(exitPlanItemsTable.userId, req.userId!))
    .orderBy(asc(exitPlanItemsTable.targetDate));
  res.json(ListExitPlanResponse.parse(rows.map(ser)));
});

// POST /exit-plan
router.post("/exit-plan", async (req, res): Promise<void> => {
  const body = CreateExitPlanItemBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }
  const [row] = await db
    .insert(exitPlanItemsTable)
    .values({ ...body.data, ticker: body.data.ticker.toUpperCase(), userId: req.userId! })
    .returning();
  res.status(201).json(ExitPlanItemSchema.parse(ser(row)));
});

// PATCH /exit-plan/:id
router.patch("/exit-plan/:id", async (req, res): Promise<void> => {
  const p = ExitPlanItemParams.safeParse(req.params);
  const body = UpdateExitPlanItemBody.safeParse(req.body);
  if (!p.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const [row] = await db
    .update(exitPlanItemsTable)
    .set({ ...body.data, updatedAt: new Date() })
    .where(and(eq(exitPlanItemsTable.id, p.data.id), eq(exitPlanItemsTable.userId, req.userId!)))
    .returning();
  if (!row) { res.status(404).json({ error: "Not found" }); return; }
  res.json(ExitPlanItemSchema.parse(ser(row)));
});

// DELETE /exit-plan/:id
router.delete("/exit-plan/:id", async (req, res): Promise<void> => {
  const p = DeleteExitPlanItemParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  const deleted = await db
    .delete(exitPlanItemsTable)
    .where(and(eq(exitPlanItemsTable.id, p.data.id), eq(exitPlanItemsTable.userId, req.userId!)))
    .returning({ id: exitPlanItemsTable.id });
  if (!deleted.length) { res.status(404).json({ error: "Not found" }); return; }
  res.status(204).send();
});

export default router;
