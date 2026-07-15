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

// get_market_alerts_snapshot.py precisa rodar via `-m agent.xxx` (import
// absoluto do pacote), diferente de runPython() acima (caminho direto do
// script) -- market_alerts.py faz `from .cache import cached`, import
// relativo que só resolve nesse contexto de pacote. Mesmo padrão de
// routes/quotes.ts (get_quotes.py) e routes/chart.ts (get_chart.py).
function runMarketAlertsSnapshot(payload: object): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_market_alerts_snapshot"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // 120s (mesmo timeout de runPython() acima) -- run_all_alerts faz MUITAS
    // chamadas de rede por conta própria (peers, macro, e por ticker:
    // overbought/volume/candles/earnings/analistas/geopolítico/halt), além
    // da busca de manchetes que já é paralela.
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
makeTickerRoute("/trend", "get_trend.py", "trend");
makeTickerRoute("/options", "get_options_chain.py", "options");
makeTickerRoute("/news", "get_news_feed.py", "news", { maxItems: 5 });
// Congress trading (Quiver Quant) + dark pool (Unusual Whales) — cada seção
// funciona só se a env var de chave correspondente estiver configurada
// (QUIVER_API_KEY / UNUSUAL_WHALES_API_KEY); sem chave, volta
// {configured: false} em vez de erro.
makeTickerRoute("/alt-data", "get_alt_data.py", "alt-data");

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

// Filings 13F de gestores institucionais acompanhados — no tickers, mesmo
// cache de 60s dos demais endpoints desta rota (o dado em si só muda a cada
// trimestre, o cache aqui é só pra não bater na SEC a cada clique).
router.get("/institutional-filings", async (_req, res): Promise<void> => {
  try {
    const hit = cached("institutional-filings", "_");
    if (hit) { res.json(hit); return; }
    const data = await runPython("get_institutional_filings.py", {});
    setCache("institutional-filings", "_", data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /institutional-filings");
    res.status(500).json({ error: "Failed to fetch institutional filings" });
  }
});

// Snapshot ao vivo dos alertas de market_alerts.py (setor, macro, técnico,
// geopolítico -- inclui as categorias de risco macro: petróleo, Taiwan,
// Irã/Ormuz, Coreia do Norte, independência do Fed, rating soberano) pro
// card "Alertas de Mercado" do Dashboard. NÃO passa pelo loop do agente/LLM
// -- é o mesmo check_market_alerts que o agente chama, só que direto via
// HTTP, sem custo de token.
router.get("/market-alerts", async (_req, res): Promise<void> => {
  try {
    const tickers = await resolveTickers(String(_req.query.tickers ?? ""));
    const key = tickers.join(",");
    const hit = cached("market-alerts", key);
    if (hit) { res.json(hit); return; }
    const data = await runMarketAlertsSnapshot({ tickers });
    setCache("market-alerts", key, data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /market-alerts");
    res.status(500).json({ error: "Failed to fetch market alerts" });
  }
});

export default router;
