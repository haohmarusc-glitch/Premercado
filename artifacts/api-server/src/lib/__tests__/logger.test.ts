/**
 * Trava a redação de err.body no logger -- body-parser anexa o corpo bruto
 * da requisição em err.body quando o JSON é inválido (pra ajudar a debugar o
 * parse). Sem redigir esse campo, um erro de parse em /auth/login grava
 * senha em texto puro no log (visto em produção 13/08: senha real de um
 * usuário apareceu em claro em `docker compose logs` depois de um curl com
 * JSON malformado). Testa a lista `redactPaths` com uma instância pino
 * própria (não o `logger` exportado, que usa transport pino-pretty fora de
 * produção e não produz JSON parseável de volta).
 */
import { describe, it, expect } from "vitest";
import pino from "pino";
import { Writable } from "stream";
import { redactPaths } from "../logger";

function loggerComCaptura() {
  const linhas: string[] = [];
  const destino = new Writable({
    write(chunk, _enc, cb) {
      linhas.push(chunk.toString());
      cb();
    },
  });
  const logger = pino({ redact: redactPaths }, destino);
  return { logger, ultimaLinha: () => JSON.parse(linhas[linhas.length - 1]) };
}

describe("logger redactPaths", () => {
  it("redige err.body (corpo bruto anexado pelo body-parser em erro de JSON)", () => {
    const { logger, ultimaLinha } = loggerComCaptura();
    const err = new Error("Expected property name or '}' in JSON") as Error & { body?: string; statusCode?: number };
    err.body = '{email:"x@x.com",password:"segredo123"}';
    err.statusCode = 400;

    logger.error({ err }, "Unhandled route error");

    const log = ultimaLinha();
    expect(log.err.body).toBe("[Redacted]");
    expect(JSON.stringify(log)).not.toContain("segredo123");
  });

  it("preserva message/stack/statusCode -- não é um apagão do erro inteiro", () => {
    const { logger, ultimaLinha } = loggerComCaptura();
    const err = new Error("boom") as Error & { body?: string; statusCode?: number };
    err.body = "qualquer coisa";
    err.statusCode = 400;

    logger.error({ err }, "Unhandled route error");

    const log = ultimaLinha();
    expect(log.err.message).toBe("boom");
    expect(typeof log.err.stack).toBe("string");
    expect(log.err.statusCode).toBe(400);
  });

  it("redige authorization/cookie (comportamento pré-existente, não regrediu)", () => {
    const { logger, ultimaLinha } = loggerComCaptura();
    logger.error({ req: { headers: { authorization: "Bearer segredo", cookie: "session=abc" } } }, "x");

    const log = ultimaLinha();
    expect(log.req.headers.authorization).toBe("[Redacted]");
    expect(log.req.headers.cookie).toBe("[Redacted]");
  });
});
