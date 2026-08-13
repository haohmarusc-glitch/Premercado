/**
 * Background job que recalcula, 1x por dia, TODOS os Estudos de Entrada e
 * Saída ativos (entry_exit_study_targets) -- é isso que constrói o
 * "histórico diário" em entry_exit_study_history sozinho, sem precisar
 * perguntar de novo a cada dia (ver entry_exit_study.py e
 * routes/entry-exit-study.ts::persistSnapshot, reaproveitado aqui).
 *
 * Roda como uma ETAPA do ciclo de checkers via request (routes/checkers.ts),
 * não como setTimeout solto -- mesmo motivo documentado lá: no Autoscale, CPU
 * só é garantida durante um request.
 */
import path from "path";
import { eq } from "drizzle-orm";
import { db, entryExitStudyTargetsTable, entryExitStudyResolutionsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "./runner";
import { spawnPython } from "./python-spawn";
import { runExclusive } from "./python-queue";
import { todayBRTDateString } from "./timezone";
import { state as agentState } from "./runner";
import { logger } from "./logger";
import { persistSnapshot, type StudyResult } from "../routes/entry-exit-study";

// Cada estudo é uma sequência de várias chamadas de rede (histórico + earnings
// + reação histórica + notícias) rodando em paralelo dentro do script (ver
// bounded_parallel_map em entry_exit_study.py) -- teto generoso porque este
// checker roda 1x/dia, não há pressão de latência como numa rota HTTP.
const TIMEOUT_MS = 180_000;

function runEntryExitStudyScriptExclusive(studies: Array<{ ticker: string; targetPrice: number; targetDate: string }>): Promise<{ results: StudyResult[] }> {
  return runExclusive("entry_exit_study", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "entry_exit_study.py");
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ studies }));
    py.stdin.end();
    let out = "";
    let err = "";
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("entry_exit_study.py timed out")); }, TIMEOUT_MS);
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "entry_exit_study.py failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse entry_exit_study.py output")); }
    });
  }));
}

export async function refreshEntryExitStudies(): Promise<void> {
  // Mesmo motivo dos outros checkers: entry_exit_study.py baixa histórico via
  // yfinance, não vale competir por CPU/rede com o agente diário.
  if (agentState.running) {
    logger.info("Entry/exit study checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const hoje = todayBRTDateString();

  const targets = await db.select().from(entryExitStudyTargetsTable)
    .where(eq(entryExitStudyTargetsTable.active, true));
  if (!targets.length) return;

  const studies = targets.map((t) => ({ ticker: t.ticker, targetPrice: Number(t.targetPrice), targetDate: t.targetDate }));

  let data: { results: StudyResult[] };
  try {
    data = await runEntryExitStudyScriptExclusive(studies);
  } catch (err) {
    logger.error({ err, count: studies.length }, "Entry/exit study checker: falha ao rodar entry_exit_study.py");
    return;
  }

  // Casa cada resultado de volta ao target por ticker+preço-alvo+data-alvo --
  // o script não conhece o id interno, só ecoa os 3 campos que recebeu, e o
  // trio é único o bastante pra não colidir entre estudos diferentes do mesmo
  // ticker.
  const byKey = new Map(data.results.map((r) => [`${r.ticker}|${r.targetPrice}|${r.targetDate}`, r]));

  let updated = 0;
  let resolved = 0;
  const failed: string[] = [];
  for (const t of targets) {
    // Vencida = data-alvo já passou (não hoje -- o dia da data-alvo ainda
    // conta como dentro da janela). Roda o cálculo do dia PRA TODOS antes de
    // decidir isso, pra capturar o preço final de verdade (mesmo dado que o
    // usuário veria pedindo agora), em vez de só apagar o estudo sem saber
    // se bateu.
    const vencida = t.targetDate < hoje;

    const key = `${t.ticker}|${Number(t.targetPrice)}|${t.targetDate}`;
    const r = byKey.get(key);
    if (!r || r.error || r.currentPrice == null) {
      failed.push(`${t.ticker}(#${t.id})`);
      if (vencida) {
        // Sem preço final não dá pra registrar se bateu -- desativa mesmo
        // assim (não fica preso tentando pra sempre), só sem resolução.
        await db.update(entryExitStudyTargetsTable).set({ active: false }).where(eq(entryExitStudyTargetsTable.id, t.id));
        logger.warn({ id: t.id, ticker: t.ticker }, "Entry/exit study: data-alvo vencida sem conseguir preço final -- desativado sem resolução registrada");
      }
      continue;
    }

    await persistSnapshot(t.id, r);
    updated++;

    if (vencida) {
      await db.insert(entryExitStudyResolutionsTable)
        .values({
          targetId: t.id,
          ticker: t.ticker,
          targetPrice: t.targetPrice,
          targetDate: t.targetDate,
          finalPrice: r.currentPrice,
          bateu: r.currentPrice >= Number(t.targetPrice),
          probFinal: r.probReachTarget ?? null,
        })
        .onConflictDoNothing({ target: entryExitStudyResolutionsTable.targetId });
      await db.update(entryExitStudyTargetsTable).set({ active: false }).where(eq(entryExitStudyTargetsTable.id, t.id));
      resolved++;
    }
  }

  if (failed.length) {
    logger.warn({ failed }, "Entry/exit study checker: estudos sem cálculo válido no ciclo");
  }
  logger.info({ updated, resolved, total: targets.length }, "Entry/exit studies refreshed");
}
