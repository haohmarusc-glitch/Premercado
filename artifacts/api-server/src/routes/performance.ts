import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { db, portfolioPositionsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { asc } from "drizzle-orm";

const router: IRouter = Router();

router.get("/performance", async (_req, res): Promise<void> => {
  const positions = await db.select().from(portfolioPositionsTable).orderBy(asc(portfolioPositionsTable.createdAt));
  const tickers = [...new Set(positions.map((p) => p.ticker)), "SPY"];

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
      const data = positions.map((pos) => {
        const info = prices[pos.ticker] ?? { price: null, previousClose: null };
        const qty = Number(pos.quantity);
        const avgCost = Number(pos.avgCost);
        const invested = Number(pos.investedAmount);
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
