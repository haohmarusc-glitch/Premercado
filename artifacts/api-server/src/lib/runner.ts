/**
 * Shared agent runner — used by both the HTTP route and the scheduler.
 * Spawns the Python subprocess, records the run in DB, saves the report, and sends e-mail.
 */
import { spawn } from "child_process";
import { existsSync } from "fs";
import path from "path";
import { eq } from "drizzle-orm";
import { db, reportsTable, agentRunsTable, settingsTable } from "@workspace/db";
import { logger } from "./logger";
import { sendReportEmail } from "./mailer";

const DEFAULT_TICKERS = [
  "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
  "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML",
];

async function getMonitoredTickers(): Promise<string[]> {
  try {
    const [settings] = await db.select().from(settingsTable).limit(1);
    if (settings && settings.tickers.length > 0) return settings.tickers;
  } catch (err) {
    logger.error({ err }, "Failed to read tickers from settings; using defaults");
  }
  return DEFAULT_TICKERS;
}

const workspaceRoot = process.cwd().endsWith(
  path.join("artifacts", "api-server"),
)
  ? path.resolve(process.cwd(), "../..")
  : process.cwd();

export const agentDir = path.resolve(workspaceRoot, "artifacts/api-server/src");

// Use venv Python if one exists at the project root (created with `uv venv .venv`).
const _venvDir = path.resolve(workspaceRoot, ".venv");
const _venvBin = path.join(_venvDir, "bin");
const _venvPython = path.join(_venvBin, "python3");

// Check venv existence at call time so the server survives venv recreation without restart.
export function getPythonBin(): string {
  return existsSync(_venvPython) ? _venvPython : "python3";
}
export function getVenvEnv(): Record<string, string> {
  return existsSync(_venvPython)
    ? { VIRTUAL_ENV: _venvDir, PATH: `${_venvBin}:${process.env.PATH ?? ""}` }
    : {};
}

// Legacy exports kept for import compatibility.
export const pythonBin = _venvPython; // caller should prefer getPythonBin()
export const venvEnv: Record<string, string> = {};

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

export function runAgent(trigger: "manual" | "scheduled" | "premarket" = "manual"): void {
  if (state.running) {
    logger.warn("Agent already running — skipping trigger");
    return;
  }

  const mode = trigger === "premarket" ? "premarket" : "daily";

  state.running = true;
  state.currentStep = trigger === "premarket" ? "Iniciando varredura pré-mercado..." : "Iniciando agente...";
  state.lastRunAt = new Date().toISOString();

  const startedAt = new Date();
  let runId: number | null = null;

  void (async () => {
  try {
  const tickers = await getMonitoredTickers();

  // Insert run record (awaited so runId is set deterministically before the process can close)
  try {
    const [row] = await db
      .insert(agentRunsTable)
      .values({ status: "running", trigger, mode, startedAt })
      .returning();
    runId = row.id;
  } catch (err) {
    logger.error({ err }, "Failed to insert agent run record");
  }

  const apiUrl = `http://localhost:${process.env.PORT ?? 5000}`;

  const py = spawn(getPythonBin(), ["-m", "agent.run_agent"], {
    cwd: agentDir,
    env: {
      ...process.env,
      ...getVenvEnv(),
      INTERNAL_API_URL: apiUrl,
      PYTHONPATH: agentDir,
      AGENT_TICKERS: tickers.join(","),
      AGENT_MODE: mode,
      OPERATOR_API_KEY: process.env.OPERATOR_API_KEY ?? "",
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

  py.on("error", (err) => {
    logger.error({ err }, "Failed to spawn agent process");
    state.running = false;
    state.currentStep = null;
  });

  py.on("close", async (code) => {
    state.running = false;
    state.currentStep = null;

    const finishedAt = new Date();
    const durationMs = finishedAt.getTime() - startedAt.getTime();

    if (code !== 0) {
      logger.error({ code, errorOutput }, "Agent process exited with error");
      if (runId !== null) {
        await db
          .update(agentRunsTable)
          .set({ status: "failed", finishedAt, durationMs, errorMessage: errorOutput.slice(0, 2000) })
          .where(eq(agentRunsTable.id, runId))
          .catch((err) => logger.error({ err }, "Failed to update failed run record"));
      }
      return;
    }

    const reportMatch = output.match(/REPORT:([\s\S]+)/);
    const content = reportMatch ? reportMatch[1].trim() : output.trim();

    if (!content) {
      logger.warn("Agent produced no report content");
      if (runId !== null) {
        await db
          .update(agentRunsTable)
          .set({ status: "failed", finishedAt, durationMs, errorMessage: "No report content produced" })
          .where(eq(agentRunsTable.id, runId))
          .catch((err) => logger.error({ err }, "Failed to update empty run record"));
      }
      return;
    }

    const today = new Date().toISOString().split("T")[0];

    // Save report to DB
    try {
      await db.insert(reportsTable).values({
        date: today,
        content,
        tickers,
        mode,
      });
      logger.info("Report saved to database");
    } catch (err) {
      logger.error({ err }, "Failed to save report to database");
    }

    // Mark run as success
    if (runId !== null) {
      await db
        .update(agentRunsTable)
        .set({ status: "success", finishedAt, durationMs })
        .where(eq(agentRunsTable.id, runId))
        .catch((err) => logger.error({ err }, "Failed to update success run record"));
    }

    // Send e-mail notification
    await sendReportEmail(content, today, tickers);
  });
  } catch (err) {
    logger.error({ err }, "Unexpected error while running agent");
    state.running = false;
    state.currentStep = null;
  }
  })();
}

