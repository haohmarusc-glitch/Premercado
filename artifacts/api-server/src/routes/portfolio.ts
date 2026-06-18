import { Router, type IRouter } from "express";
import { asc, eq } from "drizzle-orm";
import { db, portfolioPositionsTable, portfolioPurchasesTable } from "@workspace/db";
import {
  ListPortfolioPositionsResponse,
  PortfolioPositionSchema,
  CreatePortfolioPositionBody,
  UpdatePortfolioPositionBody,
  PortfolioPositionParams,
  ListPortfolioPurchasesResponse,
  PortfolioPurchaseSchema,
  CreatePortfolioPurchaseBody,
  UpdatePortfolioPurchaseBody,
  PortfolioPurchaseParams,
} from "@workspace/api-zod";
import { logger } from "../lib/logger";

const router: IRouter = Router();

function serPos(r: typeof portfolioPositionsTable.$inferSelect) {
  return { ...r, createdAt: r.createdAt.toISOString(), updatedAt: r.updatedAt.toISOString() };
}
function serPur(r: typeof portfolioPurchasesTable.$inferSelect) {
  return {
    ...r,
    purchasePrice: r.purchasePrice ?? null,
    saleDate: r.saleDate ?? null,
    salePrice: r.salePrice ?? null,
    createdAt: r.createdAt.toISOString(),
  };
}

// Seed initial portfolio data if table is empty
const SEED_POSITIONS = [
  { ticker: "NVDA", quantity: 5.37435,  avgCost: 208.21, investedAmount: 1119.00, firstPurchaseDate: "2026-03-20" },
  { ticker: "MU",   quantity: 0.46091,  avgCost: 865.65, investedAmount:  398.99, firstPurchaseDate: "2026-05-14" },
  { ticker: "INTC", quantity: 3.35583,  avgCost: 104.29, investedAmount:  350.00, firstPurchaseDate: "2026-06-02" },
  { ticker: "ARM",  quantity: 0.87559,  avgCost: 399.73, investedAmount:  350.00, firstPurchaseDate: "2026-06-02" },
  { ticker: "GOOGL",quantity: 0.81693,  avgCost: 367.22, investedAmount:  300.00, firstPurchaseDate: "2026-06-02" },
  { ticker: "TSLA", quantity: 0.53411,  avgCost: 374.45, investedAmount:  200.00, firstPurchaseDate: "2026-03-20" },
  { ticker: "SMCI", quantity: 13.98789, avgCost:  39.31, investedAmount:  550.00, firstPurchaseDate: "2026-05-14" },
] as const;

const SEED_PURCHASES: Record<string, Array<{ purchaseDate: string; amount: number }>> = {
  NVDA:  [{ purchaseDate: "2026-03-20", amount: 300.00 }, { purchaseDate: "2026-05-18", amount: 470.00 },
          { purchaseDate: "2026-05-20", amount: 140.00 }, { purchaseDate: "2026-05-21", amount: 70.00  },
          { purchaseDate: "2026-05-27", amount: 139.00 }],
  MU:    [{ purchaseDate: "2026-05-14", amount: 258.99 }, { purchaseDate: "2026-06-02", amount: 140.00 }],
  INTC:  [{ purchaseDate: "2026-06-02", amount: 350.00 }],
  ARM:   [{ purchaseDate: "2026-06-02", amount: 350.00 }],
  GOOGL: [{ purchaseDate: "2026-06-02", amount: 300.00 }],
  TSLA:  [{ purchaseDate: "2026-03-20", amount: 200.00 }],
  SMCI:  [{ purchaseDate: "2026-05-14", amount: 250.00 }, { purchaseDate: "2026-06-02", amount: 300.00 }],
};

export async function seedPortfolioIfEmpty() {
  try {
    const existing = await db.select({ id: portfolioPositionsTable.id }).from(portfolioPositionsTable).limit(1);
    if (existing.length > 0) return;

    for (const pos of SEED_POSITIONS) {
      const [inserted] = await db.insert(portfolioPositionsTable).values(pos).returning();
      const purchases = SEED_PURCHASES[pos.ticker] ?? [];
      if (purchases.length > 0) {
        await db.insert(portfolioPurchasesTable).values(
          purchases.map((p) => ({ positionId: inserted.id, ...p })),
        );
      }
    }
    logger.info("Portfolio seeded with initial positions");
  } catch (err) {
    logger.error({ err }, "Failed to seed portfolio");
  }
}

