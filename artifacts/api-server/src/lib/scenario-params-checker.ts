/**
 * Background job que recalcula vol_annual/beta_sector de scenario_params uma
 * vez por dia, a partir do histórico real de preços (ver
 * get_scenario_params.py) -- em vez dos valores fixos digitados à mão na
 * migração 0022, que nunca mais eram atualizados. scenario_params é global
 * (chave só o ticker, sem user_id, ver ensure-schema.ts), então varre as
 * posições ativas de TODOS os usuários numa run só, mesmo padrão de
 * portfolio-alerts.ts.
 */
import { db, portfolioPositionsTable, scenarioParamsTable, sectorMomentumTable } from "@workspace/db";
import { isActivePosition } from "./portfolio-math";
import { runScript } from "../routes/scenarios";
import { state as agentState } from "./runner";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 24 * 60 * 60_000; // 24h

// ETF setorial usado como benchmark do beta -- SMH (semicondutores) é o mais
// usado no resto do agente pra esse setor (ver BELLWETHERS em
// market_alerts.py, SECTOR_ETFS em get_macro.py).
const BENCHMARK = "SMH";

interface ScenarioParamResult {
  volAnnual?: number;
  betaSector?: number;
  daysUsed?: number;
  error?: string;
}

interface SectorMomentumResult {
  benchmark: string;
  momentumAnnualPct: number;
  lookbackDays: number;
}

interface ScenarioParamsScriptOutput {
  params: Record<string, ScenarioParamResult>;
  sectorMomentum: SectorMomentumResult | null;
}

export async function refreshScenarioParams(): Promise<void> {
  // Mesmo motivo dos outros checkers: get_scenario_params.py baixa histórico
  // via yfinance, não vale competir por CPU/rede com o agente diário.
  if (agentState.running) {
    logger.info("Scenario params checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const rows = await db
    .select({ ticker: portfolioPositionsTable.ticker, isEtf: portfolioPositionsTable.isEtf, quantity: portfolioPositionsTable.quantity })
    .from(portfolioPositionsTable);
  const tickers = [...new Set(rows.filter((r) => !r.isEtf && isActivePosition(r.quantity)).map((r) => r.ticker))];
  if (!tickers.length) return;

  let out: string;
  try {
    out = await runScript("get_scenario_params.py", [tickers.join(","), BENCHMARK]);
  } catch (err) {
    logger.error({ err, tickers }, "Scenario params checker: falha ao rodar get_scenario_params.py");
    return;
  }

  let parsed: ScenarioParamsScriptOutput;
  try {
    parsed = JSON.parse(out);
  } catch (err) {
    logger.error({ err, out }, "Scenario params checker: resposta inválida do script");
    return;
  }

  if (parsed.sectorMomentum) {
    const mo = parsed.sectorMomentum;
    await db
      .insert(sectorMomentumTable)
      .values({ benchmark: mo.benchmark, momentumAnnualPct: mo.momentumAnnualPct, lookbackDays: mo.lookbackDays })
      .onConflictDoUpdate({
        target: sectorMomentumTable.benchmark,
        set: { momentumAnnualPct: mo.momentumAnnualPct, lookbackDays: mo.lookbackDays, updatedAt: new Date() },
      });
  }

  let updated = 0;
  const skipped: string[] = [];
  for (const ticker of tickers) {
    const r = parsed.params[ticker];
    // Ticker sem número válido (histórico insuficiente, erro de rede etc.)
    // fica de fora do upsert -- mantém o valor anterior (ou o fallback
    // DEFAULT_VOL/DEFAULT_BETA de buildScenarioPositions) em vez de gravar
    // lixo por cima de um dado bom.
    if (!r || r.error || typeof r.volAnnual !== "number" || typeof r.betaSector !== "number") {
      skipped.push(ticker);
      continue;
    }
    await db
      .insert(scenarioParamsTable)
      .values({ ticker, volAnnual: r.volAnnual, betaSector: r.betaSector })
      .onConflictDoUpdate({
        target: scenarioParamsTable.ticker,
        set: { volAnnual: r.volAnnual, betaSector: r.betaSector, updatedAt: new Date() },
      });
    updated++;
  }

  if (skipped.length) {
    logger.warn({ skipped }, "Scenario params checker: tickers sem histórico suficiente, mantido valor anterior");
  }
  logger.info({ updated, total: tickers.length }, "Scenario params refreshed");
}

let checkerStarted = false;

export function startScenarioParamsChecker(): void {
  if (checkerStarted) return;
  checkerStarted = true;

  async function loop(): Promise<void> {
    try {
      await refreshScenarioParams();
    } catch (e) {
      logger.error({ e }, "Scenario params check error");
    }
    setTimeout(loop, CHECK_INTERVAL_MS);
  }

  // primeira execução após 150s (depois dos outros checkers: 60s, 60s, 90s)
  setTimeout(loop, 150_000);
  logger.info("Scenario params checker started (interval: 24h)");
}
