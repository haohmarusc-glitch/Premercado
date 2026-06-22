import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { getOrCreateSettings } from "./settings";
import { logger } from "../lib/logger";

const router: IRouter = Router();

interface Cache { data: unknown; fetchedAt: number; key: string; }
let cache: Cache | null = null;
const CACHE_TTL_MS = 60_000;

function fetchTechnicals(tickers: string[]): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_technicals.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 90_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  });
}

router.get("/technicals", async (req, res): Promise<void> => {
  try {
    let tickers: string[];
    const raw = String(req.query.tickers ?? "").trim();
    if (raw) {
      tickers = raw.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    } else {
      const settings = await getOrCreateSettings();
      tickers = settings.tickers;
    }
    if (!tickers.length) { res.json({ items: [] }); return; }

    const key = tickers.join(",");
    const now = Date.now();
    if (cache && cache.key === key && now - cache.fetchedAt < CACHE_TTL_MS) {
      res.json(cache.data);
      return;
    }

    const data = await fetchTechnicals(tickers);
    cache = { data, fetchedAt: now, key };
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed to fetch technicals");
    res.status(500).json({ error: "Failed to fetch technicals" });
  }
});

export default router;
