import { Router, type IRouter } from "express";
import { RunAgentResponse, GetAgentStatusResponse } from "@workspace/api-zod";
import { runAgent, state } from "../lib/runner";

const router: IRouter = Router();

router.post("/agent/run", async (req, res): Promise<void> => {
  if (state.running) {
    res.status(409).json({ error: "Agent already running" });
    return;
  }
  const mode = req.body?.mode === "portfolio" ? "portfolio" : "manual";
  runAgent(mode);
  const message = mode === "portfolio"
    ? "Análise rápida da carteira iniciada. Aguarde a conclusão."
    : "Agente iniciado. Aguarde a conclusão.";
  res.json(RunAgentResponse.parse({ reportId: 0, message }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse(state));
});

export default router;
