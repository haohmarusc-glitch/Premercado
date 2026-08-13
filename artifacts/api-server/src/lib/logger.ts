import pino from "pino";

const isProduction = process.env.NODE_ENV === "production";

// Exportado à parte (não só inline no pino()) pra dar pra testar a lista em
// lib/__tests__/logger.test.ts sem depender do transport pino-pretty (que só
// existe fora de produção e não produz JSON parseável).
export const redactPaths = [
  "req.headers.authorization",
  "req.headers.cookie",
  "res.headers['set-cookie']",
  // body-parser anexa o corpo bruto da requisição em err.body quando o
  // JSON é inválido (pra ajudar a debugar o parse) -- sem isso, um erro de
  // parse em /auth/login (ou qualquer rota futura com campo sensível)
  // grava senha/token em texto puro no log (visto em produção).
  "err.body",
];

export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  redact: redactPaths,
  ...(isProduction
    ? {}
    : {
        transport: {
          target: "pino-pretty",
          options: { colorize: true },
        },
      }),
});
