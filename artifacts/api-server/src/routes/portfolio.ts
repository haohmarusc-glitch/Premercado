import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { asc, eq } from "drizzle-orm";
import { db, portfolioPositionsTable, portfolioPurchasesTable, settingsTable } from "@workspace/db";
import { getOrCreateSettings } from "./settings";
import { getPythonBin, agentDir } from "../lib/runner";
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

// Fetch real historical close prices for a ticker on a set of dates (via yfinance)
function fetchHistoricalPrices(ticker: string, dates: string[]): Promise<Record<string, number>> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_historical_price.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ ticker, dates }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try {
        const parsed = JSON.parse(out) as { prices?: Record<string, number> };
        resolve(parsed.prices ?? {});
      } catch { reject(new Error("Parse error")); }
    });
  });
}

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

// GET /portfolio/cash — saldo em USD não investido por modo (real/paper)
router.get("/portfolio/cash", async (_req, res): Promise<void> => {
  const s = await getOrCreateSettings();
  res.json({ real: Number(s.cashReal ?? 0), simulated: Number(s.cashSimulated ?? 0) });
});

// PATCH /portfolio/cash — { mode: "real" | "simulated", amount: number }
router.patch("/portfolio/cash", async (req, res): Promise<void> => {
  const mode = req.body?.mode;
  const amount = Number(req.body?.amount);
  if ((mode !== "real" && mode !== "simulated") || !Number.isFinite(amount) || amount < 0) {
    res.status(400).json({ error: "mode (real|simulated) e amount >= 0 obrigatórios" });
    return;
  }
  const s = await getOrCreateSettings();
  const patch = mode === "real" ? { cashReal: amount } : { cashSimulated: amount };
  const [updated] = await db
    .update(settingsTable)
    .set({ ...patch, updatedAt: new Date() })
    .where(eq(settingsTable.id, s.id))
    .returning();
  res.json({ real: Number(updated.cashReal ?? 0), simulated: Number(updated.cashSimulated ?? 0) });
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

// GET /portfolio/historical-price?ticker=NVDA&date=2026-03-20 — single real close price
router.get("/portfolio/historical-price", async (req, res): Promise<void> => {
  const ticker = String(req.query.ticker ?? "").toUpperCase();
  const date = String(req.query.date ?? "");
  if (!ticker || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    res.status(400).json({ error: "ticker and valid date (YYYY-MM-DD) required" });
    return;
  }
  try {
    const prices = await fetchHistoricalPrices(ticker, [date]);
    res.json({ ticker, date, price: prices[date] ?? null });
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

// POST /portfolio/:id/backfill-prices — corrige purchasePrice de cada compra
// usando o fechamento real da data (yfinance). Por padrão só preenche compras
// sem preço; passe { force: true } para sobrescrever todas.
router.post("/portfolio/:id/backfill-prices", async (req, res): Promise<void> => {
  const params = PortfolioPositionParams.safeParse(req.params);
  if (!params.success) { res.status(400).json({ error: "invalid id" }); return; }
  const force = req.body?.force === true;

  const [pos] = await db
    .select()
    .from(portfolioPositionsTable)
    .where(eq(portfolioPositionsTable.id, params.data.id));
  if (!pos) { res.status(404).json({ error: "Position not found" }); return; }

  const purchases = await db
    .select()
    .from(portfolioPurchasesTable)
    .where(eq(portfolioPurchasesTable.positionId, params.data.id));

  const targets = force ? purchases : purchases.filter((p) => p.purchasePrice == null);
  if (targets.length === 0) {
    res.json({ updated: 0, message: "Nenhuma compra para atualizar" });
    return;
  }

  try {
    const dates = [...new Set(targets.map((p) => p.purchaseDate))];
    const prices = await fetchHistoricalPrices(pos.ticker, dates);

    let updated = 0;
    for (const p of targets) {
      const price = prices[p.purchaseDate];
      if (price == null) continue;
      await db
        .update(portfolioPurchasesTable)
        .set({ purchasePrice: price })
        .where(eq(portfolioPurchasesTable.id, p.id));
      updated++;
    }
    res.json({ updated, total: targets.length, prices });
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

export default router;
