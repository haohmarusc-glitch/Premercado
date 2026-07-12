import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { db, portfolioPositionsTable } from "@workspace/db";

const router: IRouter = Router();

function runPython(payload: object): Promise<object> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "risk_manager.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  });
}

router.post("/risk/position-size", async (req, res): Promise<void> => {
  const { accountSize, riskPct, entry, stop } = req.body;
  if (!accountSize || !riskPct || !entry || !stop) {
    res.status(400).json({ error: "accountSize, riskPct, entry, stop required" }); return;
  }
  try {
    res.json(await runPython({ action: "position_size", accountSize, riskPct, entry, stop }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

router.post("/risk/risk-reward", async (req, res): Promise<void> => {
  const { entry, stop, target } = req.body;
  if (!entry || !stop || !target) {
    res.status(400).json({ error: "entry, stop, target required" }); return;
  }
  try {
    res.json(await runPython({ action: "risk_reward", entry, stop, target }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

router.post("/risk/stop-distance", async (req, res): Promise<void> => {
  const { ticker, period, atrMultiplier } = req.body;
  if (!ticker) { res.status(400).json({ error: "ticker required" }); return; }
  try {
    res.json(await runPython({ action: "stop_distance", ticker, period: period ?? "3mo", atrMultiplier: atrMultiplier ?? 2.0 }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

router.get("/risk/portfolio-exposure", async (req, res): Promise<void> => {
  try {
    const positions = await db.select().from(portfolioPositionsTable);
    const payload = positions.map((p) => ({
      ticker: p.ticker,
      investedAmount: p.investedAmount,
    }));
    res.json(await runPython({ action: "portfolio_exposure", positions: payload }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

// GET /risk/portfolio-correlation — correlação de Pearson entre os retornos
// diários das posições atuais (peso em dólar não conta como diversificação
// se os ativos se movem juntos).
// POST /risk/intraday-beta — beta deslizante (rolling) de hedgeTicker em
// relação a baseTicker (ex.: SKHY vs NVDA) e alocação sugerida em
// hedgeTicker pra igualar a exposição de volatilidade de targetCapital
// investido em baseTicker. Pensado pra pares recém-listados sem histórico
// diário suficiente ainda (ver .agents/memory/skhy-ipo-monitoring.md).
router.post("/risk/intraday-beta", async (req, res): Promise<void> => {
  const { baseTicker, hedgeTicker, targetCapital, interval, window, period, winsorizeStd } = req.body;
  if (!baseTicker || !hedgeTicker || !targetCapital) {
    res.status(400).json({ error: "baseTicker, hedgeTicker, targetCapital required" }); return;
  }
  try {
    res.json(await runPython({
      action: "intraday_beta",
      baseTicker, hedgeTicker, targetCapital,
      interval: interval ?? "5m",
      window: window ?? 24,
      period: period ?? "1d",
      winsorizeStd: winsorizeStd ?? 3.0,
    }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

router.get("/risk/portfolio-correlation", async (req, res): Promise<void> => {
  try {
    const positions = await db.select({ ticker: portfolioPositionsTable.ticker }).from(portfolioPositionsTable);
    const tickers = [...new Set(positions.map((p) => p.ticker))];
    if (tickers.length < 2) {
      res.json({ error: "Precisa de pelo menos 2 posições na carteira" });
      return;
    }
    const period = typeof req.query.period === "string" ? req.query.period : "6mo";
    res.json(await runPython({ action: "correlation", tickers, period }));
  } catch (e: unknown) {
    res.status(500).json({ error: String(e) });
  }
});

export default router;
