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

const router: IRouter = Router();

router.use(healthRouter);
router.use(reportsRouter);
router.use(observationsRouter);
router.use(agentRouter);
router.use(settingsRouter);
router.use(runsRouter);
router.use(quotesRouter);
router.use(chartRouter);
router.use(alertsRouter);

export default router;
