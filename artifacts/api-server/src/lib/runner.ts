/**
 * Shared agent runner — used by both the HTTP route and the scheduler.
 * Spawns the Python subprocess, records the run in DB, saves the report, and sends e-mail.
 */
import { spawn } from "child_process";
import path from "path";
import { existsSync } from "fs";
import { asc, eq, gte } from "drizzle-orm";
import { db, reportsTable, agentRunsTable, settingsTable, portfolioPositionsTable } from "@workspace/db";
import { logger } from "./logger";
import { sendReportEmail } from "./mailer";
import { startOfTodayBRT, todayBRTDateString } from "./timezone";
import { isActivePosition } from "./portfolio-math";

const DEFAULT_TICKERS = [
  "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
  "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML",
  "HCC", "AMR",
];

const COAL_TICKERS = ["HCC", "AMR", "ARCH", "CEIX", "BTU"];
const AI_TICKERS   = ["NVDA", "ARM", "GOOGL", "META", "MSFT", "AMD", "PLTR", "SMCI"];

async function getMonitoredTickers(): Promise<string[]> {
  try {
    const [settings] = await db.select().from(settingsTable).limit(1);
    if (settings && settings.tickers.length > 0) return settings.tickers;
  } catch (err) {
    logger.error({ err }, "Failed to read tickers from settings; using defaults");
  }
  return DEFAULT_TICKERS;
}

// Provedor manual configurado pelo usuário (ou undefined = ordem padrão do
// provider.py, anthropic primeiro), rebaixado para o provedor barato quando o
// gasto de hoje (horário de Brasília) no provedor primário atinge o teto diário.
async function getEffectiveAgentProvider(): Promise<string | undefined> {
  try {
    const [settings] = await db.select().from(settingsTable).limit(1);
    if (!settings || settings.dailyBudgetUsd == null) return settings?.agentProvider ?? undefined;

    const primary = settings.agentProvider || "anthropic";
    const runs = await db
      .select({ costUsd: agentRunsTable.costUsd, llmProvider: agentRunsTable.llmProvider })
      .from(agentRunsTable)
      .where(gte(agentRunsTable.startedAt, startOfTodayBRT()));
    // O driver pg devolve `numeric` como string — converter antes de somar.
    const spentToday = runs
      .filter((r) => (r.llmProvider ?? "").split(",").includes(primary))
      .reduce((sum, r) => sum + (r.costUsd === null ? 0 : Number(r.costUsd)), 0);
    const dailyBudgetUsd = Number(settings.dailyBudgetUsd);

    if (spentToday >= dailyBudgetUsd) {
      logger.warn(
        { primary, spentToday, dailyBudgetUsd, cheapProvider: settings.cheapProvider },
        "Daily budget exceeded for primary provider — switching to cheap provider for the rest of the day",
      );
      return settings.cheapProvider;
    }
    return settings.agentProvider ?? undefined;
  } catch (err) {
    logger.error({ err }, "Failed to compute effective agent provider; using default order");
    return undefined;
  }
}

