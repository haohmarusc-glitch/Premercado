import { Router, type IRouter } from "express";
import { coalescer } from "../lib/em-voo";
import { spawnAgente } from "../lib/runner";
import { getOrCreateSettings } from "./settings";
import { logger } from "../lib/logger";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

interface Cache { data: unknown; fetchedAt: number; key: string; }
let cache: Cache | null = null;
const CACHE_TTL_MS = 60_000;

function fetchTechnicals(tickers: string[]): Promise<unknown> {
  // comVagaPython por dentro do coalescer -- ver lib/vaga-python.ts.
  return coalescer(`technicals:${tickers.join(",")}`, () => comVagaPython("technicals", () => new Promise((resolve, reject) => {
    const py = spawnAgente("get_technicals.py");
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
      try {
        return resolve(JSON.parse(out));
      } catch {
        // As pontas do stdout no log. Sem isto o erro era só "Parse error", e
        // descobrir a causa exigiu rodar o script à mão no container -- em
        // 18/08/2026, para achar um `NaN` no meio do JSON (json.dumps do
        // Python o emite; JSON.parse do Node o rejeita, e um campo derruba a
        // resposta inteira). Mesmo remédio já aplicado em analysis.ts.
        logger.error(
          { stdoutHead: out.slice(0, 500), stdoutTail: out.slice(-500), bytes: out.length,
            stderr: err.slice(-1000) },
          "technicals: stdout não é JSON",
        );
        return reject(new Error("Parse error"));
      }
    });
  })));
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
