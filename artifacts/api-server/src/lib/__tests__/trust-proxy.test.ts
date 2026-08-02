/**
 * Trava a configuração de `trust proxy` do Express.
 *
 * Sem ela o Express deixa o default (false), req.ip vira o endereço do próprio
 * proxy do Replit, e os dois rate limiters passam a keyar todos os clientes no
 * mesmo bucket -- o llmLimiter (30/15min em POST /agent/run e POST
 * /chat/message) deixa de ser por cliente e vira um teto global. O
 * express-rate-limit detecta a incoerência e loga
 * ERR_ERL_UNEXPECTED_X_FORWARDED_FOR a cada request (visto em produção 02/08).
 *
 * O valor precisa ser exatamente 1 (um hop). Com `true` o Express confia na
 * cadeia inteira de X-Forwarded-For, e aí qualquer cliente forja o header pra
 * ganhar bucket novo a cada request -- trocaria o ruído de log por um bypass
 * do limite de custo de LLM, que é justamente o que ele existe pra conter.
 *
 * app.ts importa a cadeia inteira de rotas, que chega no @workspace/db e exige
 * DATABASE_URL já no import. Por isso a env é stubada ANTES do import dinâmico:
 * o drizzle só abre conexão de verdade na primeira query, e este teste não faz
 * nenhuma.
 */
import { describe, it, expect, beforeAll, vi } from "vitest";
import type { Express } from "express";

let app: Express;

beforeAll(async () => {
  if (!process.env.DATABASE_URL) {
    vi.stubEnv("DATABASE_URL", "postgres://user:pass@localhost:5432/naousado");
  }
  app = (await import("../../app")).default;
});

describe("trust proxy", () => {
  it("confia em exatamente um hop de proxy", () => {
    expect(app.get("trust proxy")).toBe(1);
  });

  it("não confia na cadeia inteira (evita X-Forwarded-For forjado)", () => {
    expect(app.get("trust proxy")).not.toBe(true);
  });
});
