import { Router, type IRouter } from "express";
import { RunAgentResponse, GetAgentStatusResponse } from "@workspace/api-zod";
import { runAgent, state } from "../lib/runner";
import { isAdminUser } from "../middleware/require-auth";

const router: IRouter = Router();

// Modos que rodam sobre os DADOS DE QUEM CHAMA (carteira própria) -- ver
// req.userId sendo passado pra runAgent() abaixo. Qualquer usuário
// autenticado pode disparar estes, mesmo sem ser admin.
const MODOS_PROPRIOS_DO_USUARIO = new Set(["portfolio", "veredito"]);

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
  const mode = rawMode === "portfolio" ? "portfolio" : rawMode === "premarket" ? "premarket" : rawMode === "coal" ? "coal" : rawMode === "ai" ? "ai" : rawMode === "news" ? "news" : rawMode === "exit_plan" ? "exit_plan" : rawMode === "alerts" ? "alerts" : rawMode === "veredito" ? "veredito" : rawMode === "consensus" ? "consensus" : rawMode === "scheduled" ? "scheduled" : "manual";

  // requireAdmin só para modos COMPARTILHADOS (premarket, news, ai, etc.) --
  // eles disparam trabalho caro sobre TODOS os usuários e consomem o
  // orçamento diário de LLM global. Sem isto, qualquer usuário autenticado
  // -- inclusive um cadastro público via /auth/signup -- podia queimar esse
  // orçamento. "portfolio"/"veredito" ficam de fora porque rodam só sobre a
  // carteira de quem chamou (req.userId, repassado pra runAgent() abaixo).
  if (!MODOS_PROPRIOS_DO_USUARIO.has(mode) && !(await isAdminUser(req.userId!))) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }

  const maxTurns = typeof req.body?.maxTurns === "number" ? req.body.maxTurns : undefined;
  runAgent(mode, maxTurns, req.userId);
  const message =
    mode === "portfolio" ? "Análise rápida da carteira iniciada. Aguarde a conclusão." :
    mode === "premarket" ? "Varredura pré-mercado iniciada. Aguarde a conclusão." :
    mode === "coal" ? "Análise do setor de carvão iniciada. Aguarde a conclusão." :
    mode === "ai" ? "Análise do setor de IA iniciada. Aguarde a conclusão." :
    mode === "news" ? "Varredura de notícias iniciada. Aguarde a conclusão." :
    mode === "exit_plan" ? "Reavaliação do plano de saída iniciada. Aguarde a conclusão." :
    mode === "alerts" ? "Gestão de alertas iniciada. Aguarde a conclusão." :
    mode === "veredito" ? "Gerando veredito do dia. Aguarde a conclusão." :
    mode === "consensus" ? "Relatório de consenso (3 provedores) iniciado. Aguarde a conclusão." :
    "Agente iniciado. Aguarde a conclusão.";
  res.json(RunAgentResponse.parse({ reportId: 0, message }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse({ ...state, uptimeSeconds: process.uptime() }));
});

export default router;
