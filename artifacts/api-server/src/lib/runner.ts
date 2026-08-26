/**
 * Shared agent runner — used by both the HTTP route and the scheduler.
 * Spawns the Python subprocess, records the run in DB, saves the report, and sends e-mail.
 */
import path from "path";
import { existsSync } from "fs";
import { asc, eq, inArray, gte, sql } from "drizzle-orm";
import { db, reportsTable, agentRunsTable, settingsTable, portfolioPositionsTable, portfolioPurchasesTable } from "@workspace/db";
import { logger } from "./logger";
import { spawnPython } from "./python-spawn";
import { sendReportEmail } from "./mailer";
import { bannerDeAvisos, bannerProvedoresCaidos, preflightRelatorio } from "./report-preflight";
import { startOfTodayBRT, todayBRTDateString } from "./timezone";
import { decideProvider } from "./agent-budget";
import { isPositionActiveFromLots, carteiraParaOAgente } from "./portfolio-math";

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
//
// Devolve TAMBÉM a ordem de fallback: rebaixar só o primeiro da fila não
// segurava gasto nenhum, porque o provedor estourado continuava logo atrás na
// cadeia e recebia a chamada assim que o barato falhasse (ver agent-budget.ts).
async function getEffectiveAgentProvider(): Promise<{ provider?: string; order?: string }> {
  try {
    const [settings] = await db.select().from(settingsTable).limit(1);
    if (!settings) return {};

    const runsToday = await db
      .select({ costUsd: agentRunsTable.costUsd, llmProvider: agentRunsTable.llmProvider })
      .from(agentRunsTable)
      .where(gte(agentRunsTable.startedAt, startOfTodayBRT()));

    const decisao = decideProvider({
      agentProvider: settings.agentProvider,
      dailyBudgetUsd: settings.dailyBudgetUsd,
      cheapProvider: settings.cheapProvider,
      runsToday,
    });

    if (decisao.unpricedRuns > 0) {
      // Run com modelo fora do MODEL_PRICING reporta custo null e soma ZERO no
      // teto -- furo real, e silencioso se não avisar aqui.
      logger.warn(
        { unpricedRuns: decisao.unpricedRuns, spentToday: decisao.spentToday },
        "Runs de hoje com custo desconhecido não somam no teto diário — adicionar o modelo em MODEL_PRICING (provider.py)",
      );
    }
    if (decisao.exceeded) {
      logger.warn(
        {
          spentToday: decisao.spentToday,
          budget: decisao.budget,
          provider: decisao.provider,
          order: decisao.order,
        },
        "Teto diário estourado — rebaixando para o provedor barato pelo resto do dia",
      );
    }
    if (decisao.downgradeIneffective) {
      logger.error(
        { provider: decisao.provider },
        "Teto diário estourado mas cheapProvider == provedor primário: o teto não economiza nada. Configure um provedor barato diferente.",
      );
    }
    return { provider: decisao.provider, order: decisao.order };
  } catch (err) {
    logger.error({ err }, "Failed to compute effective agent provider; using default order");
    return {};
  }
}

// Escopado por usuário -- ANTES buscava portfolio_positions sem filtro
// nenhum, ou seja, misturava as posições de TODOS os usuários numa run só
// (e o relatório resultante, salvo numa tabela global, aparecia igual pra
// todo mundo). Ver reportsTable.userId e routes/reports.ts.
/**
 * Tickers da carteira REAL, do banco.
 *
 * `userId` opcional: sem ele, cobre as posições de todos os usuários. É o que
 * os fluxos disparados por agendamento precisam -- não existe "usuário da
 * requisição" num cron -- e segue o mesmo modelo dos demais jobs de fundo, que
 * já rodam sobre a tabela inteira (ver o NOTE no topo de alert-checker.ts).
 *
 * Uma função só, com escopo opcional, em vez de duas: a regra de "posição
 * ativa" é decidida pelos LOTES, não pelo campo `quantity` armazenado, e cada
 * cópia dessa regra é uma chance de ela divergir (aconteceu com MU, que ficou
 * aparecendo como ativa em quatro lugares depois de totalmente vendida).
 */
