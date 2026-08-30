import { Router, type IRouter } from "express";
import { and, eq, asc, desc, inArray } from "drizzle-orm";
import { db, entryExitStudyTargetsTable, entryExitStudyHistoryTable, entryExitStudyResolutionsTable, alertsTable, usersTable } from "@workspace/db";
import { spawnAgente } from "../lib/runner";
import { comVagaPython } from "../lib/vaga-python";
import { todayBRTDateString } from "../lib/timezone";
import { logger } from "../lib/logger";

const router: IRouter = Router();

export interface StudyResult {
  ticker: string;
  targetPrice: number;
  targetDate: string;
  currentPrice?: number;
  avgLow1y?: number | null;
  minLow1y?: number | null;
  avgLow6m?: number | null;
  minLow6m?: number | null;
  // Nível de entrada projetado pela vol atual do papel (Phi^-1 da mesma
  // matemática de probReachTarget) -- ver entry_exit_study.py::
  // _entry_pullback_price. Base do 2º alerta de entrada em vez de minLow1y:
  // pra papéis que subiram muito no último ano, a mínima de 12 meses fica
  // longe demais do preço atual pra servir de gatilho realista.
  entryPullbackPrice?: number | null;
  volAnnual?: number | null;
  betaSector?: number | null;
  earningsDate?: string | null;
  daysUntilTarget?: number;
  probReachTarget?: number | null;
  probReachTargetMomentum?: number | null;
  momentumAnnualPct?: number | null;
  news?: Array<{
    title?: string;
    published?: string | null;
    summary?: string | null;
    source?: string | null;
    url?: string | null;
    relatedTickers?: string[] | null;
  }>;
  // Preenchidos SÓ pelo checker diário (entry-exit-study-checker.ts anexa o
  // resultado de agent/entry_exit_sentiment.py antes de persistir) -- a rota
  // POST não paga a chamada de LLM, então snapshots criados por ela ficam
  // com null aqui.
  newsSentiment?: "positivo" | "neutro" | "negativo" | null;
  newsSentimentReason?: string | null;
  error?: string;
}

// Mesmo raciocínio de orçamento de earnings-reaction.ts: cada estudo faz
// várias chamadas de rede sequenciais (histórico + earnings + reação +
// notícias) -- 45s cobre 1 estudo isolado (POST) com folga; o checker
// diário (múltiplos estudos em paralelo) usa um teto maior, ver
// lib/entry-exit-study-checker.ts.
function runEntryExitStudyScript(studies: Array<{ ticker: string; targetPrice: number; targetDate: string }>, timeoutMs = 45_000): Promise<{ results: StudyResult[] }> {
  // comVagaPython -- teto de Python simultâneo vindo de rota HTTP, mesmo padrão de earnings-reaction.ts.
  return comVagaPython("entry_exit_study", () => new Promise((resolve, reject) => {
    const py = spawnAgente("entry_exit_study.py");
    py.stdin.write(JSON.stringify({ studies }));
    py.stdin.end();
    let out = "";
    let err = "";
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("entry_exit_study.py timed out")); }, timeoutMs);
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "entry_exit_study.py failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse entry_exit_study.py output")); }
    });
  }));
}

function serializeTarget(t: typeof entryExitStudyTargetsTable.$inferSelect) {
  return { ...t, targetPrice: Number(t.targetPrice), createdAt: t.createdAt.toISOString() };
}

function serializeHistory(h: typeof entryExitStudyHistoryTable.$inferSelect) {
  return {
    ...h,
    currentPrice: Number(h.currentPrice),
    avgLow1y: h.avgLow1y == null ? null : Number(h.avgLow1y),
    minLow1y: h.minLow1y == null ? null : Number(h.minLow1y),
    avgLow6m: h.avgLow6m == null ? null : Number(h.avgLow6m),
    minLow6m: h.minLow6m == null ? null : Number(h.minLow6m),
    entryPullbackPrice: h.entryPullbackPrice == null ? null : Number(h.entryPullbackPrice),
    volAnnual: h.volAnnual == null ? null : Number(h.volAnnual),
    betaSector: h.betaSector == null ? null : Number(h.betaSector),
    probReachTarget: h.probReachTarget == null ? null : Number(h.probReachTarget),
    probReachTargetMomentum: h.probReachTargetMomentum == null ? null : Number(h.probReachTargetMomentum),
    momentumAnnualPct: h.momentumAnnualPct == null ? null : Number(h.momentumAnnualPct),
    createdAt: h.createdAt.toISOString(),
  };
}

