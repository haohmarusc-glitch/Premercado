import { Router, type IRouter } from "express";
import authRouter from "./auth";
import { requireAuth } from "../middleware/require-auth";
import { llmLimiter } from "../middleware/llm-rate-limit";
import healthRouter from "./health";
import reportsRouter from "./reports";
import observationsRouter from "./observations";
import agentRouter from "./agent";
import settingsRouter from "./settings";
import runsRouter from "./runs";
import quotesRouter from "./quotes";
import chartRouter from "./chart";
import alertsRouter from "./alerts";
import chatRouter from "./chat";
import portfolioRouter from "./portfolio";
import watchlistRouter from "./watchlist";
import journalRouter from "./journal";
import exitPlanRouter from "./exit-plan";
import earningsRouter from "./earnings";
import earningsReactionRouter from "./earnings-reaction";
import performanceRouter from "./performance";
import backtestRouter from "./backtest";
import riskRouter from "./risk";
import confluenceRouter from "./confluence";
import technicalsRouter from "./technicals";
import analysisRouter from "./analysis";
import internalRouter from "./internal";
import activityRouter from "./activity";
import adminUsersRouter from "./admin-users";
import aiSpendRouter from "./ai-spend";
import scenariosRouter from "./scenarios";
import scenarioAlertsRouter from "./scenario-alerts";
import checkersRouter from "./checkers";
import entryExitStudyRouter from "./entry-exit-study";
import radarRouter from "./radar";
import macroRiskRouter from "./macro-risk";

const router: IRouter = Router();

router.use(healthRouter);
router.use(internalRouter); // localhost-only agent routes
router.use(authRouter); // login/signup/logout/me/claim -- abertas, sem exigir sessão

// Tudo abaixo exige sessão de login (cookie) OU bearer OPERATOR_API_KEY
// (agente Python / carteira.py) -- ver middleware/require-auth.ts.
router.use(requireAuth);

// Teto estrito só nas rotas que gastam LLM por chamada (ver
// middleware/llm-rate-limit.ts). Aplicado por MÉTODO+CAMINHO exato, nunca
// no router inteiro: GET /agent/status é polado a cada 5s pelo frontend
// enquanto uma run está ativa e estouraria qualquer limite baixo, e
// GET /chat/sessions é leitura barata de histórico.
router.post("/agent/run", llmLimiter);
router.post("/chat/message", llmLimiter);

router.use(reportsRouter);
router.use(quotesRouter);
router.use(chartRouter);
router.use(observationsRouter);
router.use(agentRouter);
router.use(settingsRouter);
router.use(alertsRouter);
router.use(runsRouter);
router.use(chatRouter);
router.use(portfolioRouter);
router.use(watchlistRouter);
router.use(journalRouter);
router.use(exitPlanRouter);
router.use(earningsRouter);
router.use(earningsReactionRouter);
router.use(performanceRouter);
router.use(backtestRouter);
router.use(riskRouter);
router.use(confluenceRouter);
router.use(technicalsRouter);
router.use(analysisRouter);
router.use(activityRouter);
router.use(adminUsersRouter);
router.use(aiSpendRouter);
router.use(scenariosRouter);
router.use(scenarioAlertsRouter);
router.use(checkersRouter); // ciclo de checkers via request (Scheduled Deployment)
router.use(entryExitStudyRouter);
router.use(radarRouter);
router.use(macroRiskRouter);

export default router;
