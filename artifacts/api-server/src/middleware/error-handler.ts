import type { ErrorRequestHandler } from "express";
import { logger } from "../lib/logger";
import { marcarOrigemErrorHandler } from "./observar-5xx";

// Handler de erro global -- rotas devem chamar next(e) no catch em vez de
// responder o erro cru (String(e) vazava mensagem/stack interno pro
// cliente). O rastreio completo vai só pro Pino; o cliente recebe sempre
// uma mensagem genérica. Precisa vir depois de todas as rotas e ter
// exatamente 4 parâmetros para o Express reconhecer como error handler.
//
// Módulo próprio (e não inline no app.ts) porque o app inteiro não importa
// sem banco -- e este handler precisa de teste comportamental: a ÚNICA
// distinção que ele faz (expose) é fácil de inverter sem nenhum teste piscar.
export const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
  // err.body (corpo bruto que body-parser anexa quando o JSON é inválido,
  // pra ajudar a debugar o parse) é redigido em lib/logger.ts -- vaza
  // literalmente qualquer coisa que o cliente mandou, incluindo senha em
  // texto puro em /auth/login (visto em produção: senha real gravada em
  // claro no log depois de um erro de parse).
  logger.error({ err }, "Unhandled route error");
  marcarOrigemErrorHandler(res);
  if (res.headersSent) return;
  // Erro de CLIENTE que o middleware já classificou (body-parser marca
  // status + expose=true: 413 payload grande, 400 JSON inválido) responde
  // com o status e a mensagem DELE. Antes tudo virava 500 "Internal server
  // error" -- visto em 20/08/2026: a tela mandou 107KB contra o limite de
  // 100KB do express.json e o usuário leu "erro interno do servidor" para um
  // problema que era do tamanho do corpo, investigável só por quem abrisse o
  // log. `expose` é o contrato do http-errors para "esta mensagem é segura
  // para o cliente"; sem ele, continua genérico -- vazar mensagem interna é
  // o bug que este handler existe para impedir.
  const status = Number((err as { status?: unknown })?.status);
  if (Number.isInteger(status) && status >= 400 && status < 500
      && (err as { expose?: boolean })?.expose === true) {
    res.status(status).json({ error: String((err as Error).message || "Request error") });
    return;
  }
  res.status(500).json({ error: "Internal server error" });
};