// GET /portfolio
router.get("/portfolio", async (_req, res): Promise<void> => {
  const rows = await db.select().from(portfolioPositionsTable).orderBy(asc(portfolioPositionsTable.createdAt));
  res.json(ListPortfolioPositionsResponse.parse(rows.map(serPos)));
});

// GET /portfolio/:id/purchases
router.get("/portfolio/:id/purchases", async (req, res): Promise<void> => {
  const p = PortfolioPositionParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  const rows = await db
    .select()
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.positionId, p.data.id))
    .orderBy(asc(portfolioPurchasesTable.purchaseDate));
  res.json(ListPortfolioPurchasesResponse.parse(rows.map(serPur)));
});

// POST /portfolio
router.post("/portfolio", async (req, res): Promise<void> => {
  const body = CreatePortfolioPositionBody.safeParse(req.body);
  if (!body.success) { res.status(400).json({ error: body.error.message }); return; }
  const [row] = await db
    .insert(portfolioPositionsTable)
    .values({ ...body.data, ticker: body.data.ticker.toUpperCase() })
    .returning();
  res.status(201).json(PortfolioPositionSchema.parse(serPos(row)));
});

// PUT /portfolio/:id
router.put("/portfolio/:id", async (req, res): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  const body = UpdatePortfolioPositionBody.safeParse(req.body);
  if (!params.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const update: Record<string, unknown> = { ...body.data, updatedAt: new Date() };
  if (body.data.ticker) update.ticker = body.data.ticker.toUpperCase();
  const [row] = await db
    .update(portfolioPositionsTable)
    .set(update)
    .where(eq(portfolioPositionsTable.id, params.data.id))
    .returning();
  if (!row) { res.status(404).json({ error: "Position not found" }); return; }
  res.json(PortfolioPositionSchema.parse(serPos(row)));
});

// DELETE /portfolio/:id
router.delete("/portfolio/:id", async (req, res): Promise<void> => {
  const p = PortfolioPositionParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  await db.delete(portfolioPositionsTable).where(eq(portfolioPositionsTable.id, p.data.id));
  res.status(204).send();
});

// POST /portfolio/:id/purchases
router.post("/portfolio/:id/purchases", async (req, res): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  const body = CreatePortfolioPurchaseBody.safeParse(req.body);
  if (!params.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const [row] = await db
    .insert(portfolioPurchasesTable)
    .values({ positionId: params.data.id, ...body.data })
    .returning();
  res.status(201).json(PortfolioPurchaseSchema.parse(serPur(row)));
});

// PATCH /portfolio/purchases/:purchaseId — registrar venda
router.patch("/portfolio/purchases/:purchaseId", async (req, res): Promise<void> => {
  const p = PortfolioPurchaseParams.safeParse(req.params);
  const body = UpdatePortfolioPurchaseBody.safeParse(req.body);
  if (!p.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }
  const [row] = await db
    .update(portfolioPurchasesTable)
    .set({ saleDate: body.data.saleDate ?? null, salePrice: body.data.salePrice ?? null })
    .where(eq(portfolioPurchasesTable.id, p.data.purchaseId))
    .returning();
  if (!row) { res.status(404).json({ error: "Not found" }); return; }
  res.json(PortfolioPurchaseSchema.parse(serPur(row)));
});

// DELETE /portfolio/purchases/:purchaseId
router.delete("/portfolio/purchases/:purchaseId", async (req, res): Promise<void> => {
  const p = PortfolioPurchaseParams.safeParse(req.params);
  if (!p.success) { res.status(400).json({ error: "invalid id" }); return; }
  await db.delete(portfolioPurchasesTable).where(eq(portfolioPurchasesTable.id, p.data.purchaseId));
  res.status(204).send();
});

export default router;