// Persiste o snapshot do dia pra um target (upsert idempotente por
// target_id+calc_date, mesmo padrão de scenario_snapshots) -- chamado tanto
// pela rota POST (primeiro cálculo, na hora de criar o estudo) quanto pelo
// checker diário (lib/entry-exit-study-checker.ts).
export async function persistSnapshot(targetId: number, r: StudyResult) {
  if (r.error || r.currentPrice == null) return null;
  const calcDate = todayBRTDateString();
  const [row] = await db
    .insert(entryExitStudyHistoryTable)
    .values({
      targetId,
      calcDate,
      currentPrice: r.currentPrice,
      avgLow1y: r.avgLow1y ?? null,
      minLow1y: r.minLow1y ?? null,
      avgLow6m: r.avgLow6m ?? null,
      minLow6m: r.minLow6m ?? null,
      entryPullbackPrice: r.entryPullbackPrice ?? null,
      volAnnual: r.volAnnual ?? null,
      betaSector: r.betaSector ?? null,
      probReachTarget: r.probReachTarget ?? null,
      probReachTargetMomentum: r.probReachTargetMomentum ?? null,
      momentumAnnualPct: r.momentumAnnualPct ?? null,
      earningsDate: r.earningsDate ?? null,
      news: r.news ?? null,
      newsSentiment: r.newsSentiment ?? null,
      newsSentimentReason: r.newsSentimentReason ?? null,
    })
    .onConflictDoUpdate({
      target: [entryExitStudyHistoryTable.targetId, entryExitStudyHistoryTable.calcDate],
      set: {
        currentPrice: r.currentPrice,
        avgLow1y: r.avgLow1y ?? null,
        minLow1y: r.minLow1y ?? null,
        avgLow6m: r.avgLow6m ?? null,
        minLow6m: r.minLow6m ?? null,
        entryPullbackPrice: r.entryPullbackPrice ?? null,
        volAnnual: r.volAnnual ?? null,
        betaSector: r.betaSector ?? null,
        probReachTarget: r.probReachTarget ?? null,
        probReachTargetMomentum: r.probReachTargetMomentum ?? null,
        momentumAnnualPct: r.momentumAnnualPct ?? null,
        earningsDate: r.earningsDate ?? null,
        news: r.news ?? null,
        newsSentiment: r.newsSentiment ?? null,
        newsSentimentReason: r.newsSentimentReason ?? null,
      },
    })
    .returning();
  return row;
}

function serializeResolution(r: typeof entryExitStudyResolutionsTable.$inferSelect) {
  return {
    ...r,
    targetPrice: Number(r.targetPrice),
    finalPrice: Number(r.finalPrice),
    probFinal: r.probFinal == null ? null : Number(r.probFinal),
    resolvedAt: r.resolvedAt.toISOString(),
  };
}

