import { Router, type IRouter } from "express";
import { desc } from "drizzle-orm";
import { db, reportsTable } from "@workspace/db";
import { RunAgentResponse, GetAgentStatusResponse } from "@workspace/api-zod";
import { logger } from "../lib/logger";
import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const router: IRouter = Router();

interface AgentState {
  running: boolean;
  lastRunAt: string | null;
  currentStep: string | null;
}

const state: AgentState = {
  running: false,
  lastRunAt: null,
  currentStep: null,
};

const workspaceRoot = process.cwd().endsWith(path.join("artifacts", "api-server"))
  ? path.resolve(process.cwd(), "../..")
  : process.cwd();

const agentDir = path.resolve(workspaceRoot, "artifacts/api-server/src");

router.post("/agent/run", async (req, res): Promise<void> => {
  if (state.running) {
    res.status(409).json({ error: "Agent already running" });
    return;
  }

  state.running = true;
  state.currentStep = "Iniciando agente...";
  state.lastRunAt = new Date().toISOString();

  // Run the Python agent as a subprocess
  const apiUrl = `http://localhost:${process.env.PORT ?? 5000}`;

  const py = spawn("python3", ["-m", "agent.run_agent"], {
    cwd: agentDir,
    env: {
      ...process.env,
      INTERNAL_API_URL: apiUrl,
      PYTHONPATH: agentDir,
    },
  });

  let output = "";
  let errorOutput = "";

  py.stdout.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    logger.info({ line }, "Agent stdout");
    if (line.startsWith("STEP:")) {
      state.currentStep = line.replace("STEP:", "").trim();
    }
    output += data.toString();
  });

  py.stderr.on("data", (data: Buffer) => {
    errorOutput += data.toString();
    logger.warn({ stderr: data.toString() }, "Agent stderr");
  });

  py.on("close", async (code) => {
    state.running = false;
    state.currentStep = null;
    if (code !== 0) {
      logger.error({ code, errorOutput }, "Agent process exited with error");
      return;
    }
    // Extract the report content (everything after "REPORT:")
    const reportMatch = output.match(/REPORT:([\s\S]+)/);
    const content = reportMatch ? reportMatch[1].trim() : output.trim();
    if (content) {
      try {
        const today = new Date().toISOString().split("T")[0];
        await db.insert(reportsTable).values({
          date: today,
          content,
          tickers: ["MU", "SMCI"],
        });
        logger.info("Report saved to database");
      } catch (err) {
        logger.error({ err }, "Failed to save report");
      }
    }
  });

  // Return immediately with a pending response
  // The report ID will be fetched by polling /reports/latest
  res.json(RunAgentResponse.parse({ reportId: 0, message: "Agente iniciado. Aguarde a conclusão." }));
});

router.get("/agent/status", (_req, res): void => {
  res.json(GetAgentStatusResponse.parse(state));
});

export default router;
