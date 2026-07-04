import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { getOrCreateSettings } from "./settings";

const router: IRouter = Router();

function clamp(val: unknown, min: number, max: number, def: number): number {
  const n = parseFloat(String(val ?? def));
  if (isNaN(n)) return def;
  return Math.min(Math.max(n, min), max);
}

function runBacktestScript(payload: object, timeoutMs: number): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "backtest.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("Backtest timed out")); }, timeoutMs);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "Script failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse script output")); }
    });
  });
}

router.post("/backtest", async (req, res): Promise<void> => {
  const { ticker, start, end, strategy } = req.body;
  if (!ticker || !start || !end) {
    res.status(400).json({ error: "ticker, start, end are required" });
    return;
  }

  const positionFraction = clamp(req.body.positionFraction, 0.1, 1.0, 1.0);
  const commissionPct    = clamp(req.body.commissionPct,    0,   0.05, 0.001);
  const slippagePct      = clamp(req.body.slippagePct,      0,   0.05, 0.0005);

  try {
    const data = await runBacktestScript({
      ticker: String(ticker).toUpperCase(), start, end,
      strategy: strategy ?? "rsi",
      positionFraction, commissionPct, slippagePct,
    }, 90_000);
    res.json(data);
  } catch (e: unknown) {
    res.status(500).json({ error: String((e as Error)?.message ?? e) });
  }
});

// POST /backtest/basket — roda a mesma simulação pra cesta inteira de uma vez
// (default: tickers configurados em Settings), pensado pra estratégia
// "confluencia" (o sinal técnico do Screener/TrendCard sem a camada de
// notícias, que não dá pra reconstruir com fidelidade histórica).
router.post("/backtest/basket", async (req, res): Promise<void> => {
  const { start, end, strategy } = req.body;
  if (!start || !end) {
    res.status(400).json({ error: "start, end are required" });
    return;
  }

  let tickers: string[] = Array.isArray(req.body.tickers)
    ? req.body.tickers.map((t: unknown) => String(t).toUpperCase())
    : [];
  if (!tickers.length) {
    const settings = await getOrCreateSettings();
    tickers = settings.tickers;
  }
  if (!tickers.length) {
    res.json({ tickersRequested: 0, tickersOk: 0, results: [], failed: [] });
    return;
  }

  const positionFraction = clamp(req.body.positionFraction, 0.1, 1.0, 1.0);
  const commissionPct    = clamp(req.body.commissionPct,    0,   0.05, 0.001);
  const slippagePct      = clamp(req.body.slippagePct,      0,   0.05, 0.0005);

  try {
    // Cada ticker faz um fetch + simulação própria — timeout maior que o de
    // um único ticker pra cobrir a cesta inteira.
    const data = await runBacktestScript({
      tickers, start, end,
      strategy: strategy ?? "confluencia",
      positionFraction, commissionPct, slippagePct,
    }, 20_000 * Math.max(1, tickers.length));
    res.json(data);
  } catch (e: unknown) {
    res.status(500).json({ error: String((e as Error)?.message ?? e) });
  }
});

export default router;
