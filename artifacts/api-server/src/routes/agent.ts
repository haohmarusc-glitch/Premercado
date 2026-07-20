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
  // "scheduled" é usado pelo Replit Scheduled Deployment externo (ver
  // scripts/trigger-scheduled-run.sh) -- acorda o deploy Autoscale via HTTP
  // e dispara a mesma análise diária completa que o node-cron interno
  // (scheduler.ts) chamaria, só que sem depender do processo já estar
  // acordado no horário exato. Mantém o rótulo correto no histórico de runs
  // em vez de aparecer como "manual".
  const mode = rawMode === "portfolio" ? "portfolio" : rawMode === "premarket" ? "premarket" : rawMode === "coal" ? "coal" : rawMode === "ai" ? "ai" : rawMode === "news" ? "news" : rawMode === "scheduled" ? "scheduled" : "manual";
  const maxTurns = typeof req.body?.maxTurns === "number" ? req.body.maxTurns : undefined;
  runAgent(mode, maxTurns);
  const message =
    mode === "portfolio" ? "Análise rápida da carteira iniciada. Aguarde a conclusão." :
    mode === "premarket" ? "Varredura pré-mercado iniciada. Aguarde a conclusão." :
    mode === "coal" ? "Análise do setor de carvão iniciada. Aguarde a conclusão." :
    mode === "ai" ? "Análise do setor de IA iniciada. Aguarde a conclusão." :
    mode === "news" ? "Varredura de notícias iniciada. Aguarde a conclusão." :
    "Agente iniciado. Aguarde a conclusão.";
  res.json(RunAgentResponse.parse({ reportId: 0, message }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse({ ...state, uptimeSeconds: process.uptime() }));
});

export default router;