// POST /entry-exit-study -- cria um novo estudo (ticker + preço-alvo + data-alvo),
// roda o cálculo do dia na hora, e cria o alerta de preço vinculado
// (mesmo sistema de `alerts`, condition="above", thresholdPrice=targetPrice --
// nível absoluto, não variação diária, ver lib/alert-checker.ts linha 253) pra
// avisar quando o preço cruzar o alvo. Reaproveita o checker de alertas que
// já roda em background -- não duplica monitoramento intraday.
router.post("/entry-exit-study", async (req, res, next): Promise<void> => {
  try {
    const ticker = String(req.body?.ticker ?? "").trim().toUpperCase();
    const targetPrice = Number(req.body?.targetPrice);
    const targetDate = String(req.body?.targetDate ?? "").trim();
    if (!ticker) { res.status(400).json({ error: "ticker is required" }); return; }
    if (!Number.isFinite(targetPrice) || targetPrice <= 0) { res.status(400).json({ error: "targetPrice must be a positive number" }); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) { res.status(400).json({ error: "targetDate must be YYYY-MM-DD" }); return; }

    const data = await runEntryExitStudyScript([{ ticker, targetPrice, targetDate }]);
    const result = data.results?.[0];
    if (!result || result.error) {
      res.status(422).json({ error: result?.error ?? "Failed to compute study" });
      return;
    }

    const [me] = await db.select({ email: usersTable.email }).from(usersTable).where(eq(usersTable.id, req.userId!)).limit(1);
    const notifyEmail = me?.email ?? null;

    // 3 alertas de preço (mesmo sistema de `alerts`, thresholdPrice = nível
    // ABSOLUTO, não variação diária, ver lib/alert-checker.ts linha 253) --
    // reaproveita o checker de alertas que já roda em background, não duplica
    // monitoramento intraday. Saída: acima do preço-alvo. Entrada: abaixo da
    // média das mínimas de 6 meses (sinal mais frequente) e abaixo do nível
    // de entrada projetado pela vol atual (entryPullbackPrice, ver
    // entry_exit_study.py -- fallback pra minLow1y só se a vol não pôde ser
    // calculada). Trocado de minLow1y puro em ago/2026: pra papéis que
    // subiram muito no último ano (visto em produção com INTC/SMCI), a
    // mínima de 12 meses fica tão longe do preço atual que o alerta nunca
    // dispara -- o nível projetado se adapta à vol de cada papel.
    const [exitAlert] = await db.insert(alertsTable)
      .values({ symbol: ticker, indicator: "price", condition: "above", thresholdPrice: targetPrice, notifyEmail, userId: req.userId! })
      .returning();

    let entryAvgLowAlertId: number | null = null;
    if (result.avgLow6m != null) {
      const [a] = await db.insert(alertsTable)
        .values({ symbol: ticker, indicator: "price", condition: "below", thresholdPrice: result.avgLow6m, notifyEmail, userId: req.userId! })
        .returning();
      entryAvgLowAlertId = a.id;
    }

    let entryMinLowAlertId: number | null = null;
    const entryPullbackThreshold = result.entryPullbackPrice ?? result.minLow1y;
    if (entryPullbackThreshold != null) {
      const [a] = await db.insert(alertsTable)
        .values({ symbol: ticker, indicator: "price", condition: "below", thresholdPrice: entryPullbackThreshold, notifyEmail, userId: req.userId! })
        .returning();
      entryMinLowAlertId = a.id;
    }

    const [target] = await db.insert(entryExitStudyTargetsTable)
      .values({ userId: req.userId!, ticker, targetPrice, targetDate, exitAlertId: exitAlert.id, entryAvgLowAlertId, entryMinLowAlertId })
      .returning();

    await persistSnapshot(target.id, result);

    res.status(201).json({ target: serializeTarget(target), calc: result });
  } catch (e) {
    logger.error({ err: e }, "POST /entry-exit-study failed");
    next(e);
  }
});

// GET /entry-exit-study -- lista os estudos ativos do usuário com o último snapshot de cada.
router.get("/entry-exit-study", async (req, res, next): Promise<void> => {
  try {
    const targets = await db.select().from(entryExitStudyTargetsTable)
      .where(and(eq(entryExitStudyTargetsTable.userId, req.userId!), eq(entryExitStudyTargetsTable.active, true)))
      .orderBy(desc(entryExitStudyTargetsTable.createdAt));

    const out = await Promise.all(targets.map(async (t) => {
      const [latest] = await db.select().from(entryExitStudyHistoryTable)
        .where(eq(entryExitStudyHistoryTable.targetId, t.id))
        .orderBy(desc(entryExitStudyHistoryTable.calcDate))
        .limit(1);
      return { target: serializeTarget(t), latest: latest ? serializeHistory(latest) : null };
    }));

    res.json({ studies: out });
  } catch (e) { next(e); }
});

