import type { Request, RequestHandler, Response } from "express";
import { logger } from "../lib/logger";

/**
 * Diz DE ONDE veio um 5xx -- não que ele aconteceu.
 *
 * Em produção apareceu um 500 sem NENHUM log de erro junto. Isso descarta as
 * duas origens que já se identificam sozinhas:
 *
 *   - rotas que respondem 500 direto já logam a própria mensagem
 *     (`logger.error({err}, "Failed: /macro")` e as outras 15);
 *   - rotas que chamam next(e) caem no errorHandler global, que loga
 *     "Unhandled route error".
 *
 * Sobra a camada que ninguém instrumenta: os middlewares ANTES do router
 * (helmet, cors, rate limit, pino-http, parsers). Um 500 vindo dali não passa
 * por rota nenhuma nem pelo errorHandler, então some sem deixar rastro além do
 * status na linha de acesso do pino -- e status sozinho não diz qual camada
 * respondeu.
 *
 * Dois ganchos baratos resolvem, sem tocar em nenhuma das 16 rotas:
 *
 *   - `marcarEntradaNoRouter` monta junto com o router e marca que a request
 *     chegou lá;
 *   - o errorHandler global marca `origem5xx = "errorHandler"`.
 *
 * Com isso a linha de log já vem com o diagnóstico pronto em vez de deixar o
 * leitor deduzir -- que é o ponto: uma mensagem que nomeia a causa errada custa
 * mais que nenhuma mensagem.
 */

/** Valor de `origem` quando a resposta nunca chegou ao router. */
export const ORIGEM_ANTES_DO_ROUTER = "middleware-antes-do-router";
/** Chegou ao router mas respondeu 500 por conta própria (sem next(e)). */
export const ORIGEM_ROTA_DIRETA = "rota-respondeu-direto";
/** Chegou ao errorHandler global via next(e). */
export const ORIGEM_ERROR_HANDLER = "errorHandler";

export function marcarOrigemErrorHandler(res: Response): void {
  res.locals.origem5xx = ORIGEM_ERROR_HANDLER;
}

/** Monte junto com o router: `app.use("/api", marcarEntradaNoRouter, router)`. */
export const marcarEntradaNoRouter: RequestHandler = (_req, res, next) => {
  res.locals.chegouNoRouter = true;
  next();
};

export function origemDoStatus(res: Response): string {
  if (res.locals.origem5xx) return String(res.locals.origem5xx);
  return res.locals.chegouNoRouter ? ORIGEM_ROTA_DIRETA : ORIGEM_ANTES_DO_ROUTER;
}

/**
 * Registre como PRIMEIRO middleware do app -- o gancho é `res.on("finish")`,
 * então ele precisa estar instalado antes de qualquer camada que possa
 * responder sozinha.
 */
export const observar5xx: RequestHandler = (req: Request, res: Response, next) => {
  const inicio = Date.now();
  res.on("finish", () => {
    if (res.statusCode < 500) return;
    logger.error(
      {
        metodo: req.method,
        // originalUrl porque req.url é reescrito pelo mount do router.
        caminho: (req.originalUrl || req.url || "").split("?")[0],
        status: res.statusCode,
        ms: Date.now() - inicio,
        origem: origemDoStatus(res),
      },
      "Resposta 5xx",
    );
  });
  next();
};
