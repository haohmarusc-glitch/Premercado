/**
 * Background job que checa o Painel de Cenários de cada usuário com alerta
 * habilitado, a cada hora, e dispara e-mail quando a probabilidade de
 * empatar até a data-alvo cai abaixo do limiar configurado.
 *
 * Usa o MESMO núcleo matemático do frontend (@workspace/scenario-math),
 * pra nunca divergir do que a tela /cenarios mostra ao usuário. Roda o
 * cenário NEUTRO (nenhuma posição travada em caixa manualmente, setor
 * parado, volatilidade base 1x) -- é a leitura mais honesta possível sem
 * acesso ao estado dos sliders da UI, que só existe na sessão do navegador.
 *
 * Cooldown de 24h via lastFiredAt: reenvia no máximo uma vez por dia
 * enquanto a condição persistir, em vez de mandar e-mail a cada ciclo.
 */
import { eq, and, or, isNull, lt, sql } from "drizzle-orm";
import { db, scenarioAlertSettingsTable } from "@workspace/db";
import { computeScenarioMetrics } from "@workspace/scenario-math";
import { buildScenarioPositions } from "../routes/scenarios";
import { sendScenarioAlertEmail } from "./mailer";
import { state as agentState } from "./runner";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 60 * 60_000; // 1h
const COOLDOWN_MS = 24 * 60 * 60_000; // 24h

// Exportado (diferente do padrão privado dos outros checkers) pra permitir
// invocação direta em teste manual, sem precisar esperar o intervalo de 1h.
export async function checkScenarioAlerts(): Promise<void> {
  // Mesmo motivo dos outros checkers (alert-checker.ts, portfolio-alerts.ts):
  // buildScenarioPositions roda 3 subprocessos Python por usuário, não vale
  // competir por CPU/rede com o agente diário.
  if (agentState.running) {
    logger.info("Scenario alert checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const rows = await db
    .select()
    .from(scenarioAlertSettingsTable)
    .where(and(
      eq(scenarioAlertSettingsTable.enabled, true),
      or(
        isNull(scenarioAlertSettingsTable.lastFiredAt),
        lt(scenarioAlertSettingsTable.lastFiredAt, sql`now() - interval '24 hours'`),
      ),
    ));

  if (!rows.length) return;

  for (const row of rows) {
    try {
      const positions = await buildScenarioPositions(row.userId);
      if (!positions.length) continue; // sem posições ativas, nada a alertar

      const dataAlvo = new Date(row.dataAlvo + "T00:00:00");
      const m = computeScenarioMetrics(positions, {}, {}, 0, 1, dataAlvo);
      if (m.risco <= 0) continue; // tudo em caixa -- sem risco, sem sentido alertar

      const thresholdPct = Number(row.thresholdPct);
      const pEmpatePct = m.pEmpate * 100;
      if (pEmpatePct >= thresholdPct) continue; // acima do limiar, nada a fazer

      await sendScenarioAlertEmail({
        to: row.notifyEmail,
        dataAlvo: row.dataAlvo,
        thresholdPct,
        pEmpatePct,
        caixa: m.caixa,
        risco: m.risco,
        custoTotal: m.custoTotal,
      });

      await db
        .update(scenarioAlertSettingsTable)
        .set({ lastFiredAt: new Date() })
        .where(eq(scenarioAlertSettingsTable.userId, row.userId));

      logger.info({ userId: row.userId, pEmpatePct, thresholdPct }, "Scenario alert fired");
    } catch (err) {
      logger.error({ err, userId: row.userId }, "Scenario alert check failed for user");
    }
  }
}

let checkerStarted = false;

export function startScenarioAlertChecker(): void {
  if (checkerStarted) return;
  checkerStarted = true;

  async function loop(): Promise<void> {
    try {
      await checkScenarioAlerts();
    } catch (e) {
      logger.error({ e }, "Scenario alert check error");
    }
    setTimeout(loop, CHECK_INTERVAL_MS);
  }

  // primeira execução após 90s para o servidor estabilizar (depois dos
  // outros checkers, que já usam 60s)
  setTimeout(loop, 90_000);
  logger.info("Scenario alert checker started (interval: 1h)");
}
