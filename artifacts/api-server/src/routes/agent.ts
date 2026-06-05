import { Router, type IRouter } from "express";
import { RunAgentResponse, GetAgentStatusResponse } from "@workspace/api-zod";
import { runAgent, state } from "../lib/runner";

const router: IRouter = Router();

router.post("/agent/run", async (req, res): Promise<void> => {
  if (state.running) {
    res.status(409).json({ error: "Agent already running" });
    return;
  }
  runAgent();
  res.json(RunAgentResponse.parse({ reportId: 0, message: "Agente iniciado. Aguarde a conclusão." }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse(state));
});

export default router;
