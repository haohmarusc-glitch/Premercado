import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { eq, inArray } from "drizzle-orm";
import { db, portfolioPositionsTable, scenarioParamsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { isActivePosition } from "../lib/portfolio-math";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// Seed do Painel de Cenários (ver migração 0022_scenario_params.sql) --
// usado como fallback quando um ticker da carteira ainda não tem linha em
// scenario_params (posição nova, ou fora da cesta setorial original).
const DEFAULT_VOL = 0.5;
const DEFAULT_BETA = 1.0;

function runScript(scriptName: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", scriptName);
    const py = spawn(getPythonBin(), [scriptPath, ...args]);
    let out = "";
    let err = "";
    const timer = setTimeout(() => { py.kill("SIGTERM"); reject(new Error(`${scriptName} timeout`)); }, 60_000);
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) { reject(new Error(err || `${scriptName} failed`)); return; }
      resolve(out);
    });
  });
}

// "2026-08-26" -> "26/08" (formato usado na agenda/eventos do painel)
function toBrShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return "—";
  return `${m[3]}/${m[2]}`;
}

interface ScenarioPosition {
  t: string;
  nome: string;
  value: number;
  cost: number;
  vol: number;
  beta: number;
  evento: string;
}

router.get("/scenarios/positions", async (req, res): Promise<void> => {
  try {
    const rows = await db
      .select()
      .from(portfolioPositionsTable)
      .where(eq(portfolioPositionsTable.userId, req.userId!));

    // Só posições ativas (ainda possuídas) e não-ETF -- ETFs de caixa (ex.:
    // SGOV) não têm volatilidade/beta setorial que faça sentido no modelo.
    const active = rows.filter((p) => !p.isEtf && isActivePosition(p.quantity));
    if (!active.length) {
      res.json([]);
      return;
    }

    const tickers = [...new Set(active.map((p) => p.ticker))];

    const [paramsRows, perfOut, earnOut] = await Promise.all([
      db.select().from(scenarioParamsTable).where(inArray(scenarioParamsTable.ticker, tickers)),
      runScript("get_performance.py", [tickers.join(",")]).catch((err: Error) => {
        logger.warn({ err }, "Scenario positions: falha ao buscar preços atuais, usando custo como fallback");
        return "{}";
      }),
      runScript("get_earnings.py", [tickers.join(",")]).catch((err: Error) => {
        logger.warn({ err }, "Scenario positions: falha ao buscar datas de resultado");
        return "[]";
      }),
    ]);

    const paramsMap = new Map(paramsRows.map((r) => [r.ticker, r]));
    let prices: Record<string, { price: number | null }> = {};
    let earnings: Array<{ ticker: string; name: string; earningsDate: string | null }> = [];
    try { prices = JSON.parse(perfOut); } catch { /* fica no fallback abaixo */ }
    try { earnings = JSON.parse(earnOut); } catch { /* fica no fallback abaixo */ }
    const earningsMap = new Map(earnings.map((e) => [e.ticker, e]));

    const data: ScenarioPosition[] = active.map((p) => {
      // Driver pg devolve `numeric` como string -- Number() antes de entrar
      // no modelo, senão a matriz de covariância vira NaN silenciosamente.
      const qty = Number(p.quantity);
      const cost = Number(p.investedAmount);
      const price = prices[p.ticker]?.price;
      const value = price != null ? Math.round(qty * price * 100) / 100 : cost;

      const params = paramsMap.get(p.ticker);
      const earn = earningsMap.get(p.ticker);

      return {
        t: p.ticker,
        nome: earn?.name ?? p.ticker,
        value,
        cost,
        vol: params ? Number(params.volAnnual) : DEFAULT_VOL,
        beta: params ? Number(params.betaSector) : DEFAULT_BETA,
        evento: toBrShortDate(earn?.earningsDate),
      };
    });

    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed to build scenario positions");
    res.status(500).json({ error: "Failed to load scenario positions" });
  }
});

export default router;
