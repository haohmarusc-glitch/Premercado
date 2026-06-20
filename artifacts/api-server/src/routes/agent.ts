import { Router, type IRouter } from "express";
import { RunAgentResponse, GetAgentStatusResponse } from "@workspace/api-zod";
import { runAgent, state } from "../lib/runner";

const router: IRouter = Router();

router.post("/agent/run", async (req, res): Promise<void> => {
  if (state.running) {
    res.status(409).json({ error: "Agent already running" });
    return;
  }
  const rawMode = req.body?.mode;
  const mode = rawMode === "portfolio" ? "portfolio" : rawMode === "premarket" ? "premarket" : rawMode === "coal" ? "coal" : "manual";
  const maxTurns = typeof req.body?.maxTurns === "number" ? req.body.maxTurns : undefined;
  runAgent(mode, maxTurns);
  const message =
    mode === "portfolio" ? "Análise rápida da carteira iniciada. Aguarde a conclusão." :
    mode === "premarket" ? "Varredura pré-mercado iniciada. Aguarde a conclusão." :
    mode === "coal" ? "Análise do setor de carvão iniciada. Aguarde a conclusão." :
    "Agente iniciado. Aguarde a conclusão.";
  res.json(RunAgentResponse.parse({ reportId: 0, message }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse(state));
});

export default router;
