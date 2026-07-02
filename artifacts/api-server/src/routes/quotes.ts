import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import { agentDir, getPythonBin } from "../lib/runner";
import { getOrCreateSettings } from "./settings";
import { GetTickerQuotesResponse, GetFxUsdBrlResponse } from "@workspace/api-zod";
import { logger } from "../lib/logger";

const router: IRouter = Router();

interface QuoteCache {
  data: unknown[];
  fetchedAt: number;
}
let cache: QuoteCache | null = null;
const CACHE_TTL_MS = 60_000;

function fetchQuotes(tickers: string[]): Promise<unknown[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(
      getPythonBin(),
      ["-m", "agent.get_quotes", ...tickers],
      {
        cwd: agentDir,
        env: { ...process.env, PYTHONPATH: agentDir },
      },
    );

    let stdout = "";
    let stderr = "";

    const timer = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("get_quotes timeout")); }, 90_000);

    py.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    py.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`get_quotes exited ${code}: ${stderr}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (e) {
        reject(new Error(`Failed to parse quotes JSON: ${stdout}`));
      }
    });
  });
}

// GET /fx/usdbrl — cotação USD→BRL (via Yahoo "BRL=X") para converter
// posições da B3 nos totais em dólar da carteira
let fxCache: { rate: number; fetchedAt: number } | null = null;
const FX_CACHE_TTL_MS = 5 * 60_000;

router.get("/fx/usdbrl", async (_req, res): Promise<void> => {
  const now = Date.now();
  if (fxCache && now - fxCache.fetchedAt < FX_CACHE_TTL_MS) {
    res.json(GetFxUsdBrlResponse.parse({ rate: fxCache.rate }));
    return;
  }
  try {
    const data = (await fetchQuotes(["BRL=X"])) as Array<{ price?: number | null }>;
    const rate = data[0]?.price;
    if (rate == null || rate <= 0) {
      res.status(502).json({ error: "FX rate unavailable" });
      return;
    }
    fxCache = { rate, fetchedAt: now };
    res.json(GetFxUsdBrlResponse.parse({ rate }));
  } catch (err) {
    logger.error({ err }, "Failed to fetch USDBRL rate");
    res.status(500).json({ error: "Failed to fetch FX rate" });
  }
});

router.get("/tickers/quotes", async (req, res): Promise<void> => {
  const now = Date.now();

  if (cache && now - cache.fetchedAt < CACHE_TTL_MS) {
    res.json(GetTickerQuotesResponse.parse(cache.data));
    return;
  }

  try {
    const settings = await getOrCreateSettings();
    const tickers = settings.tickers;

    if (!tickers.length) {
      res.json([]);
      return;
    }

    const data = await fetchQuotes(tickers);
    cache = { data, fetchedAt: now };
    res.json(GetTickerQuotesResponse.parse(data));
  } catch (err) {
    logger.error({ err }, "Failed to fetch ticker quotes");
    res.status(500).json({ error: "Failed to fetch quotes" });
  }
});

export default router;