export async function getPortfolioTickers(userId?: number): Promise<string[]> {
  try {
    const rows = await db
      .select({ id: portfolioPositionsTable.id, ticker: portfolioPositionsTable.ticker, isEtf: portfolioPositionsTable.isEtf, quantity: portfolioPositionsTable.quantity })
      .from(portfolioPositionsTable)
      .where(userId != null ? eq(portfolioPositionsTable.userId, userId) : undefined)
      .orderBy(asc(portfolioPositionsTable.createdAt));

    const nonEtf = rows.filter((r) => !r.isEtf);
    if (!nonEtf.length) return [];

    // Ativo/vendido é decidido pelos lotes reais (portfolio_purchases), não
    // pelo campo `quantity` armazenado -- ver isPositionActiveFromLots.
    // Sem isso, uma posição com todos os lotes vendidos mas `quantity`
    // desatualizado (PUT /portfolio/:id edita esse campo direto) entrava na
    // análise de carteira do agente pra sempre (visto em produção com MU).
    const lots = await db
      .select({ positionId: portfolioPurchasesTable.positionId, saleDate: portfolioPurchasesTable.saleDate, salePrice: portfolioPurchasesTable.salePrice })
      .from(portfolioPurchasesTable)
      .where(inArray(portfolioPurchasesTable.positionId, nonEtf.map((r) => r.id)));
    const lotsByPosition = new Map<number, typeof lots>();
    for (const lot of lots) {
      const list = lotsByPosition.get(lot.positionId) ?? [];
      list.push(lot);
      lotsByPosition.set(lot.positionId, list);
    }

    const stocks = nonEtf.filter((r) => isPositionActiveFromLots(r.quantity, lotsByPosition.get(r.id) ?? []));
    // Set: sem userId, dois usuários com a mesma posição repetiriam o ticker.
    return [...new Set(stocks.map((r) => r.ticker))];
  } catch (err) {
    logger.error({ err, userId }, "Failed to read portfolio tickers for user");
    // Vazio, NUNCA um fallback fixo -- um fallback compartilhado aqui
    // devolveria a carteira de outra pessoa pra quem não tem posições ou deu
    // erro na query, o mesmo tipo de vazamento que este código existe pra
    // evitar.
    return [];
  }
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

// Quantos passos recentes manter no histórico exibido no painel de status --
// alto o bastante pra cobrir uma run inteira (tipicamente <30 passos), baixo
// o bastante pra não inflar o payload de /agent/status (polado a cada 30s).
const STEP_LOG_MAX = 60;

export interface AgentState {
  running: boolean;
  lastRunAt: string | null;
  currentStep: string | null;
  stepLog: string[];
  nextRunAt: string | null;
  scheduleEnabled: boolean;
}

export const state: AgentState = {
  running: false,
  lastRunAt: null,
  currentStep: null,
  stepLog: [],
  nextRunAt: null,
  scheduleEnabled: true,
};

// userId: obrigatório na prática pros modos "portfolio"/"veredito" (rodam em
// cima da carteira de QUEM disparou a run, ver getPortfolioTickers acima) --
// vem de req.userId na rota HTTP (routes/agent.ts). Pros demais modos
// (compartilhados por todo o app) é ignorado.
export function runAgent(trigger: "manual" | "scheduled" | "premarket" | "portfolio" | "coal" | "ai" | "news" | "exit_plan" | "alerts" | "veredito" | "consensus" = "manual", maxTurns?: number, userId?: number): void {
  if (state.running) {
    logger.warn("Agent already running — skipping trigger");
    return;
  }

  const mode = trigger === "premarket" ? "premarket" : trigger === "portfolio" ? "portfolio" : trigger === "coal" ? "coal" : trigger === "ai" ? "ai" : trigger === "news" ? "news" : trigger === "exit_plan" ? "exit_plan" : trigger === "alerts" ? "alerts" : trigger === "veredito" ? "veredito" : trigger === "consensus" ? "consensus" : "daily";

  state.running = true;
  state.currentStep =
    trigger === "premarket" ? "Iniciando varredura pré-mercado..." :
    trigger === "portfolio" ? "Iniciando análise rápida da carteira..." :
    trigger === "coal" ? "Iniciando análise do setor de carvão..." :
    trigger === "ai" ? "Iniciando análise do setor de IA..." :
    trigger === "news" ? "Iniciando varredura de notícias..." :
    trigger === "exit_plan" ? "Reavaliando plano de saída..." :
    trigger === "alerts" ? "Iniciando gestão de alertas..." :
    trigger === "veredito" ? "Gerando veredito do dia..." :
    trigger === "consensus" ? "Iniciando relatório de consenso (3 provedores)..." :
    "Iniciando agente...";
  state.stepLog = [state.currentStep];
  state.lastRunAt = new Date().toISOString();

  const startedAt = new Date();
  let runId: number | null = null;

  void (async () => {
  try {
  // Modos que rodam sobre a carteira de QUEM disparou. Com `userId` presente,
  // a carteira vazia desse usuário é RESPOSTA -- ver o vazamento descrito em
  // carteiraParaOAgente e a guarda logo abaixo.
  const escopadaAUmUsuario =
    (trigger === "portfolio" || trigger === "veredito" || trigger === "consensus")
    && userId != null;

  const tickers = trigger === "portfolio" || trigger === "veredito" || trigger === "consensus"
    // Sem userId (run agendada) a carteira agora vem global em vez de vazia --
    // antes, um veredito agendado caía na lista fixa do config.py.
    ? await getPortfolioTickers(userId)
    : trigger === "coal"
    ? COAL_TICKERS
    : trigger === "ai"
    ? AI_TICKERS
    : await getMonitoredTickers();

  // A carteira que o modo diário EXIGE observação de sai do banco, igual à
  // cobertura sai de Settings. Antes ela vinha de AGENT_PORTFOLIO_TICKERS e,
  // sem essa env var, o Python caía numa lista fixa no código -- que continuava
  // exigindo observação de GOOGL e TSLA depois de eles saírem da carteira, e de
  // qualquer posição nova nunca ser exigida. Duas listas para a mesma pergunta,
  // e a que mandava não era a que o usuário edita.
  const carteira = trigger === "portfolio" || trigger === "coal" || trigger === "ai" || trigger === "veredito" || trigger === "consensus"
    ? tickers
    : await getPortfolioTickers(userId);

  // Carteira própria vazia: RECUSA, não analisa a de outra pessoa.
  //
  // Sem isto, a conta sem posições que clicava "Gerar veredito com IA"
  // recebia um texto inteiro sobre a carteira do operador -- com o plano de
  // saída dele, os cenários dele e os valores em reais dele -- enquanto os
  // painéis estruturados da mesma tela diziam "Sem posições na carteira".
  //
  // Recusar é mais útil que gerar um veredito sobre nada: o texto vazio seria
  // publicado como análise e custaria uma chamada de LLM para dizer que não
  // há o que analisar.
  if (escopadaAUmUsuario && carteira.length === 0) {
    logger.warn({ trigger, userId }, "Run sobre carteira própria vazia -- recusada");
    state.running = false;
    state.currentStep = "Sua carteira está vazia -- cadastre posições antes de gerar.";
    state.stepLog = [state.currentStep];
    return;
  }

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
  const { provider: effectiveProvider, order: providerOrder } = await getEffectiveAgentProvider();

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
  // Visto em produção (27/07, run das 08:30): a run falhou aos 30m2s -- ou
  // seja, mesmo a chamada de resgate estourou os 2min de folga (API lenta
  // pra gerar o relatório final, ou o turno em andamento no momento do
  // deadline já tinha consumido parte da folga antes do aviso disparar).
  // Dobrado pra 4min pra dar mais margem real pra essa última chamada
  // completar. Configurável via env var, mesmo padrão do TIMEOUT_MS acima.
  const SOFT_DEADLINE_BUFFER_MS = Number(process.env.AGENT_SOFT_DEADLINE_BUFFER_MS) > 0
    ? Number(process.env.AGENT_SOFT_DEADLINE_BUFFER_MS)
    : 240 * 1000;
  const softDeadlineMs = Date.now() + TIMEOUT_MS - SOFT_DEADLINE_BUFFER_MS;

  const py = spawnPython(getPythonBin(), ["-m", "agent.run_agent"], {
    cwd: agentDir,
    env: {
      ...process.env,
      INTERNAL_API_URL: apiUrl,
      PYTHONPATH: agentDir,
      AGENT_TICKERS: tickers.join(","),
      // `escopadaAUmUsuario`: sem isto, a conta SEM posições que dispara um
      // veredito recebia a carteira do operador (AGENT_PORTFOLIO_TICKERS).
      // Ver a nota de vazamento em carteiraParaOAgente.
      AGENT_PORTFOLIO_TICKERS: carteiraParaOAgente(
        carteira, process.env.AGENT_PORTFOLIO_TICKERS, escopadaAUmUsuario),
      AGENT_MODE: mode,
      AGENT_SOFT_DEADLINE_MS: String(softDeadlineMs),
      ...(maxTurns !== undefined ? { AGENT_MAX_TURNS: String(maxTurns) } : {}),
      ...(effectiveProvider ? { AGENT_PROVIDER: effectiveProvider } : {}),
      // Só vem preenchido no rebaixamento por orçamento, e aí serve pra TIRAR
      // o provedor estourado da cadeia -- sem isso o fallback devolvia a run
      // pra ele e o teto virava decoração (ver agent-budget.ts).
      ...(providerOrder ? { AGENT_PROVIDER_ORDER: providerOrder } : {}),
      OPERATOR_API_KEY: process.env.OPERATOR_API_KEY ?? "",
    },
  });

  const killTimer = setTimeout(() => {
    logger.warn(`Agent timeout (${Math.round(TIMEOUT_MS / 60000)} min) — killing process`);
    py.kill("SIGTERM");
    state.currentStep = "Tempo limite atingido — encerrando...";
    state.stepLog.push(state.currentStep);
  }, TIMEOUT_MS);

  let output = "";
  let errorOutput = "";

  py.stdout.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    logger.info({ line }, "Agent stdout");
    if (line.startsWith("STEP:")) {
      state.currentStep = line.replace("STEP:", "").trim();
      state.stepLog.push(state.currentStep);
      if (state.stepLog.length > STEP_LOG_MAX) {
        state.stepLog = state.stepLog.slice(-STEP_LOG_MAX);
      }
    }
    // PROVIDER_DOWN:{json} -- provedor de LLM condenado na run (conta sem
    // crédito, modelo indisponível). Vai pro stepLog na hora pra aparecer ao
    // vivo na tela de Runs; o aviso no e-mail é montado no close, a partir
    // do output completo.
    if (line.includes("PROVIDER_DOWN:")) {
      const m = line.match(/PROVIDER_DOWN:(\{.*\})/);
      if (m) {
        try {
          const info = JSON.parse(m[1]) as { provider?: string; motivo?: string };
          const aviso = `⚠️ Provedor ${info.provider ?? "?"} fora desta run: ${info.motivo ?? "motivo desconhecido"}`;
          state.stepLog.push(aviso);
          logger.error({ provider: info.provider, motivo: info.motivo }, "Provedor de LLM condenado na run (sem crédito/indisponível)");
        } catch { /* linha malformada não pode derrubar o stream */ }
      }
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

    // Save report to DB -- userId só pros modos derivados da carteira de
    // quem disparou a run (ver comentário em reportsTable.userId); os demais
    // ficam null, mantendo o comportamento de sempre (relatório compartilhado).
    let reportIdAtual: number | undefined;
    try {
      const [linha] = await db.insert(reportsTable).values({
        date: today,
        content,
        tickers,
        mode,
        userId: (trigger === "portfolio" || trigger === "veredito") ? userId ?? null : null,
      }).returning({ id: reportsTable.id });
      reportIdAtual = linha?.id;
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

    // Série de IV: grava o que a run já coletou de graça (get_options_data).
    // Sem isto nunca haverá IV Rank -- o yfinance só devolve a cadeia ao vivo,
    // então não existe histórico pra consultar nem como preencher depois.
    // ON CONFLICT DO UPDATE porque mais de uma run no mesmo dia acontece (em
    // 31/07 saíram três) e a última é a mais recente, não uma duplicata.
    const ivMatch = output.match(/^IVDATA:(\{.*\})\s*$/m);
    if (ivMatch) {
      try {
        const iv = JSON.parse(ivMatch[1]) as Record<string, { atm_iv_pct: number; atr_pct: number | null }>;
        const linhas = Object.entries(iv);
        let gravadas = 0;
        for (const [ticker, v] of linhas) {
          // try POR LINHA: a tabela tem CHECK de sanidade na IV, então um
          // ticker com número absurdo é rejeitado pelo banco -- e num try
          // único isso abortaria o laço e descartaria os tickers seguintes,
          // que estavam bons. Perder um é o custo; perder o resto junto não.
          try {
            await db.execute(sql`
              INSERT INTO iv_history (ticker, date, atm_iv_pct, atr_pct)
              VALUES (${ticker}, ${today}, ${v.atm_iv_pct}, ${v.atr_pct})
              ON CONFLICT (ticker, date) DO UPDATE
                SET atm_iv_pct = EXCLUDED.atm_iv_pct,
                    atr_pct = EXCLUDED.atr_pct,
                    recorded_at = now()
            `);
            gravadas += 1;
          } catch (err) {
            logger.warn({ err, ticker, iv: v.atm_iv_pct }, "IV recusada na gravação da série");
          }
        }
        if (gravadas) logger.info({ n: gravadas, de: linhas.length }, "Série de IV registrada");
      } catch (err) {
        // Falha aqui não pode derrubar o relatório -- é dado acessório.
        logger.warn({ err }, "Falha ao registrar série de IV");
      }
    }

    // Checklist antes do envio: última porta antes do e-mail chegar ao
    // usuário. Só relatório vazio e envio duplicado bloqueiam; o resto vira
    // aviso no topo do e-mail (ver report-preflight.ts).
    let corpoEmail = content;
    try {
      const pre = await preflightRelatorio({ content, date: today, mode, tickers, reportIdAtual });
      if (pre.achados.length) {
        logger.warn(
          { mode, achados: pre.achados, bloqueado: pre.bloqueado },
          "Preflight do relatório encontrou problemas",
        );
      }
      if (pre.bloqueado) {
        logger.error(
          { mode, achados: pre.achados.filter((a) => a.severity === "BLOCK") },
          "E-mail NÃO enviado — preflight bloqueou",
        );
        return;
      }
      corpoEmail = bannerDeAvisos(pre.achados) + content;
    } catch (err) {
      // Preflight com defeito não pode impedir o relatório de sair.
      logger.error({ err }, "Preflight falhou — enviando e-mail sem verificação");
    }

    // Provedores condenados na run (PROVIDER_DOWN:{json} no stdout, ver
    // provider.py::_condenar) -- conta sem crédito/modelo indisponível.
    // Montado FORA do try do preflight: um preflight quebrado não pode
    // engolir o aviso de que um provedor de IA está fora. Vai no topo do
    // e-mail porque é onde o dono da conta realmente olha todo dia.
    corpoEmail = bannerProvedoresCaidos(output) + corpoEmail;

    // Send e-mail notification
    await sendReportEmail(corpoEmail, today, tickers, mode);
  });
  } catch (err) {
    logger.error({ err }, "Unexpected error while running agent");
    state.running = false;
    state.currentStep = null;
  }
  })();
}

