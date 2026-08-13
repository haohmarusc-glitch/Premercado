/**
 * Resolução do Estudo de Entrada e Saída -- a regra de quando um estudo
 * vencido vira uma linha em entry_exit_study_resolutions.
 *
 * Por que este teste existe: antes, o checker só marcava active=false quando
 * a data-alvo passava, e o rastro do resultado se perdia -- nunca ficava
 * registrado se o preço realmente bateu o alvo, então não havia como medir
 * se a probabilidade calculada era confiável. A ordem correta importa e é o
 * que se trava aqui: rodar o cálculo do dia PRIMEIRO (pra ter o preço final
 * de verdade), depois registrar a resolução, e só então desativar.
 *
 * Roda contra o Postgres de teste pelo mesmo motivo de
 * entry-exit-study.routes.test.ts (a lógica é toda de banco: insert
 * condicional, onConflictDoNothing, update de active) e pula sozinho sem
 * DATABASE_URL_TEST.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { EventEmitter } from "events";

const DB_URL = process.env.DATABASE_URL_TEST;

if (!DB_URL) {
  // eslint-disable-next-line no-console
  console.warn("[entry-exit-study-checker.resolution.test] pulando -- defina DATABASE_URL_TEST pra rodar.");
} else {
  process.env.DATABASE_URL = DB_URL;
}

let resultadosDoScript: unknown[] = [];

vi.mock("../lib/python-spawn", () => ({
  spawnPython: () => {
    const stdout = new EventEmitter();
    const stderr = new EventEmitter();
    const proc = new EventEmitter() as EventEmitter & {
      stdin: { write: (s: string) => void; end: () => void };
      stdout: EventEmitter;
      stderr: EventEmitter;
      kill: () => void;
    };
    proc.stdin = { write: () => {}, end: () => {} };
    proc.stdout = stdout;
    proc.stderr = stderr;
    proc.kill = () => {};
    queueMicrotask(() => {
      stdout.emit("data", Buffer.from(JSON.stringify({ results: resultadosDoScript })));
      proc.emit("close", 0);
    });
    return proc;
  },
}));

vi.mock("../lib/python-queue", () => ({
  runExclusive: async (_rotulo: string, tarefa: () => Promise<unknown>) => tarefa(),
}));

describe.skipIf(!DB_URL)("Resolução de estudo vencido (integração, Postgres real)", () => {
  let db: typeof import("@workspace/db")["db"];
  let pool: typeof import("@workspace/db")["pool"];
  let targetsTable: typeof import("@workspace/db")["entryExitStudyTargetsTable"];
  let historyTable: typeof import("@workspace/db")["entryExitStudyHistoryTable"];
  let resolutionsTable: typeof import("@workspace/db")["entryExitStudyResolutionsTable"];
  let usersTable: typeof import("@workspace/db")["usersTable"];
  let eq: typeof import("drizzle-orm")["eq"];
  let refreshEntryExitStudies: typeof import("../lib/entry-exit-study-checker")["refreshEntryExitStudies"];

  const EMAIL = "entry-exit-study-resolution-test@example.com";
  let userId: number;

  // Datas relativas a hoje pra não depender de relógio parado: uma já
  // vencida (ontem) e uma ainda no futuro.
  const ONTEM = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const FUTURO = "2099-01-01";

  beforeAll(async () => {
    const dbMod = await import("@workspace/db");
    const drizzleOrm = await import("drizzle-orm");
    const checker = await import("../lib/entry-exit-study-checker");

    db = dbMod.db;
    pool = dbMod.pool;
    targetsTable = dbMod.entryExitStudyTargetsTable;
    historyTable = dbMod.entryExitStudyHistoryTable;
    resolutionsTable = dbMod.entryExitStudyResolutionsTable;
    usersTable = dbMod.usersTable;
    eq = drizzleOrm.eq;
    refreshEntryExitStudies = checker.refreshEntryExitStudies;

    const [existing] = await db.select().from(usersTable).where(eq(usersTable.email, EMAIL)).limit(1);
    userId = existing?.id ?? (await db.insert(usersTable).values({ email: EMAIL, passwordHash: "x", isClaimed: true }).returning())[0].id;
  });

  afterAll(async () => {
    await db.delete(usersTable).where(eq(usersTable.id, userId));
    await pool.end();
  });

  beforeEach(async () => {
    await db.delete(targetsTable).where(eq(targetsTable.userId, userId));
    resultadosDoScript = [];
  });

  async function criarTarget(targetDate: string, targetPrice = 45) {
    const [t] = await db.insert(targetsTable)
      .values({ userId, ticker: "SMCI", targetPrice, targetDate })
      .returning();
    return t;
  }

  function resultado(targetDate: string, currentPrice: number, targetPrice = 45) {
    return {
      ticker: "SMCI", targetPrice, targetDate,
      currentPrice, avgLow1y: 34.14, minLow1y: 19.48, avgLow6m: 29.38, minLow6m: 19.48,
      volAnnual: 0.9, betaSector: 1.28, earningsDate: null, daysUntilTarget: 1,
      probReachTarget: 0.25, news: [],
    };
  }

  it("estudo vencido com preço ACIMA do alvo: registra bateu=true e desativa", async () => {
    const t = await criarTarget(ONTEM);
    resultadosDoScript = [resultado(ONTEM, 47.5)];

    await refreshEntryExitStudies();

    const [res] = await db.select().from(resolutionsTable).where(eq(resolutionsTable.targetId, t.id));
    expect(res.bateu).toBe(true);
    expect(Number(res.finalPrice)).toBeCloseTo(47.5);
    expect(Number(res.targetPrice)).toBeCloseTo(45);
    expect(Number(res.probFinal)).toBeCloseTo(0.25);

    const [depois] = await db.select().from(targetsTable).where(eq(targetsTable.id, t.id));
    expect(depois.active).toBe(false);
  });

  it("estudo vencido com preço ABAIXO do alvo: registra bateu=false", async () => {
    const t = await criarTarget(ONTEM);
    resultadosDoScript = [resultado(ONTEM, 38.2)];

    await refreshEntryExitStudies();

    const [res] = await db.select().from(resolutionsTable).where(eq(resolutionsTable.targetId, t.id));
    expect(res.bateu).toBe(false);
    expect(Number(res.finalPrice)).toBeCloseTo(38.2);
  });

  it("grava o snapshot final ANTES de desativar -- o preço do dia da resolução não se perde", async () => {
    const t = await criarTarget(ONTEM);
    resultadosDoScript = [resultado(ONTEM, 41.0)];

    await refreshEntryExitStudies();

    const historico = await db.select().from(historyTable).where(eq(historyTable.targetId, t.id));
    expect(historico).toHaveLength(1);
    expect(Number(historico[0].currentPrice)).toBeCloseTo(41.0);
  });

  it("estudo ainda no futuro: atualiza o snapshot, NÃO resolve nem desativa", async () => {
    const t = await criarTarget(FUTURO);
    resultadosDoScript = [resultado(FUTURO, 39.0)];

    await refreshEntryExitStudies();

    const resolucoes = await db.select().from(resolutionsTable).where(eq(resolutionsTable.targetId, t.id));
    expect(resolucoes).toHaveLength(0);

    const [depois] = await db.select().from(targetsTable).where(eq(targetsTable.id, t.id));
    expect(depois.active).toBe(true);

    const historico = await db.select().from(historyTable).where(eq(historyTable.targetId, t.id));
    expect(historico).toHaveLength(1);
  });

  it("vencido sem preço no resultado: desativa mesmo assim, sem resolução (não fica preso tentando)", async () => {
    const t = await criarTarget(ONTEM);
    resultadosDoScript = [{ ticker: "SMCI", targetPrice: 45, targetDate: ONTEM, error: "sem histórico de preço" }];

    await refreshEntryExitStudies();

    const resolucoes = await db.select().from(resolutionsTable).where(eq(resolutionsTable.targetId, t.id));
    expect(resolucoes).toHaveLength(0);

    const [depois] = await db.select().from(targetsTable).where(eq(targetsTable.id, t.id));
    expect(depois.active).toBe(false);
  });

  it("resolução é idempotente -- rodar o checker de novo não duplica nem sobrescreve", async () => {
    const t = await criarTarget(ONTEM);
    resultadosDoScript = [resultado(ONTEM, 47.5)];
    await refreshEntryExitStudies();

    // Reativa à força e roda de novo com preço diferente: a resolução original
    // (uma vez resolvida, nunca muda -- mesma regra de scenario_resolutions)
    // tem que sobreviver.
    await db.update(targetsTable).set({ active: true }).where(eq(targetsTable.id, t.id));
    resultadosDoScript = [resultado(ONTEM, 10.0)];
    await refreshEntryExitStudies();

    const resolucoes = await db.select().from(resolutionsTable).where(eq(resolutionsTable.targetId, t.id));
    expect(resolucoes).toHaveLength(1);
    expect(Number(resolucoes[0].finalPrice)).toBeCloseTo(47.5);
    expect(resolucoes[0].bateu).toBe(true);
  });
});
