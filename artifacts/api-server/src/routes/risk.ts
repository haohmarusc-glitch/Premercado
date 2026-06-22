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

export default router;
