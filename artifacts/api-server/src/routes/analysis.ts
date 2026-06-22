import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { getOrCreateSettings } from "./settings";
import { logger } from "../lib/logger";

const router: IRouter = Router();

function runPython(script: string, payload: object): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", script);
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 120_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  });
}

async function resolveTickers(raw: string): Promise<string[]> {
  const trimmed = raw.trim();
  if (trimmed) return trimmed.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
  const settings = await getOrCreateSettings();
  return settings.tickers;
}

// Simple per-endpoint cache keyed by ticker list
const caches: Record<string, { key: string; data: unknown; at: number }> = {};
function cached(name: string, key: string): unknown | null {
  const c = caches[name];
  if (c && c.key === key && Date.now() - c.at < 60_000) return c.data;
  return null;
}
function setCache(name: string, key: string, data: unknown) {
  caches[name] = { key, data, at: Date.now() };
}

function makeTickerRoute(routePath: string, script: string, cacheName: string, extra: object = {}) {
  router.get(routePath, async (req, res): Promise<void> => {
    try {
      const tickers = await resolveTickers(String(req.query.tickers ?? ""));
      if (!tickers.length) { res.json({ items: [] }); return; }
      const key = tickers.join(",");
      const hit = cached(cacheName, key);
      if (hit) { res.json(hit); return; }
      const data = await runPython(script, { tickers, ...extra });
      setCache(cacheName, key, data);
      res.json(data);
    } catch (err) {
      logger.error({ err }, `Failed: ${routePath}`);
      res.status(500).json({ error: `Failed to fetch ${routePath}` });
    }
  });
}

makeTickerRoute("/fundamentals", "get_fundamentals.py", "fundamentals");
makeTickerRoute("/options", "get_options_chain.py", "options");
makeTickerRoute("/news", "get_news_feed.py", "news", { maxItems: 5 });

// Macro — no tickers, single cache
router.get("/macro", async (_req, res): Promise<void> => {
  try {
    const hit = cached("macro", "_");
    if (hit) { res.json(hit); return; }
    const data = await runPython("get_macro.py", {});
    setCache("macro", "_", data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /macro");
    res.status(500).json({ error: "Failed to fetch macro" });
  }
});

export default router;
