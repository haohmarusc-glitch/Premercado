/**
 * Background job que recalcula vol_annual/beta_sector de scenario_params uma
 * vez por dia, a partir do histórico real de preços (ver
 * get_scenario_params.py) -- em vez dos valores fixos digitados à mão na
 * migração 0022, que nunca mais eram atualizados. scenario_params é global
 * (chave só o ticker, sem user_id, ver ensure-schema.ts), então varre as
 * posições ativas de TODOS os usuários numa run só, mesmo padrão de
 * portfolio-alerts.ts.
 *
 * Também recalcula sectorMovePct (a premissa "movimento do setor até a
 * data-alvo" do slider do Painel de Cenários) 1x/dia pra CADA usuário com
 * data-alvo configurada, a partir do momentum real do benchmark (SMH)
 * escalado pelos dias restantes até a data-alvo de cada um -- persistido em
 * scenario_alert_settings.sector_move_pct, em vez de existir só como estado
 * local do slider na sessão do navegador (que exigia clique manual em
 * "aplicar sugestão" e nunca era usado pelo checker em background,
 * ver scenario-alert-checker.ts).
 */
import { eq, notInArray } from "drizzle-orm";
import { db, portfolioPositionsTable, scenarioParamsTable, scenarioAlertSettingsTable, sectorMomentumTable } from "@workspace/db";
import { diasAteAlvo } from "@workspace/scenario-math";
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
  // Sem nenhuma posição ativa, sai antes -- e de propósito NÃO limpa os
  // órfãos aqui. `notInArray(ticker, [])` apagaria a tabela inteira, e
  // "carteira vazia" é mais provável de ser anomalia momentânea (migração,
  // consulta que voltou vazia) do que estado real. A limpeza só roda quando
  // existe ao menos um ticker ativo para comparar, que é o caso do AVGO.
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

    await refreshSectorMovePctForUsers(mo.momentumAnnualPct);
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

  // Órfãos: linha cujo ticker saiu da lista ativa (posição zerada). Sem esta
  // limpeza a linha ficava para sempre, e o Painel de Cenários seguia
  // carregando vol/beta de um papel que o usuário não tem mais -- visto em
  // produção com AVGO (auditoria 17/08/2026), que precisou ser apagado à mão.
  //
  // DELETE simples em vez de marcar stale: o próprio ciclo recria a linha se
  // a posição voltar, então não há nada a preservar. O dado é derivado, não
  // é histórico.
  const removidos = await db
    .delete(scenarioParamsTable)
    .where(notInArray(scenarioParamsTable.ticker, tickers))
    .returning({ ticker: scenarioParamsTable.ticker });
  if (removidos.length) {
    logger.info(
      { removidos: removidos.map((r) => r.ticker) },
      "Scenario params checker: linhas removidas (ticker fora da lista ativa)",
    );
  }

  logger.info({ updated, total: tickers.length }, "Scenario params refreshed");
}

// Mesma extrapolação que o botão "aplicar sugestão" do slider já fazia no
// frontend (momentum anualizado × fração do ano até a data-alvo), mas
// aplicada automaticamente pra CADA usuário aqui -- a data-alvo é por
// usuário (scenario_alert_settings), então mesmo com um `momentumAnnualPct`
// global (1 benchmark, SMH), o valor final escalado difere por usuário.
// Clamp em ±40 pra bater com os limites do slider (min/max do componente em
// cenarios.tsx) -- o valor persistido tem que caber no range que o usuário
// enxerga e pode ajustar manualmente por cima.
async function refreshSectorMovePctForUsers(momentumAnnualPct: number): Promise<void> {
  const rows = await db
    .select({ userId: scenarioAlertSettingsTable.userId, dataAlvo: scenarioAlertSettingsTable.dataAlvo })
    .from(scenarioAlertSettingsTable);
  if (!rows.length) return;

  for (const row of rows) {
    const dataAlvo = new Date(row.dataAlvo + "T00:00:00");
    const dias = diasAteAlvo(dataAlvo);
    const sectorMovePct = Math.max(-40, Math.min(40, Math.round(momentumAnnualPct * (dias / 365))));
    await db
      .update(scenarioAlertSettingsTable)
      .set({ sectorMovePct, sectorMoveUpdatedAt: new Date() })
      .where(eq(scenarioAlertSettingsTable.userId, row.userId));
  }
}

let checkerStarted = false;

export function startScenarioParamsChecker(): void {
  if (checkerStarted) return;
  checkerStarted = true;

  async function loop(): Promise<void> {
    try {
      await refreshScenarioParams();
    } catch (err) {
      // `err`, não `e` -- ver comentário em alert-checker.ts::dispararCiclo.
      logger.error({ err }, "Scenario params check error");
    }
    setTimeout(loop, CHECK_INTERVAL_MS);
  }

  // primeira execução após 150s (depois dos outros checkers: 60s, 60s, 90s)
  setTimeout(loop, 150_000);
  logger.info("Scenario params checker started (interval: 24h)");
}
