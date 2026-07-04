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

const router: IRouter = Router();

router.use(healthRouter);
router.use(internalRouter); // localhost-only agent routes

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
router.use(earningsRouter);
router.use(performanceRouter);
router.use(backtestRouter);
router.use(riskRouter);
router.use(technicalsRouter);
router.use(analysisRouter);

export default router;
