/**
 * Shared agent runner — used by both the HTTP route and the scheduler.
 * Spawns the Python subprocess, saves the report to the DB, and sends e-mail.
 */
import { spawn } from "child_process";
import path from "path";
import { db, reportsTable } from "@workspace/db";
import { logger } from "./logger";
import { sendReportEmail } from "./mailer";

const workspaceRoot = process.cwd().endsWith(
  path.join("artifacts", "api-server"),
)
  ? path.resolve(process.cwd(), "../..")
  : process.cwd();

export const agentDir = path.resolve(workspaceRoot, "artifacts/api-server/src");

export interface AgentState {
  running: boolean;
  lastRunAt: string | null;
  currentStep: string | null;
  nextRunAt: string | null;
  scheduleEnabled: boolean;
}

export const state: AgentState = {
  running: false,
  lastRunAt: null,
  currentStep: null,
  nextRunAt: null,
  scheduleEnabled: true,
};

export function runAgent(): void {
  if (state.running) {
    logger.warn("Agent already running — skipping trigger");
    return;
  }

  state.running = true;
  state.currentStep = "Iniciando agente...";
  state.lastRunAt = new Date().toISOString();

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

    const reportMatch = output.match(/REPORT:([\s\S]+)/);
    const content = reportMatch ? reportMatch[1].trim() : output.trim();

    if (!content) {
      logger.warn("Agent produced no report content");
      return;
    }

    const today = new Date().toISOString().split("T")[0];

    // Save to DB
    try {
      await db.insert(reportsTable).values({
        date: today,
        content,
        tickers: ["MU", "SMCI"],
      });
      logger.info("Report saved to database");
    } catch (err) {
      logger.error({ err }, "Failed to save report to database");
    }

    // Send e-mail notification
    await sendReportEmail(content, today);
  });
}
