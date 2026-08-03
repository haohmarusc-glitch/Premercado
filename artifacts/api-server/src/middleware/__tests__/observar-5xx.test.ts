import { describe, it, expect, vi, beforeEach } from "vitest";
import express, { type ErrorRequestHandler } from "express";
import request from "supertest";

const erros: { campos: Record<string, unknown>; msg: string }[] = [];

vi.mock("../../lib/logger", () => ({
  logger: {
    error: (campos: Record<string, unknown>, msg: string) => {
      erros.push({ campos, msg });
    },
    info: () => {},
    warn: () => {},
  },
}));

const {
  observar5xx,
  marcarEntradaNoRouter,
  marcarOrigemErrorHandler,
  ORIGEM_ANTES_DO_ROUTER,
  ORIGEM_ROTA_DIRETA,
  ORIGEM_ERROR_HANDLER,
} = await import("../observar-5xx");

/**
 * Monta um app com a MESMA ordem de registro do app.ts real -- é a ordem que
 * está sendo testada, não os handlers. Um teste que registrasse observar5xx
 * depois do middleware que responde passaria sem provar nada.
 */
function montarApp(opts: {
  middlewareQuebrado?: boolean;
  rota?: express.RequestHandler;
}) {
  const app = express();
  app.use(observar5xx);
  if (opts.middlewareQuebrado) {
    app.use((_req, res, _next) => {
      res.status(503).json({ error: "camada antes do router" });
    });
  }
  const router = express.Router();
  router.get("/coisa", opts.rota ?? ((_req, res) => { res.json({ ok: true }); }));
  app.use("/api", marcarEntradaNoRouter, router);
  const errorHandler: ErrorRequestHandler = (_err, _req, res, _next) => {
    marcarOrigemErrorHandler(res);
    if (res.headersSent) return;
    res.status(500).json({ error: "Internal server error" });
  };
  app.use(errorHandler);
  return app;
}

function ultimo5xx() {
  return erros.filter((e) => e.msg === "Resposta 5xx").at(-1);
}

beforeEach(() => {
  erros.length = 0;
});

describe("observar5xx", () => {
  it("acusa 5xx de middleware ANTES do router -- o caso que era invisível", async () => {
    // Este é o motivo do middleware existir: em produção apareceu um 500 sem
    // nenhum log de erro junto, o que descarta rota (elas logam sozinhas) e
    // errorHandler (loga "Unhandled route error"). Sobrava esta camada.
    await request(montarApp({ middlewareQuebrado: true })).get("/api/coisa");
    const log = ultimo5xx();
    expect(log?.campos.origem).toBe(ORIGEM_ANTES_DO_ROUTER);
    expect(log?.campos.status).toBe(503);
    expect(log?.campos.caminho).toBe("/api/coisa");
  });

  it("distingue rota que responde 500 direto de rota que chama next(e)", async () => {
    await request(montarApp({
      rota: (_req, res) => { res.status(500).json({ error: "falhou" }); },
    })).get("/api/coisa");
    expect(ultimo5xx()?.campos.origem).toBe(ORIGEM_ROTA_DIRETA);

    erros.length = 0;
    await request(montarApp({
      rota: (_req, _res, next) => { next(new Error("boom")); },
    })).get("/api/coisa");
    expect(ultimo5xx()?.campos.origem).toBe(ORIGEM_ERROR_HANDLER);
  });

  it("não loga nada em resposta de sucesso nem em 4xx", async () => {
    const app = montarApp({});
    await request(app).get("/api/coisa");
    await request(app).get("/api/nao-existe");   // 404
    await request(app).get("/fora-do-api");      // 404
    expect(erros.filter((e) => e.msg === "Resposta 5xx")).toHaveLength(0);
  });

  it("registra o caminho sem a query string", async () => {
    await request(montarApp({
      rota: (_req, res) => { res.status(500).json({ error: "x" }); },
    })).get("/api/coisa?ticker=NVDA&secreto=abc");
    const log = ultimo5xx();
    expect(log?.campos.caminho).toBe("/api/coisa");
    expect(JSON.stringify(log?.campos)).not.toContain("secreto");
  });

  it("registra o caminho completo, não o reescrito pelo mount do router", async () => {
    // req.url dentro do router vira "/coisa" -- só originalUrl preserva o
    // "/api" que a gente precisa pra achar a rota no log.
    await request(montarApp({
      rota: (_req, res) => { res.status(500).json({ error: "x" }); },
    })).get("/api/coisa");
    expect(ultimo5xx()?.campos.caminho).toBe("/api/coisa");
  });

  it("mede a duração da request", async () => {
    await request(montarApp({
      rota: (_req, res) => { res.status(500).json({ error: "x" }); },
    })).get("/api/coisa");
    expect(typeof ultimo5xx()?.campos.ms).toBe("number");
    expect(ultimo5xx()?.campos.ms as number).toBeGreaterThanOrEqual(0);
  });
});
