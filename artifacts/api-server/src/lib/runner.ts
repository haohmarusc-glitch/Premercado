/**
 * Shared agent runner — used by both the HTTP route and the scheduler.
 * Spawns the Python subprocess, records the run in DB, saves the report, and sends e-mail.
 */
import { spawn } from "child_process";
import path from "path";
import { existsSync } from "fs";
import { asc, eq } from "drizzle-orm";
import { db, reportsTable, agentRunsTable, settingsTable, portfolioPositionsTable } from "@workspace/db";
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

async function getPortfolioTickers(): Promise<string[]> {
  try {
    const rows = await db
      .select({ ticker: portfolioPositionsTable.ticker })
      .from(portfolioPositionsTable)
      .orderBy(asc(portfolioPositionsTable.createdAt));
    if (rows.length > 0) return rows.map((r) => r.ticker);
  } catch (err) {
    logger.error({ err }, "Failed to read portfolio tickers; using defaults");
  }
  return ["NVDA", "MU", "INTC", "ARM", "GOOGL", "TSLA", "SMCI"];
}

const workspaceRoot = process.cwd().endsWith(
  path.join("artifacts", "api-server"),
)
  ? path.resolve(process.cwd(), "../..")
  : process.cwd();

export const agentDir = path.resolve(workspaceRoot, "artifacts/api-server/src");

export function getPythonBin(): string {
  const venvPython = path.resolve(workspaceRoot, ".venv/bin/python");
  return existsSync(venvPython) ? venvPython : "python3";
}

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

export function runAgent(trigger: "manual" | "scheduled" | "premarket" | "portfolio" = "manual", maxTurns?: number): void {
  if (state.running) {
    logger.warn("Agent already running — skipping trigger");
    return;
  }

  const mode = trigger === "premarket" ? "premarket" : trigger === "portfolio" ? "portfolio" : "daily";

  state.running = true;
  state.currentStep =
    trigger === "premarket" ? "Iniciando varredura pré-mercado..." :
    trigger === "portfolio" ? "Iniciando análise rápida da carteira..." :
    "Iniciando agente...";
  state.lastRunAt = new Date().toISOString();

  const startedAt = new Date();
  let runId: number | null = null;

  void (async () => {
  try {
  const tickers = trigger === "portfolio"
    ? await getPortfolioTickers()
    : await getMonitoredTickers();

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
      INTERNAL_API_URL: apiUrl,
      PYTHONPATH: agentDir,
      AGENT_TICKERS: tickers.join(","),
      AGENT_PORTFOLIO_TICKERS: trigger === "portfolio" ? tickers.join(",") : (process.env.AGENT_PORTFOLIO_TICKERS ?? ""),
      AGENT_MODE: mode,
      ...(maxTurns !== undefined ? { AGENT_MAX_TURNS: String(maxTurns) } : {}),
      OPERATOR_API_KEY: process.env.OPERATOR_API_KEY ?? "",
    },
  });

  const TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes
  const killTimer = setTimeout(() => {
    logger.warn("Agent timeout (10 min) — killing process");
    py.kill("SIGTERM");
    state.currentStep = "Tempo limite atingido — encerrando...";
  }, TIMEOUT_MS);

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
    clearTimeout(killTimer);
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

