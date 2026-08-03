import express, { type Express, type ErrorRequestHandler } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import cookieParser from "cookie-parser";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";
import {
  observar5xx,
  marcarEntradaNoRouter,
  marcarOrigemErrorHandler,
} from "./middleware/observar-5xx";

const app: Express = express();

// PRIMEIRO de tudo, de propósito: o gancho é res.on("finish"), então precisa
// estar instalado antes de qualquer camada que possa responder sozinha (helmet,
// cors, rate limit, parsers). Ver observar-5xx.ts.
app.use(observar5xx);

// UM hop de proxy confiável (o do Replit, que termina o TLS e põe o
// X-Forwarded-For). Sem isto o Express deixa `trust proxy` em false, req.ip
// vira o endereço do PRÓPRIO proxy, e os dois rate limiters abaixo passam a
// keyar todo mundo no mesmo bucket -- o llmLimiter (30/15min) deixaria de ser
// por cliente e viraria um teto global. O express-rate-limit detecta a
// incoerência e loga ERR_ERL_UNEXPECTED_X_FORWARDED_FOR a cada request.
//
// Precisa ser 1, NUNCA `true`: com `true` o Express confia na cadeia inteira de
// X-Forwarded-For, e aí qualquer cliente forja o header pra ganhar um bucket
// novo a cada request, desligando na prática o limite de custo de LLM.
app.set("trust proxy", 1);

// crossOriginResourcePolicy "cross-origin" -- o padrao do helmet
// ("same-origin") bloqueia fetch() do frontend quando ele roda em origem
// diferente do backend (exatamente o caso que o cors() abaixo ja permite
// explicitamente via ALLOWED_ORIGINS); sem isso o helmet quebraria requests
// que o CORS deveria continuar liberando.
app.use(helmet({ crossOriginResourcePolicy: { policy: "cross-origin" } }));

// Configurar origens permitidas do CORS -- sempre array, mesmo no fallback,
// pra nao misturar string/array na mesma variavel (cors() aceita os dois,
// mas isso evitava previsibilidade de tipo em qualquer outro uso futuro).
const allowedOrigins = process.env.ALLOWED_ORIGINS
  ? process.env.ALLOWED_ORIGINS.split(",")
  : ["http://localhost:3000"];

// Rate limit global -- barreira contra abuso genérico (ex.: força bruta de
// login). Precisa ser GENEROSO porque a maior parte do tráfego normal é
// polling barato do frontend, não ação do usuário: só o layout já faz
// alertas (60s) + cotações (60s) + plano de saída (5min) + status do agente
// (5s enquanto uma run está ativa), e páginas como /quotes-bubbles e
// /grafico somam mais um poll de 15s. Com o agente rodando isso passa de
// 280 req/15min só de polling -- o teto anterior (300) encostava nisso e
// começaria a devolver 429 em uso legítimo. Custo de LLM NÃO é contido
// aqui: quem faz isso é o llmLimiter (middleware/llm-rate-limit.ts),
// aplicado em routes/index.ts só onde há gasto real.
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 1000,
  standardHeaders: true,
  legacyHeaders: false,
});
app.use(limiter);

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);

app.use(
  cors({
    credentials: true,
    origin: allowedOrigins,
  }),
);

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());

app.use("/api", marcarEntradaNoRouter, router);

// Handler de erro global -- rotas devem chamar next(e) no catch em vez de
// responder o erro cru (String(e) vazava mensagem/stack interno pro
// cliente). O rastreio completo vai só pro Pino; o cliente recebe sempre
// uma mensagem genérica. Precisa vir depois de todas as rotas e ter
// exatamente 4 parâmetros para o Express reconhecer como error handler.
const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
  logger.error({ err }, "Unhandled route error");
  marcarOrigemErrorHandler(res);
  if (res.headersSent) return;
  res.status(500).json({ error: "Internal server error" });
};
app.use(errorHandler);

export default app;
