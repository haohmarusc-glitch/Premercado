import { Router, type IRouter } from "express";
import healthRouter from "./health";
import reportsRouter from "./reports";
import observationsRouter from "./observations";
import agentRouter from "./agent";
import settingsRouter from "./settings";
import runsRouter from "./runs";
import quotesRouter from "./quotes";
import chartRouter from "./chart";
import alertsRouter from "./alerts";
import authRouter from "./auth";
import chatRouter from "./chat";
import portfolioRouter from "./portfolio";
import watchlistRouter from "./watchlist";
import journalRouter from "./journal";
import earningsRouter from "./earnings";
import performanceRouter from "./performance";
import backtestRouter from "./backtest";
import riskRouter from "./risk";
import technicalsRouter from "./technicals";
import analysisRouter from "./analysis";
import internalRouter from "./internal";
import { requireAuth } from "../middleware/auth";

const router: IRouter = Router();

router.use(healthRouter);
router.use(authRouter);
router.use(internalRouter); // localhost-only agent routes, no session required

router.use(requireAuth, reportsRouter);
router.use(requireAuth, quotesRouter);
router.use(requireAuth, chartRouter);
router.use(requireAuth, observationsRouter);
router.use(requireAuth, agentRouter);
router.use(requireAuth, settingsRouter);
router.use(requireAuth, alertsRouter);
router.use(requireAuth, runsRouter);
router.use(requireAuth, chatRouter);
router.use(requireAuth, portfolioRouter);
router.use(requireAuth, watchlistRouter);
router.use(requireAuth, journalRouter);
router.use(requireAuth, earningsRouter);
router.use(requireAuth, performanceRouter);
router.use(requireAuth, backtestRouter);
router.use(requireAuth, riskRouter);
router.use(requireAuth, technicalsRouter);
router.use(requireAuth, analysisRouter);

export default router;
