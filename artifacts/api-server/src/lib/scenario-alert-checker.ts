/**
 * Background job que roda a cada hora sobre o Painel de Cenários de cada
 * usuário com data-alvo configurada (uma linha por usuário em
 * scenario_alert_settings), fazendo três coisas por ciclo:
 *
 * 1. Snapshot diário (idempotente -- upsert por dia): registra a pEmpate do
 *    dia, calculada com preço de mercado real, alimentando o termômetro de
 *    confirmação da tela /cenarios (histórico de "quantos dias a chance de
 *    empatar ficou acima do limiar").
 * 2. Resolução do ciclo: quando a data-alvo já passou e ainda não existe uma
 *    linha em scenario_resolutions pra ela, fecha o ciclo com o resultado
 *    final (bateu ou não) -- depois disso o ciclo fica congelado até o
 *    usuário definir uma nova data-alvo.
 * 3. Alerta por e-mail: dispara quando a probabilidade de empatar cai abaixo
 *    do limiar configurado, com cooldown de 24h via lastFiredAt (só se
 *    enabled=true e o ciclo ainda não foi resolvido).
 *
 * Usa o MESMO núcleo matemático do frontend (@workspace/scenario-math), pra
 * nunca divergir do que a tela /cenarios mostra ao usuário. Roda o cenário
 * NEUTRO (nenhuma posição travada em caixa manualmente, setor parado,
 * volatilidade base 1x) -- é a leitura mais honesta possível sem acesso ao
 * estado dos sliders da UI, que só existe na sessão do navegador.
 */
import { eq, and } from "drizzle-orm";
import { db, scenarioAlertSettingsTable, scenarioSnapshotsTable, scenarioResolutionsTable } from "@workspace/db";
import { computeScenarioMetrics, cicloBateu } from "@workspace/scenario-math";
import { buildScenarioPositions } from "../routes/scenarios";
import { sendScenarioAlertEmail } from "./mailer";
import { state as agentState } from "./runner";
import { logger } from "./logger";

const CHECK_INTERVAL_MS = 60 * 60_000; // 1h
const COOLDOWN_MS = 24 * 60 * 60_000; // 24h

function hojeISO(): string {
  return new Date().toISOString().slice(0, 10);
}

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

  // Todas as linhas, não só as com enabled=true: o snapshot diário e a
  // resolução do ciclo acontecem independente do usuário ter habilitado o
  // e-mail -- enabled só filtra o envio de alerta mais abaixo.
  const rows = await db.select().from(scenarioAlertSettingsTable);
  if (!rows.length) return;

  const hoje = hojeISO();

  for (const row of rows) {
    try {
      const [resolucaoExistente] = await db
        .select({ id: scenarioResolutionsTable.id })
        .from(scenarioResolutionsTable)
        .where(and(
          eq(scenarioResolutionsTable.userId, row.userId),
          eq(scenarioResolutionsTable.dataAlvo, row.dataAlvo),
        ));
      if (resolucaoExistente) continue; // ciclo já fechado -- espera o usuário definir nova data-alvo

      const positions = await buildScenarioPositions(row.userId);
      if (!positions.length) continue; // sem posições ativas, nada a acompanhar

      const dataAlvo = new Date(row.dataAlvo + "T00:00:00");
      const m = computeScenarioMetrics(positions, {}, {}, 0, 1, dataAlvo);
      if (m.risco <= 0) continue; // tudo em caixa -- sem risco, sem sentido acompanhar

      await db
        .insert(scenarioSnapshotsTable)
        .values({
          userId: row.userId,
          snapshotDate: hoje,
          dataAlvo: row.dataAlvo,
          diasRestantes: Math.round(m.T * 365),
          pEmpate: m.pEmpate,
          valorTotalHoje: m.valorTotalHoje,
          custoTotal: m.custoTotal,
          p05: m.p05,
          p50: m.p50,
          p95: m.p95,
        })
        .onConflictDoUpdate({
          target: [scenarioSnapshotsTable.userId, scenarioSnapshotsTable.snapshotDate],
          set: {
            dataAlvo: row.dataAlvo,
            diasRestantes: Math.round(m.T * 365),
            pEmpate: m.pEmpate,
            valorTotalHoje: m.valorTotalHoje,
            custoTotal: m.custoTotal,
            p05: m.p05,
            p50: m.p50,
            p95: m.p95,
          },
        });

      if (hoje > row.dataAlvo) {
        // data-alvo já passou -- fecha o ciclo com o resultado final
        await db
          .insert(scenarioResolutionsTable)
          .values({
            userId: row.userId,
            dataAlvo: row.dataAlvo,
            valorFinal: m.valorTotalHoje,
            custoTotal: m.custoTotal,
            pEmpateFinal: m.pEmpate,
            bateu: cicloBateu(m.valorTotalHoje, m.custoTotal),
          })
          .onConflictDoNothing();
        logger.info({ userId: row.userId, bateu: cicloBateu(m.valorTotalHoje, m.custoTotal) }, "Scenario cycle resolved");
        continue; // ciclo encerrado -- não faz sentido mandar alerta de limiar
      }

      if (!row.enabled) continue;
      if (row.lastFiredAt && row.lastFiredAt.getTime() > Date.now() - COOLDOWN_MS) continue;

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