async function getPortfolioTickers(): Promise<string[]> {
  try {
    const rows = await db
      .select({ ticker: portfolioPositionsTable.ticker, isEtf: portfolioPositionsTable.isEtf, quantity: portfolioPositionsTable.quantity })
      .from(portfolioPositionsTable)
      .orderBy(asc(portfolioPositionsTable.createdAt));
    // ETFs (ex.: SGOV) ficam de fora da análise de carteira -- são
    // instrumentos de caixa, sem notícia/sentimento pra analisar como uma
    // ação real, e o fluxo (news, technicals, candle patterns, etc.) não faz
    // sentido pra eles. Posições totalmente vendidas (quantity = 0 -- ver
    // recomputePosition em routes/portfolio.ts) também ficam de fora: a
    // linha continua no banco só pra preservar o histórico de compra/venda
    // exibido na Carteira, não representa mais um ativo realmente possuído.
    const stocks = rows.filter((r) => !r.isEtf && isActivePosition(r.quantity));
    if (stocks.length > 0) return stocks.map((r) => r.ticker);
  } catch (err) {
    logger.error({ err }, "Failed to read portfolio tickers; using defaults");
  }
  // Carteira real (Nomad) conferida posição a posição em 17/07 -- MU e INTC
  // foram vendidos, AVGO/MRVL/SKHY são posições novas (ver config.py,
  // PORTFOLIO_TICKERS, mesma lista mantida em sincronia).
  return ["NVDA", "SMCI", "GOOGL", "ARM", "AVGO", "MRVL", "SKHY", "TSLA"];
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

export function runAgent(trigger: "manual" | "scheduled" | "premarket" | "portfolio" | "coal" | "ai" | "news" = "manual", maxTurns?: number): void {
  if (state.running) {
    logger.warn("Agent already running — skipping trigger");
    return;
  }

  const mode = trigger === "premarket" ? "premarket" : trigger === "portfolio" ? "portfolio" : trigger === "coal" ? "coal" : trigger === "ai" ? "ai" : trigger === "news" ? "news" : "daily";

  state.running = true;
  state.currentStep =
    trigger === "premarket" ? "Iniciando varredura pré-mercado..." :
    trigger === "portfolio" ? "Iniciando análise rápida da carteira..." :
    trigger === "coal" ? "Iniciando análise do setor de carvão..." :
    trigger === "ai" ? "Iniciando análise do setor de IA..." :
    trigger === "news" ? "Iniciando varredura de notícias..." :
    "Iniciando agente...";
  state.lastRunAt = new Date().toISOString();

  const startedAt = new Date();
  let runId: number | null = null;

  void (async () => {
  try {
  const tickers = trigger === "portfolio"
    ? await getPortfolioTickers()
    : trigger === "coal"
    ? COAL_TICKERS
    : trigger === "ai"
    ? AI_TICKERS
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
  const effectiveProvider = await getEffectiveAgentProvider();

  // Default subiu de 10 -> 18 -> 30 min. O gargalo já não é mais a lentidão
  // pontual do yfinance (10 -> 18min, resolvido por ferramentas mais rápidas
  // e cache) nem a execução em série das ferramentas de um turno (18min,
  // resolvido pela paralelização em agent.py) -- scans maiores (17+ ativos,
  // várias categorias de ferramenta) simplesmente precisam de mais turnos, e
  // cada turno tem um custo fixo de latência do próprio modelo (chamada à
  // API) que a paralelização de ferramentas não reduz. Visto em produção:
  // runs matando aos 18min consistentemente (18/07) mesmo já com o fix de
  // paralelização. Configurável via env var para dar folga sem precisar de
  // outro deploy.
  const TIMEOUT_MS = Number(process.env.AGENT_TIMEOUT_MS) > 0 ? Number(process.env.AGENT_TIMEOUT_MS) : 30 * 60 * 1000;
  // Folga reservada pro agente fechar sozinho com um relatório parcial (um
  // turno final sem ferramentas) antes do SIGTERM chegar -- sem isso, a run
  // simplesmente morre sem nunca imprimir REPORT:, e todo o progresso e
  // dinheiro já gasto nas chamadas parciais viram uma falha total registrada
  // sem relatório nenhum (ver agent.py::_agent_loop, deadline_ts).
  const SOFT_DEADLINE_BUFFER_MS = 120 * 1000;
  const softDeadlineMs = Date.now() + TIMEOUT_MS - SOFT_DEADLINE_BUFFER_MS;

  const py = spawn(getPythonBin(), ["-m", "agent.run_agent"], {
    cwd: agentDir,
    env: {
      ...process.env,
      INTERNAL_API_URL: apiUrl,
      PYTHONPATH: agentDir,
      AGENT_TICKERS: tickers.join(","),
      AGENT_PORTFOLIO_TICKERS: (trigger === "portfolio" || trigger === "coal" || trigger === "ai") ? tickers.join(",") : (process.env.AGENT_PORTFOLIO_TICKERS ?? ""),
      AGENT_MODE: mode,
      AGENT_SOFT_DEADLINE_MS: String(softDeadlineMs),
      ...(maxTurns !== undefined ? { AGENT_MAX_TURNS: String(maxTurns) } : {}),
      ...(effectiveProvider ? { AGENT_PROVIDER: effectiveProvider } : {}),
      OPERATOR_API_KEY: process.env.OPERATOR_API_KEY ?? "",
    },
  });

  const killTimer = setTimeout(() => {
    logger.warn(`Agent timeout (${Math.round(TIMEOUT_MS / 60000)} min) — killing process`);
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

    // Linha USAGE:{json} emitida pelo agente (antes de REPORT:, inclusive em falhas)
    // com tokens agregados e custo estimado da run.
    interface RunUsage {
      input_tokens?: number;
      output_tokens?: number;
      cache_read_tokens?: number;
      cache_write_tokens?: number;
      total_cost_usd?: number | null;
      providers?: Array<{ provider?: string; model?: string }>;
    }
    let usageFields: Partial<typeof agentRunsTable.$inferInsert> = {};
    const usageMatch = output.match(/^USAGE:(\{.*\})\s*$/m);
    if (usageMatch) {
      try {
        const u = JSON.parse(usageMatch[1]) as RunUsage;
        const providers = u.providers ?? [];
        usageFields = {
          inputTokens: u.input_tokens ?? null,
          outputTokens: u.output_tokens ?? null,
          cacheReadTokens: u.cache_read_tokens ?? null,
          cacheWriteTokens: u.cache_write_tokens ?? null,
          costUsd: u.total_cost_usd ?? null,
          llmProvider: providers.map((p) => p.provider).filter(Boolean).join(",") || null,
          llmModel: providers.map((p) => p.model).filter(Boolean).join(",") || null,
        };
        logger.info({ usage: u }, "Agent run usage");
      } catch (err) {
        logger.warn({ err }, "Failed to parse agent USAGE line");
      }
    }

    if (code !== 0) {
      logger.error({ code, errorOutput }, "Agent process exited with error");
      if (runId !== null) {
        await db
          .update(agentRunsTable)
          .set({ status: "failed", finishedAt, durationMs, errorMessage: errorOutput.slice(0, 2000), ...usageFields })
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

    const today = todayBRTDateString();

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
        .set({ status: "success", finishedAt, durationMs, ...usageFields })
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

