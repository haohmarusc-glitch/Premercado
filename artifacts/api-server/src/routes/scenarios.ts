import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { eq, inArray } from "drizzle-orm";
import { db, portfolioPositionsTable, portfolioPurchasesTable, scenarioParamsTable } from "@workspace/db";
import type { ScenarioPosition } from "@workspace/scenario-math";
import { getPythonBin, agentDir } from "../lib/runner";
import { computeOpenLotTotals, isPositionActiveFromLots } from "../lib/portfolio-math";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// Seed do Painel de Cenários (ver migração 0022_scenario_params.sql) --
// usado como fallback quando um ticker da carteira ainda não tem linha em
// scenario_params (posição nova, ou fora da cesta setorial original).
const DEFAULT_VOL = 0.5;
const DEFAULT_BETA = 1.0;

export function runScript(scriptName: string, args: string[]): Promise<string> {
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

// earnings_reaction_analysis.py recebe payload por stdin (não argv), mesmo
// padrão de routes/earnings-reaction.ts.
function runStdinScript(scriptName: string, payload: object, timeoutMs = 60_000): Promise<string> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", scriptName);
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    const timer = setTimeout(() => { py.kill("SIGTERM"); reject(new Error(`${scriptName} timeout`)); }, timeoutMs);
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

// Monta as posições ativas do usuário no formato do Painel de Cenários --
// exportado pra ser reaproveitado tanto pela rota HTTP abaixo quanto pelo
// checker de alerta em background (lib/scenario-alert-checker.ts), que
// precisa dos mesmos dados (preço, custo, vol/beta, salto de earnings) sem
// depender de uma sessão HTTP.
export async function buildScenarioPositions(userId: number): Promise<ScenarioPosition[]> {
  const rows = await db
    .select()
    .from(portfolioPositionsTable)
    .where(eq(portfolioPositionsTable.userId, userId));

  const nonEtf = rows.filter((p) => !p.isEtf);
  if (!nonEtf.length) return [];

  // Ativo/vendido é decidido pelos lotes reais (portfolio_purchases), não
  // pelo campo `quantity` armazenado -- ver isPositionActiveFromLots. Sem
  // isso, uma posição com todos os lotes vendidos mas `quantity`
  // desatualizado (PUT /portfolio/:id edita esse campo direto, sem
  // recalcular a partir dos lotes) ficava presa no Painel de Cenários pra
  // sempre (visto em produção com MU).
  const lots = await db
    .select({
      positionId: portfolioPurchasesTable.positionId,
      amount: portfolioPurchasesTable.amount,
      purchasePrice: portfolioPurchasesTable.purchasePrice,
      saleDate: portfolioPurchasesTable.saleDate,
      salePrice: portfolioPurchasesTable.salePrice,
    })
    .from(portfolioPurchasesTable)
    .where(inArray(portfolioPurchasesTable.positionId, nonEtf.map((p) => p.id)));
  const lotsByPosition = new Map<number, typeof lots>();
  for (const lot of lots) {
    const list = lotsByPosition.get(lot.positionId) ?? [];
    list.push(lot);
    lotsByPosition.set(lot.positionId, list);
  }

  // Só posições ativas (ainda possuídas) e não-ETF -- ETFs de caixa (ex.:
  // SGOV) não têm volatilidade/beta setorial que faça sentido no modelo.
  // `derived` traz quantity/investedAmount recalculados dos lotes ABERTOS
  // (mesma lógica de recomputePosition em routes/portfolio.ts) -- só cai de
  // volta pro campo armazenado quando a posição não tem NENHUM lote
  // registrado ainda (ver isPositionActiveFromLots).
  const active = nonEtf
    .map((p) => {
      const positionLots = lotsByPosition.get(p.id) ?? [];
      const open = positionLots.filter((l) => !(l.saleDate && l.salePrice));
      const derived = positionLots.length > 0
        ? computeOpenLotTotals(open.map((l) => ({
            amount: Number(l.amount),
            purchasePrice: l.purchasePrice != null ? Number(l.purchasePrice) : null,
          })))
        : { quantity: Number(p.quantity), avgCost: 0, investedAmount: Number(p.investedAmount) };
      return { p, derived };
    })
    .filter(({ p, derived }) => isPositionActiveFromLots(derived.quantity, lotsByPosition.get(p.id) ?? []));
  if (!active.length) return [];

  const tickers = [...new Set(active.map(({ p }) => p.ticker))];

  const [paramsRows, perfOut, earnOut, reactionOut] = await Promise.all([
    db.select().from(scenarioParamsTable).where(inArray(scenarioParamsTable.ticker, tickers)),
    runScript("get_performance.py", [tickers.join(",")]).catch((err: Error) => {
      logger.warn({ err }, "Scenario positions: falha ao buscar preços atuais, usando custo como fallback");
      return "{}";
    }),
    runScript("get_earnings.py", [tickers.join(",")]).catch((err: Error) => {
      logger.warn({ err }, "Scenario positions: falha ao buscar datas de resultado");
      return "[]";
    }),
    // Reação histórica a earnings -- usada só pro "salto" de volatilidade
    // no dia do balanço (ver ScenarioPosition.jumpStdPct). Falha aqui
    // degrada graciosamente pro modelo de difusão pura (sem salto), não
    // derruba a chamada inteira.
    runStdinScript("earnings_reaction_analysis.py", { tickers, lookback: 8 }, 12_000 * Math.max(1, tickers.length))
      .catch((err: Error) => {
        logger.warn({ err }, "Scenario positions: falha ao buscar reação histórica a earnings");
        return "[]";
      }),
  ]);

  const paramsMap = new Map(paramsRows.map((r) => [r.ticker, r]));
  let prices: Record<string, { price: number | null }> = {};
  let earnings: Array<{ ticker: string; name: string; earningsDate: string | null }> = [];
  let reactions: Array<{ ticker: string; summary?: { close_pct_std: number | null; close_pct_abs_mean: number } }> = [];
  try { prices = JSON.parse(perfOut); } catch { /* fica no fallback abaixo */ }
  try { earnings = JSON.parse(earnOut); } catch { /* fica no fallback abaixo */ }
  try { reactions = JSON.parse(reactionOut); } catch { /* fica no fallback abaixo */ }
  const earningsMap = new Map(earnings.map((e) => [e.ticker, e]));
  const reactionMap = new Map(reactions.map((r) => [r.ticker, r]));

  return active.map(({ p, derived }) => {
    // qty/cost já vêm recalculados dos lotes abertos (ver `derived` acima) --
    // Number() só continua necessário pro que ainda lê direto de p.
    const qty = derived.quantity;
    const cost = derived.investedAmount;
    const price = prices[p.ticker]?.price;
    const value = price != null ? Math.round(qty * price * 100) / 100 : cost;

    const params = paramsMap.get(p.ticker);
    const earn = earningsMap.get(p.ticker);
    const reaction = reactionMap.get(p.ticker)?.summary;
    // std precisa de ≥2 eventos históricos pra existir -- com só 1 evento
    // (ticker recém-listado, ex. SKHY) usa a magnitude média absoluta como
    // proxy conservador em vez de descartar o salto inteiramente.
    const jumpStdPct = reaction ? (reaction.close_pct_std ?? reaction.close_pct_abs_mean ?? null) : null;

    return {
      t: p.ticker,
      nome: earn?.name ?? p.ticker,
      value,
      cost,
      vol: params ? Number(params.volAnnual) : DEFAULT_VOL,
      beta: params ? Number(params.betaSector) : DEFAULT_BETA,
      evento: toBrShortDate(earn?.earningsDate),
      eventoISO: earn?.earningsDate ?? null,
      jumpStdPct,
    };
  });
}

router.get("/scenarios/positions", async (req, res): Promise<void> => {
  try {
    const data = await buildScenarioPositions(req.userId!);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed to build scenario positions");
    res.status(500).json({ error: "Failed to load scenario positions" });
  }
});

export default router;
