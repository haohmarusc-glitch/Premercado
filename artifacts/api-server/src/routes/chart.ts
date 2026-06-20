import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import { agentDir, getPythonBin } from "../lib/runner";
import { GetTickerChartQueryParams, GetTickerChartResponse } from "@workspace/api-zod";
import { logger } from "../lib/logger";

const router: IRouter = Router();

const VALID_PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "1y"];

interface CacheEntry { data: unknown; fetchedAt: number }
const cache = new Map<string, CacheEntry>();
const TTL: Record<string, number> = {
  "1d":  60_000,
  "5d":  5 * 60_000,
  "1mo": 30 * 60_000,
  "3mo": 60 * 60_000,
  "6mo": 60 * 60_000,
  "1y":  60 * 60_000,
};

function fetchChart(symbol: string, period: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const sym = symbol, per = period;
    const py = spawn(getPythonBin(), ["-m", "agent.get_chart", sym, per], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      if (err) logger.warn({ symbol: sym, period: per, stderr: err.trim() }, "get_chart stderr");
      if (code !== 0) { reject(new Error(`get_chart exited ${code}: ${err}`)); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

router.get("/tickers/chart", async (req, res): Promise<void> => {
  const parsed = GetTickerChartQueryParams.safeParse(req.query);
  if (!parsed.success) { res.status(400).json({ error: "symbol is required" }); return; }

  const symbol = (parsed.data.symbol as string).toUpperCase();
  const period = VALID_PERIODS.includes(parsed.data.period as string)
    ? (parsed.data.period as string)
    : "1d";

  const key = `${symbol}:${period}`;
  const now = Date.now();
  const cached = cache.get(key);
  if (cached && now - cached.fetchedAt < (TTL[period] ?? 60_000)) {
    res.json(GetTickerChartResponse.parse(cached.data));
    return;
  }

  try {
    const data = await fetchChart(symbol, period);
    cache.set(key, { data, fetchedAt: now });
    res.json(GetTickerChartResponse.parse(data));
  } catch (err) {
    logger.error({ err }, "Failed to fetch chart data");
    res.status(500).json({ error: "Failed to fetch chart" });
  }
});

export default router;
