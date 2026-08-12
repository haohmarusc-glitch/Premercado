import rateLimit from "express-rate-limit";
import type { RateLimitRequestHandler } from "express-rate-limit";

/**
 * Rate limit dedicado para login/signup.
 *
 * O limiter global (app.ts, 1000 req/15min) é generoso de propósito pra não
 * travar o polling legítimo do frontend -- o que também significa que ele
 * não trava força bruta de senha em /auth/login nem criação automatizada de
 * contas em /auth/signup. Aqui o teto é baixo porque login/signup são ações
 * humanas raras, nunca polling.
 *
 * Duas instâncias separadas (não uma compartilhada): cada rateLimit() tem
 * seu próprio contador, então tentativas de login de um IP não consomem o
 * orçamento de tentativas de signup do mesmo IP, e vice-versa.
 *
 * Mora em middleware/ pelo mesmo motivo de llm-rate-limit.ts: evitar import
 * circular com app.ts via routes/index.ts.
 */
export const authLoginLimiter: RateLimitRequestHandler = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Muitas tentativas de login. Aguarde alguns minutos." },
});

export const authSignupLimiter: RateLimitRequestHandler = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Muitas tentativas de cadastro. Aguarde alguns minutos." },
});