// GET /entry-exit-study/:id -- histórico diário completo de UM estudo (pra
// acompanhar como a probabilidade mudou desde que começou a acompanhar).
// Inclui `resolution` quando a data-alvo já venceu e o checker já registrou
// se bateu ou não (ver lib/entry-exit-study-checker.ts) -- null enquanto o
// estudo ainda está ativo.
router.get("/entry-exit-study/:id", async (req, res, next): Promise<void> => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

    const [target] = await db.select().from(entryExitStudyTargetsTable)
      .where(and(eq(entryExitStudyTargetsTable.id, id), eq(entryExitStudyTargetsTable.userId, req.userId!)))
      .limit(1);
    if (!target) { res.status(404).json({ error: "Not found" }); return; }

    const history = await db.select().from(entryExitStudyHistoryTable)
      .where(eq(entryExitStudyHistoryTable.targetId, id))
      .orderBy(asc(entryExitStudyHistoryTable.calcDate));

    const [resolution] = await db.select().from(entryExitStudyResolutionsTable)
      .where(eq(entryExitStudyResolutionsTable.targetId, id))
      .limit(1);

    res.json({
      target: serializeTarget(target),
      history: history.map(serializeHistory),
      resolution: resolution ? serializeResolution(resolution) : null,
    });
  } catch (e) { next(e); }
});

// PATCH /entry-exit-study/:id -- muda preço-alvo e/ou data-alvo MANTENDO o
// histórico já acumulado (recriar do zero perderia o histórico, porque cada
// snapshot é amarrado ao target_id). Recalcula na hora com o alvo novo e
// atualiza o alerta de saída (o preço-alvo mudou); os dois alertas de
// entrada não mexem -- eles vêm das mínimas históricas do papel, que não
// dependem do alvo escolhido.
router.patch("/entry-exit-study/:id", async (req, res, next): Promise<void> => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

    const temPreco = req.body?.targetPrice !== undefined;
    const temData = req.body?.targetDate !== undefined;
    if (!temPreco && !temData) { res.status(400).json({ error: "targetPrice or targetDate is required" }); return; }

    const [atual] = await db.select().from(entryExitStudyTargetsTable)
      .where(and(eq(entryExitStudyTargetsTable.id, id), eq(entryExitStudyTargetsTable.userId, req.userId!)))
      .limit(1);
    if (!atual) { res.status(404).json({ error: "Not found" }); return; }

    let targetPrice = Number(atual.targetPrice);
    if (temPreco) {
      targetPrice = Number(req.body.targetPrice);
      if (!Number.isFinite(targetPrice) || targetPrice <= 0) { res.status(400).json({ error: "targetPrice must be a positive number" }); return; }
    }

    let targetDate = atual.targetDate;
    if (temData) {
      targetDate = String(req.body.targetDate).trim();
      if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) { res.status(400).json({ error: "targetDate must be YYYY-MM-DD" }); return; }
    }

    const data = await runEntryExitStudyScript([{ ticker: atual.ticker, targetPrice, targetDate }]);
    const result = data.results?.[0];
    if (!result || result.error) {
      res.status(422).json({ error: result?.error ?? "Failed to compute study" });
      return;
    }

    const [target] = await db.update(entryExitStudyTargetsTable)
      .set({ targetPrice, targetDate, active: true })
      .where(eq(entryExitStudyTargetsTable.id, id))
      .returning();

    if (atual.exitAlertId != null) {
      await db.update(alertsTable)
        .set({ thresholdPrice: targetPrice, enabled: true })
        .where(eq(alertsTable.id, atual.exitAlertId));
    }

    await persistSnapshot(target.id, result);

    res.json({ target: serializeTarget(target), calc: result });
  } catch (e) {
    logger.error({ err: e }, "PATCH /entry-exit-study failed");
    next(e);
  }
});

// DELETE /entry-exit-study/:id -- para de acompanhar (soft: mantém o
// histórico já registrado) e desativa os 3 alertas de preço vinculados
// (saída + as 2 entradas).
router.delete("/entry-exit-study/:id", async (req, res, next): Promise<void> => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) { res.status(400).json({ error: "Invalid id" }); return; }

    const [target] = await db.update(entryExitStudyTargetsTable)
      .set({ active: false })
      .where(and(eq(entryExitStudyTargetsTable.id, id), eq(entryExitStudyTargetsTable.userId, req.userId!)))
      .returning();
    if (!target) { res.status(404).json({ error: "Not found" }); return; }

    const alertIds = [target.exitAlertId, target.entryAvgLowAlertId, target.entryMinLowAlertId].filter((v): v is number => v != null);
    if (alertIds.length) {
      await db.update(alertsTable).set({ enabled: false }).where(inArray(alertsTable.id, alertIds));
    }
    res.status(204).end();
  } catch (e) { next(e); }
});

export default router;
