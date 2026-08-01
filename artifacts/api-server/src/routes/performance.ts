import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { db, portfolioPositionsTable, portfolioPurchasesTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { asc, eq, inArray } from "drizzle-orm";
import { computeOpenLotTotals, isPositionActiveFromLots } from "../lib/portfolio-math";

const router: IRouter = Router();

router.get("/performance", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(portfolioPositionsTable)
    .where(eq(portfolioPositionsTable.userId, req.userId!))
    .orderBy(asc(portfolioPositionsTable.createdAt));

  // Ativo/vendido é decidido pelos lotes reais (portfolio_purchases), não
  // pelo campo `quantity` armazenado -- mesmo motivo/mesmo padrão de
  // buildScenarioPositions em routes/scenarios.ts: PUT /portfolio/:id edita
  // quantity/avgCost/investedAmount direto, sem recalcular a partir dos
  // lotes, então uma posição com todos os lotes vendidos pode ficar com
  // `quantity` desatualizado pra sempre (visto em produção com MU, inclusive
  // aqui na tela de Performance -- comparativo de carteira ATIVA vs SPY não
  // deveria nem somar, nem listar, posição já encerrada).
  const lots = rows.length
    ? await db
        .select({
          positionId: portfolioPurchasesTable.positionId,
          amount: portfolioPurchasesTable.amount,
          purchasePrice: portfolioPurchasesTable.purchasePrice,
          saleDate: portfolioPurchasesTable.saleDate,
          salePrice: portfolioPurchasesTable.salePrice,
        })
        .from(portfolioPurchasesTable)
        .where(inArray(portfolioPurchasesTable.positionId, rows.map((p) => p.id)))
    : [];
  const lotsByPosition = new Map<number, typeof lots>();
  for (const lot of lots) {
    const list = lotsByPosition.get(lot.positionId) ?? [];
    list.push(lot);
    lotsByPosition.set(lot.positionId, list);
  }

  const positions = rows
    .map((p) => {
      const positionLots = lotsByPosition.get(p.id) ?? [];
      const open = positionLots.filter((l) => !(l.saleDate && l.salePrice));
      const derived = positionLots.length > 0
        ? computeOpenLotTotals(open.map((l) => ({
            amount: Number(l.amount),
            purchasePrice: l.purchasePrice != null ? Number(l.purchasePrice) : null,
          })))
        : { quantity: Number(p.quantity), avgCost: Number(p.avgCost), investedAmount: Number(p.investedAmount) };
      return { p, derived };
    })
    .filter(({ p, derived }) => isPositionActiveFromLots(derived.quantity, lotsByPosition.get(p.id) ?? []));

  const tickers = [...new Set(positions.map(({ p }) => p.ticker)), "SPY"];

  const scriptPath = path.join(agentDir, "agent", "get_performance.py");
  const py = spawn(getPythonBin(), [scriptPath, tickers.join(",")]);

  let out = "";
  let err = "";
  py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
  py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
  py.on("close", (code) => {
    if (code !== 0) {
      res.status(500).json({ error: err || "Script failed" });
      return;
    }
    try {
      const prices: Record<string, { price: number | null; previousClose: number | null }> = JSON.parse(out);
      const data = positions.map(({ p: pos, derived }) => {
        const info = prices[pos.ticker] ?? { price: null, previousClose: null };
        const qty = derived.quantity;
        const avgCost = derived.avgCost;
        const invested = derived.investedAmount;
        const currentPrice = info.price;
        const currentValue = currentPrice != null ? qty * currentPrice : null;
        const plAbs = currentValue != null ? currentValue - invested : null;
        const plPct = plAbs != null && invested > 0 ? (plAbs / invested) * 100 : null;
        return {
          ticker: pos.ticker,
          quantity: qty,
          avgCost,
          investedAmount: invested,
          currentPrice,
          currentValue,
          plAbs,
          plPct,
          firstPurchaseDate: pos.firstPurchaseDate,
        };
      });
      const totalInvested = data.reduce((s, p) => s + p.investedAmount, 0);
      const totalValue = data.reduce((s, p) => s + (p.currentValue ?? p.investedAmount), 0);
      const totalPL = totalValue - totalInvested;
      const totalPLPct = totalInvested > 0 ? (totalPL / totalInvested) * 100 : 0;
      const spy = prices["SPY"] ?? { price: null, previousClose: null };
      const spyDayPct = (spy.price != null && spy.previousClose != null && spy.previousClose > 0)
        ? ((spy.price - spy.previousClose) / spy.previousClose) * 100
        : null;
      res.json({ positions: data, totalInvested, totalValue, totalPL, totalPLPct, spyDayPct, spyPrice: spy.price });
    } catch {
      res.status(500).json({ error: "Failed to parse script output" });
    }
  });
});

export default router;
