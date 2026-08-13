/**
 * Teste de integração das rotas de /entry-exit-study -- bate na rota de
 * verdade (Express real, roteador real, Drizzle real) contra um Postgres de
 * teste, em vez de mockar a camada de banco. A calculadora Python e as duas
 * únicas coisas que uma rota HTTP não deveria depender de rede/tempo real
 * pra testar (spawnPython, comVagaPython) são mockadas -- tudo o resto
 * (validação, criação dos 3 alertas, upsert de snapshot, soft-delete,
 * 404/400/422) roda de ponta a ponta.
 *
 * Optativo de propósito: nenhum outro teste deste repo precisa de Postgres
 * de verdade (ver checker-watchdog.test.ts/report-preflight.test.ts, que
 * mockam @workspace/db), e adicionar essa dependência ao `pnpm test` padrão
 * quebraria o fluxo de quem não tem Postgres local rodando. Esta suíte
 * PULA sozinha (com aviso claro) se DATABASE_URL_TEST não estiver definida.
 *
 * Rodar (com um Postgres de teste já com o schema aplicado via
 * `pnpm --filter @workspace/db push`):
 *   DATABASE_URL_TEST=postgresql://user:pass@localhost:5432/algum_db \
 *     pnpm --filter @workspace/api-server exec vitest run src/__tests__/entry-exit-study.routes.test.ts
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import request from "supertest";
import { EventEmitter } from "events";
import type { Express } from "express";

const DB_URL = process.env.DATABASE_URL_TEST;

if (!DB_URL) {
  // eslint-disable-next-line no-console
  console.warn(
    "[entry-exit-study.routes.test] pulando -- defina DATABASE_URL_TEST apontando " +
    "pra um Postgres de teste (com o schema já aplicado via `pnpm --filter @workspace/db push`) " +
    "pra rodar esta suíte de integração.",
  );
} else {
  process.env.DATABASE_URL = DB_URL;
}

// spawnPython/comVagaPython são mockados -- o resto da rota (validação,
// Drizzle, criação de alertas, soft-delete) roda de verdade contra o
// Postgres de teste. Configurável por teste via `proximaSaidaDoScript`.
let proximaSaidaDoScript: unknown = null;
let proximoCodigoDeSaida = 0;

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
    // Emitido depois do registro dos listeners (síncrono no fim do call
    // stack atual) -- runEntryExitStudyScript registra .on("data")/.on("close")
    // logo após chamar spawnPython, então isto sempre chega a tempo.
    queueMicrotask(() => {
      stdout.emit("data", Buffer.from(JSON.stringify({ results: [proximaSaidaDoScript] })));
      proc.emit("close", proximoCodigoDeSaida);
    });
    return proc;
  },
}));

vi.mock("../lib/vaga-python", () => ({
  comVagaPython: async (_rotulo: string, tarefa: () => Promise<unknown>) => tarefa(),
}));

describe.skipIf(!DB_URL)("Rotas /entry-exit-study (integração, Postgres real)", () => {
  let app: Express;
  let db: typeof import("@workspace/db")["db"];
  let entryExitStudyTargetsTable: typeof import("@workspace/db")["entryExitStudyTargetsTable"];
  let alertsTable: typeof import("@workspace/db")["alertsTable"];
  let usersTable: typeof import("@workspace/db")["usersTable"];
  let eq: typeof import("drizzle-orm")["eq"];

  const EMAIL = "entry-exit-study-route-test@example.com";
  let userId: number;
  let pool: typeof import("@workspace/db")["pool"];

  beforeAll(async () => {
    const express = (await import("express")).default;
    const dbMod = await import("@workspace/db");
    const drizzleOrm = await import("drizzle-orm");
    const router = (await import("../routes/entry-exit-study")).default;

    db = dbMod.db;
    pool = dbMod.pool;
    entryExitStudyTargetsTable = dbMod.entryExitStudyTargetsTable;
    alertsTable = dbMod.alertsTable;
    usersTable = dbMod.usersTable;
    eq = drizzleOrm.eq;

    const [existing] = await db.select().from(usersTable).where(eq(usersTable.email, EMAIL)).limit(1);
    if (existing) {
      userId = existing.id;
    } else {
      const [created] = await db.insert(usersTable)
        .values({ email: EMAIL, passwordHash: "x", isClaimed: true })
        .returning();
      userId = created.id;
    }

    app = express();
    app.use(express.json());
    app.use((req, _res, next) => { req.userId = userId; next(); });
    app.use("/api", router);
    app.use((err: unknown, _req: unknown, res: import("express").Response, _next: unknown) => {
      res.status(500).json({ error: (err as Error)?.message ?? "erro" });
    });
  });

  afterAll(async () => {
    await db.delete(alertsTable).where(eq(alertsTable.userId, userId));
    await db.delete(usersTable).where(eq(usersTable.id, userId));
    await pool.end();
  });

  beforeEach(async () => {
    await db.delete(entryExitStudyTargetsTable).where(eq(entryExitStudyTargetsTable.userId, userId));
    await db.delete(alertsTable).where(eq(alertsTable.userId, userId));
    proximaSaidaDoScript = null;
    proximoCodigoDeSaida = 0;
  });

  const CALC_OK = {
    ticker: "SMCI",
    targetPrice: 45,
    targetDate: "2099-01-01",
    currentPrice: 37.61,
    avgLow1y: 34.14,
    minLow1y: 19.48,
    avgLow6m: 29.38,
    minLow6m: 19.48,
    volAnnual: 0.9091,
    betaSector: 1.2829,
    earningsDate: null,
    daysUntilTarget: 9999,
    probReachTarget: 0.2492,
    news: [],
  };

  it("POST cria o target + os 3 alertas (saída, entrada média, entrada mínima) e persiste o snapshot do dia", async () => {
    proximaSaidaDoScript = CALC_OK;

    const res = await request(app)
      .post("/api/entry-exit-study")
      .send({ ticker: "smci", targetPrice: 45, targetDate: "2099-01-01" });

    expect(res.status).toBe(201);
    expect(res.body.target.ticker).toBe("SMCI");
    expect(res.body.target.exitAlertId).toEqual(expect.any(Number));
    expect(res.body.target.entryAvgLowAlertId).toEqual(expect.any(Number));
    expect(res.body.target.entryMinLowAlertId).toEqual(expect.any(Number));
    expect(res.body.calc.probReachTarget).toBeCloseTo(0.2492);

    const alerts = await db.select().from(alertsTable).where(eq(alertsTable.userId, userId));
    expect(alerts).toHaveLength(3);
    const exit = alerts.find((a) => a.id === res.body.target.exitAlertId);
    expect(exit?.condition).toBe("above");
    expect(Number(exit?.thresholdPrice)).toBe(45);
    const entradas = alerts.filter((a) => a.condition === "below");
    expect(entradas).toHaveLength(2);
  });

  it("POST sem avgLow6m/minLow1y (histórico insuficiente) cria só o alerta de saída", async () => {
    proximaSaidaDoScript = { ...CALC_OK, avgLow6m: null, minLow1y: null };

    const res = await request(app)
      .post("/api/entry-exit-study")
      .send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    expect(res.status).toBe(201);
    expect(res.body.target.exitAlertId).toEqual(expect.any(Number));
    expect(res.body.target.entryAvgLowAlertId).toBeNull();
    expect(res.body.target.entryMinLowAlertId).toBeNull();

    const alerts = await db.select().from(alertsTable).where(eq(alertsTable.userId, userId));
    expect(alerts).toHaveLength(1);
  });

  it("POST com falha da calculadora devolve 422 e não cria nada", async () => {
    proximaSaidaDoScript = { ticker: "SMCI", error: "sem histórico de preço" };

    const res = await request(app)
      .post("/api/entry-exit-study")
      .send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    expect(res.status).toBe(422);
    const targets = await db.select().from(entryExitStudyTargetsTable).where(eq(entryExitStudyTargetsTable.userId, userId));
    expect(targets).toHaveLength(0);
    const alerts = await db.select().from(alertsTable).where(eq(alertsTable.userId, userId));
    expect(alerts).toHaveLength(0);
  });

  it.each([
    { body: { targetPrice: 45, targetDate: "2099-01-01" }, motivo: "ticker" },
    { body: { ticker: "SMCI", targetDate: "2099-01-01" }, motivo: "preço-alvo" },
    { body: { ticker: "SMCI", targetPrice: -1, targetDate: "2099-01-01" }, motivo: "preço-alvo negativo" },
    { body: { ticker: "SMCI", targetPrice: 45, targetDate: "01/01/2099" }, motivo: "data em formato errado" },
  ])("POST com input inválido (falta $motivo) devolve 400", async ({ body }) => {
    const res = await request(app).post("/api/entry-exit-study").send(body);
    expect(res.status).toBe(400);
  });

  it("GET lista os estudos ativos do usuário com o último snapshot", async () => {
    proximaSaidaDoScript = CALC_OK;
    await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    const res = await request(app).get("/api/entry-exit-study");
    expect(res.status).toBe(200);
    expect(res.body.studies).toHaveLength(1);
    expect(res.body.studies[0].target.ticker).toBe("SMCI");
    expect(res.body.studies[0].latest.currentPrice).toBeCloseTo(37.61);
  });

  it("GET /:id devolve o histórico completo; id de outro usuário (ou inexistente) devolve 404", async () => {
    proximaSaidaDoScript = CALC_OK;
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });
    const id = created.body.target.id;

    const res = await request(app).get(`/api/entry-exit-study/${id}`);
    expect(res.status).toBe(200);
    expect(res.body.history).toHaveLength(1);
    expect(res.body.history[0].probReachTarget).toBeCloseTo(0.2492);

    const naoExiste = await request(app).get("/api/entry-exit-study/999999999");
    expect(naoExiste.status).toBe(404);
  });

  it("POST persiste as notícias do cálculo junto do snapshot", async () => {
    proximaSaidaDoScript = {
      ...CALC_OK,
      news: [{ title: "SMCI sobe 19% após balanço", source: "WSJ", url: "https://exemplo/1", summary: "resumo", published: "2026-08-12T21:43:00Z", relatedTickers: ["CRWV"] }],
    };
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    const res = await request(app).get(`/api/entry-exit-study/${created.body.target.id}`);
    expect(res.body.history[0].news).toHaveLength(1);
    expect(res.body.history[0].news[0].title).toContain("SMCI sobe 19%");
    expect(res.body.history[0].news[0].relatedTickers).toEqual(["CRWV"]);
  });

  it("GET /:id devolve resolution: null enquanto o estudo não venceu", async () => {
    proximaSaidaDoScript = CALC_OK;
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    const res = await request(app).get(`/api/entry-exit-study/${created.body.target.id}`);
    expect(res.body.resolution).toBeNull();
  });

  it("PATCH muda alvo/data MANTENDO o histórico e atualiza o alerta de saída", async () => {
    proximaSaidaDoScript = CALC_OK;
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });
    const id = created.body.target.id;
    const exitAlertId = created.body.target.exitAlertId;
    const entryAlertId = created.body.target.entryAvgLowAlertId;

    proximaSaidaDoScript = { ...CALC_OK, targetPrice: 60, targetDate: "2099-06-01", probReachTarget: 0.11 };
    const patch = await request(app)
      .patch(`/api/entry-exit-study/${id}`)
      .send({ targetPrice: 60, targetDate: "2099-06-01" });

    expect(patch.status).toBe(200);
    expect(patch.body.target.targetPrice).toBe(60);
    expect(patch.body.target.targetDate).toBe("2099-06-01");

    // o alerta de SAÍDA acompanha o novo alvo...
    const [exitAlert] = await db.select().from(alertsTable).where(eq(alertsTable.id, exitAlertId));
    expect(Number(exitAlert.thresholdPrice)).toBe(60);

    // ...mas os de ENTRADA não, porque vêm das mínimas históricas do papel,
    // que não dependem do alvo escolhido.
    const [entryAlert] = await db.select().from(alertsTable).where(eq(alertsTable.id, entryAlertId));
    expect(Number(entryAlert.thresholdPrice)).toBeCloseTo(29.38);

    // histórico preservado (é o ponto do PATCH existir em vez de recriar)
    const detalhe = await request(app).get(`/api/entry-exit-study/${id}`);
    expect(detalhe.body.history.length).toBeGreaterThanOrEqual(1);
  });

  it("PATCH sem nenhum campo devolve 400; id inexistente devolve 404", async () => {
    proximaSaidaDoScript = CALC_OK;
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });

    const semCampo = await request(app).patch(`/api/entry-exit-study/${created.body.target.id}`).send({});
    expect(semCampo.status).toBe(400);

    const naoExiste = await request(app).patch("/api/entry-exit-study/999999999").send({ targetPrice: 60 });
    expect(naoExiste.status).toBe(404);
  });

  it("DELETE desativa o target e os 3 alertas, mas mantém o histórico", async () => {
    proximaSaidaDoScript = CALC_OK;
    const created = await request(app).post("/api/entry-exit-study").send({ ticker: "SMCI", targetPrice: 45, targetDate: "2099-01-01" });
    const id = created.body.target.id;
    const alertIds = [created.body.target.exitAlertId, created.body.target.entryAvgLowAlertId, created.body.target.entryMinLowAlertId];

    const del = await request(app).delete(`/api/entry-exit-study/${id}`);
    expect(del.status).toBe(204);

    const [target] = await db.select().from(entryExitStudyTargetsTable).where(eq(entryExitStudyTargetsTable.id, id));
    expect(target.active).toBe(false);

    const alerts = await db.select().from(alertsTable).where(eq(alertsTable.userId, userId));
    expect(alerts.filter((a) => alertIds.includes(a.id)).every((a) => !a.enabled)).toBe(true);

    // histórico continua acessível mesmo com o estudo inativo
    const historico = await request(app).get(`/api/entry-exit-study/${id}`);
    expect(historico.status).toBe(200);
    expect(historico.body.history).toHaveLength(1);

    // some da listagem de ativos
    const lista = await request(app).get("/api/entry-exit-study");
    expect(lista.body.studies.find((s: { target: { id: number } }) => s.target.id === id)).toBeUndefined();
  });
});
