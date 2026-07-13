import express, { type Express, type ErrorRequestHandler } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import cookieParser from "cookie-parser";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

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

// Rate limit global -- protege rotas de auth (força bruta de login) e rotas
// que custam LLM por chamada (agente/chat) de serem esgotadas por excesso de
// requisições. Generoso o bastante pra uso normal de um usuario so'.
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 300,
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

app.use("/api", router);

// Handler de erro global -- rotas devem chamar next(e) no catch em vez de
// responder o erro cru (String(e) vazava mensagem/stack interno pro
// cliente). O rastreio completo vai só pro Pino; o cliente recebe sempre
// uma mensagem genérica. Precisa vir depois de todas as rotas e ter
// exatamente 4 parâmetros para o Express reconhecer como error handler.
const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
  logger.error({ err }, "Unhandled route error");
  if (res.headersSent) return;
  res.status(500).json({ error: "Internal server error" });
};
app.use(errorHandler);

export default app;
