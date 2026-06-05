import { Router, type IRouter } from "express";
import healthRouter from "./health";
import reportsRouter from "./reports";
import observationsRouter from "./observations";
import agentRouter from "./agent";
import settingsRouter from "./settings";

const router: IRouter = Router();

router.use(healthRouter);
router.use(reportsRouter);
router.use(observationsRouter);
router.use(agentRouter);
router.use(settingsRouter);

export default router;
