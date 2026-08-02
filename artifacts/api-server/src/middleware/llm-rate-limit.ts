import rateLimit from "express-rate-limit";
import type { RateLimitRequestHandler } from "express-rate-limit";

/**
 * Rate limit estrito das rotas que gastam LLM por chamada (POST /agent/run,
 * POST /chat/message).
 *
 * Fica separado do limiter global de app.ts porque os dois protegem coisas
 * diferentes e precisam de tetos MUITO diferentes:
 *
 * - O global existe contra abuso genérico (ex.: força bruta de login) e
 *   precisa ser generoso, já que a maior parte do tráfego normal é polling
 *   barato do frontend (status do agente a cada 5s durante uma run, cotações
 *   e alertas a cada 60s, etc.) -- apertá-lo devolveria 429 em uso legítimo.
 * - Este aqui existe contra gasto de LLM. As rotas cobertas são disparadas
 *   por uma AÇÃO humana (clicar "rodar agente", enviar uma mensagem no
 *   chat), nunca por polling, então um teto baixo não atrapalha o uso normal
 *   e limita o prejuízo se algo passar a chamar em loop.
 *
 * Mora em middleware/ (e não em app.ts) pra evitar import circular: app.ts
 * já importa routes/index.ts, que é justamente quem precisa aplicar isto.
 */
export const llmLimiter: RateLimitRequestHandler = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 30,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Muitas requisições que consomem IA. Aguarde alguns minutos." },
});
