/**
 * O errorHandler global distingue erro de CLIENTE já classificado (status
 * 4xx + expose=true, contrato do http-errors que o body-parser segue) de
 * todo o resto -- que continua 500 genérico, sem vazar mensagem interna.
 *
 * Incidente de 20/08/2026: a tela de reação a earnings mandou 107.650 bytes
 * contra o limite de 102.400 do express.json; o body-parser levantou
 * PayloadTooLargeError (status 413, expose true) e o handler antigo
 * respondeu 500 "Internal server error" -- o usuário leu erro de servidor
 * para um problema do corpo da requisição, diagnosticável só pelo log.
 *
 * O teste monta um app mínimo com o express.json() REAL (mesmo limite
 * default de produção) e o errorHandler real: o 413 aqui é o do incidente,
 * não um erro sintético.
 *
 * Rodar: pnpm --filter @workspace/api-server exec vitest run src/__tests__/error-handler.test.ts
 */
import { describe, it, expect } from "vitest";
import express from "express";
import request from "supertest";
import { errorHandler } from "../middleware/error-handler";

function appMinimo(rota?: express.RequestHandler) {
  const app = express();
  app.use(express.json()); // limite default (~100KB), como em produção
  app.post("/eco", rota ?? ((_req, res) => { res.json({ ok: true }); }));
  app.use(errorHandler);
  return app;
}

describe("errorHandler: erro de cliente classificado responde com o próprio status", () => {
  it("corpo acima do limite do parser vira 413 com a mensagem do body-parser", async () => {
    // O incidente, reproduzido: ~107KB contra o limite default.
    const corpo = { lixo: "x".repeat(107_000) };
    const res = await request(appMinimo()).post("/eco")
      .set("content-type", "application/json")
      .send(JSON.stringify(corpo));
    expect(res.status).toBe(413);
    // A mensagem que faz o erro ser investigável da tela, sem abrir log:
    expect(res.body.error).toContain("request entity too large");
  });

  it("JSON inválido vira 400, não 500", async () => {
    const res = await request(appMinimo()).post("/eco")
      .set("content-type", "application/json")
      .send("{isso não é json");
    expect(res.status).toBe(400);
  });
});

describe("errorHandler: todo o resto continua 500 genérico", () => {
  it("erro interno não vaza a mensagem", async () => {
    const res = await request(appMinimo((_req, _res, next) => {
      next(new Error("segredo: conexão com o banco recusada em 10.0.0.7"));
    })).post("/eco").send({});
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "Internal server error" });
  });

  it("status 4xx SEM expose continua genérico -- expose é o contrato inteiro", async () => {
    // Um erro interno qualquer pode carregar `status: 422` por coincidência
    // de forma (ex.: erro de client HTTP repassado). Sem expose=true, a
    // mensagem dele não é segura para o cliente.
    const res = await request(appMinimo((_req, _res, next) => {
      const e = new Error("detalhe interno do upstream") as Error & { status: number };
      e.status = 422;
      next(e);
    })).post("/eco").send({});
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "Internal server error" });
  });

  it("expose=true com status 5xx também não passa", async () => {
    // A janela é só 4xx: 5xx exposto não existe no contrato do body-parser,
    // e deixar passar viraria canal de vazamento para quem setar expose.
    const res = await request(appMinimo((_req, _res, next) => {
      const e = new Error("stack interno") as Error & { status: number; expose: boolean };
      e.status = 502; e.expose = true;
      next(e);
    })).post("/eco").send({});
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "Internal server error" });
  });
});
