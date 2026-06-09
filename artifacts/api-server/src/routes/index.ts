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
import { requireAuth } from "../middleware/auth";

const router: IRouter = Router();

router.use(healthRouter);
router.use(reportsRouter);
router.use(quotesRouter);
router.use(chartRouter);
router.use(authRouter);

router.use(requireAuth, observationsRouter);
router.use(requireAuth, agentRouter);
router.use(requireAuth, settingsRouter);
router.use(requireAuth, alertsRouter);
router.use(requireAuth, runsRouter);
router.use(requireAuth, chatRouter);
router.use(requireAuth, portfolioRouter);

export default router;
