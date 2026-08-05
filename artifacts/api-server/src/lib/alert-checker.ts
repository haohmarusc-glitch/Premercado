/**
 * Background job that checks price alerts every 5 minutes.
 * For each enabled alert, fetches the current changePct/price (indicator
 * 'price') or RSI/MACD/SMA (demais indicadores) and fires an email if the
 * condition is met (with a 4-hour cooldown per alert).
 *
 * NOTE: o job em si roda sobre a tabela inteira (todos os usuários, uma
 * varredura só) -- mas cada e-mail vai pro notify_email salvo NO PRÓPRIO
 * alerta (definido na criação), não mais pra um endereço único compartilhado.
 */
import path from "path";
import { and, eq, gte } from "drizzle-orm";
import { db, alertsTable, alertFiringsTable, intradaySpikesTable, bounceAlertFiringsTable, squeezeAlertFiringsTable, type Alert } from "@workspace/db";
import { agentDir, getPythonBin, state as agentState } from "./runner";
import { sendAlertEmail, sendBounceAlertEmail, sendSqueezeAlertEmail } from "./mailer";
import { logger } from "./logger";
import { runExclusiveFresh, filaPendentes } from "./python-queue";
import { spawnPython } from "./python-spawn";
import { ordemRotacionada, deveDispararCiclo, TAREFAS_POR_CICLO } from "./ciclo-rotativo";
import { evalTechnical, type Technicals } from "./alert-technical-eval";
import { getOrCreateSettings } from "../routes/settings";
import { todayBRTDateString } from "./timezone";

const CHECK_INTERVAL_MS = 5 * 60_000; // 5 min
// Picos intraday são momentâneos (1 candle de 1min) mas a condição que os
// gerou (volume/preço elevado) costuma persistir por vários ciclos de 5min
// seguidos -- sem esse cooldown, o mesmo pico viraria uma nova linha no
// card "Alertas de Mercado" a cada poll enquanto durar.
const INTRADAY_SPIKE_COOLDOWN_MS = 15 * 60_000; // 15 min
const COOLDOWN_MS = 4 * 60 * 60_000; // 4 hours

// Timeouts dos subprocessos Python. Cada um é usado em DOIS lugares (o
// setTimeout que mata o processo e o deadline passado ao Python via env), por
// isso vira constante nomeada em vez de literal repetido.
// Spike, bounce e squeeze rodam num processo só (run_checkers.py). Antes eram
// três spawns de 60s + 60s + 120s = 240s de ocupação da fila no pior caso, cada
// um pagando do zero o import de pandas+numpy+yfinance -- e, pior, subindo
// quase juntos e disputando a CPU do container (medido: 8-46s só de startup,
// contra ~1s a quente). Batelados, o import é pago uma vez e o teto cai.
//
// O Python divide este tempo entre os checks (fatia por peso, sobra
// redistribuída) e trata SIGTERM entregando o parcial -- ver run_checkers.py.
const CHECKERS_TIMEOUT_MS = 180_000;

/**
 * Env do subprocesso Python com o deadline ABSOLUTO em que este lado desiste.
 *
 * O Python calcula o orçamento do bounded_parallel_map a partir do tempo que
 * REALMENTE resta (ver bounded_parallel.py::budget_from_deadline), em vez de
 * uma constante própria que precisava adivinhar quanto do tempo o startup ia
 * consumir. Import de pandas+numpy+yfinance custa ~8s numa máquina ociosa e
 * saía da mesma folga: em 02/08 spike e bounce estouraram os 60s com 1ms de
 * diferença, spawnados juntos, mesmo com budget interno de 45s.
 *
 * Passar o deadline em vez de repetir o número dos dois lados elimina a chance
 * de eles divergirem de novo.
 */
function pythonEnv(timeoutMs: number): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PYTHONPATH: agentDir,
    AGENT_DEADLINE_TS: String(Date.now() + timeoutMs),
  };
}


/**
 * Mensagem de timeout com o stderr que o processo alcançou emitir.
 *
 * Antes disto o caminho de timeout fazia `reject(new Error("timeout"))` e o
 * `err` acumulado era descartado -- ele só era usado quando o processo saía com
 * código != 0. O resultado: os diagnósticos que os próprios scripts imprimem
 * (as marcas do startup_probe, o aviso de orçamento do bounded_parallel) eram
 * invisíveis exatamente no caminho em que interessam, e todo timeout chegava ao
 * log como a mesma linha sem informação.
 */
function timeoutError(rotulo: string, stderr: string, timeoutMs: number): Error {
  const cauda = stderr.trim().slice(-2000);
  return new Error(
    cauda
      ? `${rotulo} (${timeoutMs}ms). stderr: ${cauda}`
      : `${rotulo} (${timeoutMs}ms). Nenhum stderr -- o processo não chegou a imprimir nada.`,
  );
}

interface Quote {
  symbol: string;
  changePct: number | null;
  price: number | null;
}

// 120s, não 60s: um timeout ABAIXO do tempo de partida medido não é proteção,
// é falha garantida -- e ainda gasta um lugar na fila para não entregar nada.
//
// Medido em produção em 04/08, no mesmo deployment, só nos imports do Python:
// numpy variando de 0.23s a 63.01s, pandas de 43s a 60s, yfinance de 17s a
// 45s; o total até os imports terminarem ficou entre 69.9s e 120.8s. Com o
// teto em 60s, os 8 estouros de get_quotes daquela janela aconteceram todos
// antes de o processo chegar a consultar cotação nenhuma -- os stderr mostram
// só "[probe] boot", nunca uma linha de dado.
//
// Isto NÃO conserta a causa (o container fica sem CPU; ver
// docs/deploy-fora-do-replit.md). Só para de descartar trabalho que ia dar
// certo. pythonEnv() deriva o AGENT_DEADLINE_TS deste valor, então o orçamento
// interno do bounded_parallel_map sobe junto e não fica adivinhando.
const QUOTES_TIMEOUT_MS = 120_000;

function fetchQuotes(tickers: string[]): Promise<Quote[] | null> {
  return runExclusiveFresh("get_quotes", () => new Promise((resolve, reject) => {
    const py = spawnPython(getPythonBin(), ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      env: pythonEnv(QUOTES_TIMEOUT_MS),
    });
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // Este era o ÚNICO spawn sem timeout: podia pendurar pra sempre, e agora
    // travaria a fila inteira junto (ver invariante em python-queue.ts).
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(timeoutError("get_quotes timeout", err, QUOTES_TIMEOUT_MS)); }, QUOTES_TIMEOUT_MS);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(`get_quotes: ${err}`)); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  }), CHECK_INTERVAL_MS);
}

function fetchTechnicals(tickers: string[]): Promise<Technicals[] | null> {
  return runExclusiveFresh("get_technicals", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_technicals.py");
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(timeoutError("timeout", err, 60_000)); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_technicals: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { items?: Technicals[] };
        resolve(parsed.items ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  }), CHECK_INTERVAL_MS);
}

async function fireAlert(
  alert: Alert,
  now: Date,
  opts: {
    currentPrice: number | null;
    currentChangePct: number | null;
    valueAtFiring: number | null;
  },
): Promise<void> {
  try {
    await sendAlertEmail({
      to: alert.notifyEmail,
      symbol: alert.symbol,
      indicator: alert.indicator,
      condition: alert.condition,
      thresholdPct: alert.thresholdPct,
      thresholdPrice: alert.thresholdPrice,
      thresholdValue: alert.thresholdValue,
      valueAtFiring: opts.valueAtFiring,
      currentChangePct: opts.currentChangePct,
      currentPrice: opts.currentPrice,
    });

    await db
      .update(alertsTable)
      .set({ lastTriggeredAt: now })
      .where(eq(alertsTable.id, alert.id));

    await db.insert(alertFiringsTable).values({
      alertId: alert.id,
      symbol: alert.symbol,
      indicator: alert.indicator,
      condition: alert.condition,
      thresholdPct: alert.thresholdPct,
      thresholdPrice: alert.thresholdPrice,
      thresholdValue: alert.thresholdValue,
      valueAtFiring: opts.valueAtFiring,
      changePctAtFiring: opts.currentChangePct,
      priceAtFiring: opts.currentPrice,
      firedAt: now,
    });

    logger.info(
      { symbol: alert.symbol, indicator: alert.indicator, condition: alert.condition },
      "Alert triggered",
    );
  } catch (err) {
    logger.error({ err, alertId: alert.id }, "Failed to send alert email");
  }
}

export async function checkAlerts(): Promise<void> {
  // O agente diário já satura CPU/rede com dezenas de chamadas Python em
  // paralelo (technicals, candle patterns, short interest, etc.) -- rodar o
  // checker de alertas (outro subprocesso Python) ao mesmo tempo faz os dois
  // competirem e estourar o timeout de fetchQuotes/fetchTechnicals (visto em
  // produção). Pula o ciclo inteiro; o próximo (5 min depois) roda sem a
  // agente por perto na maioria das vezes, já que a run dura poucos minutos.
  if (agentState.running) {
    logger.info("Alert checker: pulando ciclo -- agente diário em execução");
    return;
  }

  // Get all enabled alerts
  const alerts = await db
    .select()
    .from(alertsTable)
    .where(eq(alertsTable.enabled, true));

  if (!alerts.length) return;

  const priceAlerts = alerts.filter((a) => a.indicator === "price");
  const technicalAlerts = alerts.filter((a) => a.indicator !== "price");
  const now = new Date();

  const withinCooldown = (alert: Alert): boolean => {
    if (!alert.lastTriggeredAt) return false;
    return now.getTime() - new Date(alert.lastTriggeredAt).getTime() < COOLDOWN_MS;
  };

  // ── Alertas de preco/variacao (comportamento original) ──────────────────
  if (priceAlerts.length) {
    const symbols = [...new Set(priceAlerts.map((a) => a.symbol))];
    // null = a fila descartou este ciclo por obsolescência (já logado lá).
    // Mapa vazio => nenhum alerta de preço é avaliado agora, que é o certo:
    // avaliar com cotação velha dispara alerta sobre um mercado que já mudou.
    let quotes: Quote[] | null = [];
    try {
      quotes = await fetchQuotes(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch quotes");
    }
    const quoteMap = new Map((quotes ?? []).map((q) => [q.symbol, q]));

    for (const alert of priceAlerts) {
      const quote = quoteMap.get(alert.symbol);
      if (!quote) continue;

      // Alerta por preço só precisa do preço; por variação, só do changePct
      // (no pré-mercado o changePct costuma vir nulo — não pode barrar o de preço)
      let triggered = false;
      let valueAtFiring: number | null = null;
      if (alert.thresholdPrice != null) {
        if (quote.price == null) continue;
        triggered = alert.condition === "above"
          ? quote.price >= alert.thresholdPrice
          : quote.price <= alert.thresholdPrice;
        valueAtFiring = quote.price;
      } else if (alert.thresholdPct != null) {
        if (quote.changePct == null) continue;
        triggered = alert.condition === "above"
          ? quote.changePct >= alert.thresholdPct
          : quote.changePct <= alert.thresholdPct;
        valueAtFiring = quote.changePct;
      }

      if (!triggered || withinCooldown(alert)) continue;
      await fireAlert(alert, now, { currentPrice: quote.price, currentChangePct: quote.changePct, valueAtFiring });
    }
  }

  // ── Alertas por condicao tecnica (RSI/MACD/SMA) ──────────────────────────
  if (technicalAlerts.length) {
    const symbols = [...new Set(technicalAlerts.map((a) => a.symbol))];
    // null = ciclo descartado pela fila (ver fetchQuotes acima).
    let technicals: Technicals[] | null = [];
    try {
      technicals = await fetchTechnicals(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch technicals");
    }
    const techMap = new Map((technicals ?? []).map((t) => [t.ticker, t]));

    for (const alert of technicalAlerts) {
      const t = techMap.get(alert.symbol);
      if (!t || t.error) continue;

      const valueAtFiring = evalTechnical(alert, t);
      if (valueAtFiring == null || withinCooldown(alert)) continue;
      await fireAlert(alert, now, { currentPrice: t.price ?? null, currentChangePct: null, valueAtFiring });
    }
  }
}

interface IntradaySpikeAlert {
  ticker: string;
  category: string;
  severity: "info" | "atencao" | "critico";
  title: string;
  detail: string;
  value: number | null;
  timestamp: string;
}

interface LoteDeCheckers {
  resultados: {
    spike?: IntradaySpikeAlert[];
    bounce?: IntradaySpikeAlert[];
    squeeze?: SqueezeAlert[];
  };
  falhas: Record<string, string>;
}

// run_checkers.py precisa rodar via `-m agent.xxx` (import absoluto do pacote)
// -- market_alerts.py faz `from .cache import cached`, import relativo que só
// resolve nesse contexto (mesmo motivo/padrão de
// routes/analysis.ts::runMarketAlertsSnapshot).
//
// Um spawn no lugar de três. O SIGTERM do timeout não é mais perda total: o
// Python trata o sinal e entrega o que já calculou (ver run_checkers.py), então
// o `close` abaixo tenta parsear a saída mesmo quando o processo foi morto.
/** Linhas que o run_checkers imprime de propósito (probe e duração por check). */
const LINHA_DIAGNOSTICA = /^\[(probe|run_checkers)/;
/**
 * Assinatura do yfinance devolvendo resposta vazia. A mensagem fala em
 * deslistagem, mas aparece para NVDA, AVGO e MRVL -- ou seja, é bloqueio/limite
 * do Yahoo, não deslistagem. Contar em vez de repetir cada linha: o que importa
 * é quantos tickers vieram vazios, não o texto sete vezes.
 */
const SEM_DADO_YAHOO = /possibly delisted/i;

function registrarDiagnostico(stderr: string): void {
  const linhas = stderr.split("\n").map((l) => l.trim()).filter(Boolean);
  const diagnostico = linhas.filter((l) => LINHA_DIAGNOSTICA.test(l));
  const semDado = linhas.filter((l) => SEM_DADO_YAHOO.test(l)).length;
  if (!diagnostico.length && !semDado) return;
  logger.info(
    { diagnostico, tickersSemDadoNoYahoo: semDado },
    "run_checkers: ciclo concluído",
  );
}

function fetchCheckers(tickers: string[]): Promise<LoteDeCheckers | null> {
  return runExclusiveFresh("run_checkers", () => new Promise((resolve, reject) => {
    const py = spawnPython(getPythonBin(), ["-m", "agent.run_checkers"], {
      cwd: agentDir,
      env: pythonEnv(CHECKERS_TIMEOUT_MS),
    });
    py.stdin.write(JSON.stringify({ tickers, checks: ["spike", "bounce", "squeeze"] }));
    py.stdin.end();
    let out = "";
    let err = "";
    let matouPorTimeout = false;
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => {
      matouPorTimeout = true;
      py.kill("SIGTERM");
    }, CHECKERS_TIMEOUT_MS);
    py.on("close", (code) => {
      clearTimeout(t);
      // Ordem importa: tenta o parse ANTES de decidir que foi falha. Um
      // processo morto por SIGTERM sai com código != 0 mas pode ter escrito o
      // parcial -- descartar isso jogaria fora justamente o que o handler de
      // sinal existe pra salvar.
      try {
        const parsed = JSON.parse(out) as LoteDeCheckers;
        if (parsed?.resultados) {
          if (matouPorTimeout) {
            logger.warn({ err: err.trim().slice(-2000) },
              "run_checkers: timeout, seguindo com o resultado parcial");
          } else {
            // O stderr do ciclo BEM-SUCEDIDO também precisa aparecer.
            //
            // Antes ele só era logado nos caminhos de erro, e o efeito é
            // perverso: a medição de startup e a duração por check -- as duas
            // coisas que dizem se o ciclo está saudável -- só ficavam visíveis
            // quando o ciclo falhava. Passamos vários logs esperando as linhas
            // `[probe] import` aparecerem sem perceber que só um timeout as
            // revelaria.
            //
            // Diagnóstico não pode depender de fracasso pra existir.
            registrarDiagnostico(err);
          }
          resolve({ resultados: parsed.resultados, falhas: parsed.falhas ?? {} });
          return;
        }
      } catch {
        // Sem JSON utilizável -- cai nos erros abaixo, que dizem por quê.
      }
      if (matouPorTimeout) { reject(timeoutError("run_checkers timeout", err, CHECKERS_TIMEOUT_MS)); return; }
      if (code !== 0) { reject(new Error(err || "run_checkers: script failed")); return; }
      reject(new Error(`Bad JSON: ${out}`));
    });
  }), CHECK_INTERVAL_MS);
}

// Persiste os picos intraday detectados (candle de 1min) pra aparecerem no
// card "Alertas de Mercado" mesmo entre polls -- sem isso, um pico que
// aconteceu no minuto X só apareceria se o usuário estivesse com a página
// aberta bem naquele momento. Dedup por (ticker, title) dentro do cooldown
// evita repetir a mesma linha a cada 5min enquanto a condição persistir.
async function processarIntradaySpikes(spikes: IntradaySpikeAlert[]): Promise<void> {
  if (!spikes.length) return;

  const now = new Date();
  const cooldownSince = new Date(now.getTime() - INTRADAY_SPIKE_COOLDOWN_MS);

  for (const spike of spikes) {
    const recent = await db
      .select({ id: intradaySpikesTable.id })
      .from(intradaySpikesTable)
      .where(and(
        eq(intradaySpikesTable.ticker, spike.ticker),
        eq(intradaySpikesTable.title, spike.title),
        gte(intradaySpikesTable.firedAt, cooldownSince),
      ))
      .limit(1);
    if (recent.length) continue;

    await db.insert(intradaySpikesTable).values({
      ticker: spike.ticker,
      kind: spike.title === "Pico de volume intraday" ? "volume" : "price",
      severity: spike.severity,
      title: spike.title,
      detail: spike.detail,
      value: spike.value ?? null,
      firedAt: now,
    });
    logger.info({ ticker: spike.ticker, title: spike.title }, "Intraday spike recorded");
  }
}

// Notifica por e-mail quando market_alerts.py::check_dead_cat_bounce detecta
// um "repique" (recuperação técnica dentro de queda maior, ou o espelho --
// realização de lucro dentro de alta maior). Diferente de processarIntradaySpikes
// (persiste pro card, sem e-mail): esse sinal é baseado em fechamento diário
// (hoje vs. mesmo dia da semana passada), então dedup por dia BRT em vez de um
// cooldown corrido -- evita reenviar e-mail a cada poll de 5min enquanto o
// preço intradiário oscila em torno do limiar dentro do mesmo pregão, mas
// permite um novo e-mail se a direção do sinal virar no mesmo dia.
async function processarBounceAlerts(
  alerts: IntradaySpikeAlert[],
  settings: { notifyEmail: string },
): Promise<void> {
  if (!alerts.length) return;

  const today = todayBRTDateString();

  for (const a of alerts) {
    const direction: "up" | "down" = (a.value ?? 0) >= 0 ? "up" : "down";
    const key = `bounce:${a.ticker}:${direction}:${today}`;

    const already = await db
      .select({ id: bounceAlertFiringsTable.id })
      .from(bounceAlertFiringsTable)
      .where(eq(bounceAlertFiringsTable.alertKey, key))
      .limit(1);
    if (already.length) continue;

    try {
      await sendBounceAlertEmail({
        to: settings.notifyEmail,
        ticker: a.ticker,
        direction,
        changeTodayPct: a.value ?? 0,
        title: a.title,
        detail: a.detail,
      });
      await db.insert(bounceAlertFiringsTable).values({ alertKey: key });
      logger.info({ ticker: a.ticker, direction }, "Bounce alert fired");
    } catch (err) {
      logger.error({ err, ticker: a.ticker }, "Failed to send bounce alert email");
    }
  }
}

interface SqueezeAlert {
  ticker: string;
  price: number;
  tier: "near" | "confirmed";
  riskLevel: string;
  nDangerous: number;
  riskMissing: number;
  presentRiskSignals: string[];
  missingRiskSignals: string[];
  confirmCount: number;
  confirmMissing: number;
  presentConfirmSignals: string[];
  missingConfirmSignals: string[];
  excludedEarningsReactionSignals: string[];
  earningsImminent: boolean;
  missingEventSignals: string[];
  totalMissing: number;
}

// Notifica por e-mail o progresso de um setup de squeeze (tools.py::
// check_squeeze_setup) em dois níveis: "near" (falta só 1-2 dos 4
// requisitos -- risco de squeeze alto exige 2+ sinais perigosos, reversão
// técnica exige 2+ confirmações -- lista o que ainda falta) e "confirmed"
// (os 4 batidos). Dedup por (ticker, tier, dia BRT): evita reenviar e-mail
// a cada poll de 5min enquanto o nível não muda, mas dispara de novo assim
// que "near" vira "confirmed" (chave diferente) mesmo no mesmo dia.
async function processarSqueezeAlerts(
  alerts: SqueezeAlert[],
  settings: { notifyEmail: string },
): Promise<void> {
  if (!alerts.length) return;

  const today = todayBRTDateString();

  for (const a of alerts) {
    const key = `squeeze:${a.ticker}:${a.tier}:${today}`;

    const already = await db
      .select({ id: squeezeAlertFiringsTable.id })
      .from(squeezeAlertFiringsTable)
      .where(eq(squeezeAlertFiringsTable.alertKey, key))
      .limit(1);
    if (already.length) continue;

    try {
      await sendSqueezeAlertEmail({
        to: settings.notifyEmail,
        ticker: a.ticker,
        tier: a.tier,
        price: a.price,
        totalMissing: a.totalMissing,
        nDangerous: a.nDangerous,
        presentRiskSignals: a.presentRiskSignals,
        missingRiskSignals: a.missingRiskSignals,
        confirmCount: a.confirmCount,
        presentConfirmSignals: a.presentConfirmSignals,
        missingConfirmSignals: a.missingConfirmSignals,
        excludedEarningsReactionSignals: a.excludedEarningsReactionSignals,
        earningsImminent: a.earningsImminent,
        missingEventSignals: a.missingEventSignals,
      });
      await db.insert(squeezeAlertFiringsTable).values({ alertKey: key });
      logger.info({ ticker: a.ticker, tier: a.tier }, "Squeeze alert fired");
    } catch (err) {
      logger.error({ err, ticker: a.ticker }, "Failed to send squeeze alert email");
    }
  }
}

/**
 * Um spawn de Python pros três checkers de mercado, e o pós-processamento de
 * cada um em cima do lote.
 *
 * Os três liam a MESMA lista (settings.tickers) e cada um pagava o próprio
 * import + as próprias chamadas de rede. Agora é uma consulta de settings, um
 * processo, e o cache de histórico do Python é compartilhado entre eles.
 *
 * Uma falha isolada não derruba as outras: o Python devolve `falhas` por check
 * e os resultados de quem terminou continuam valendo.
 */
export async function rodarCheckersDeMercado(): Promise<void> {
  // Mesmo motivo do checkAlerts -- evita competir por CPU/rede com o agente
  // diário, que sozinho já satura o container.
  if (agentState.running) {
    logger.info("Checkers de mercado: pulando ciclo -- agente diário em execução");
    return;
  }

  const settings = await getOrCreateSettings();
  if (!settings.tickers.length) return;

  let lote: LoteDeCheckers | null = null;
  try {
    lote = await fetchCheckers(settings.tickers);
  } catch (err) {
    logger.warn({ err }, "Checkers de mercado: falha ao rodar o lote");
    return;
  }
  // null = ciclo descartado pela fila por obsolescência (já logado lá).
  if (!lote) return;

  for (const [check, motivo] of Object.entries(lote.falhas)) {
    logger.warn({ check, motivo }, "Checkers de mercado: um check falhou, os demais seguem");
  }

  // Em série: são gravações no Postgres e envios de e-mail, não trabalho de
  // CPU -- paralelizar aqui só reintroduziria a contenção que o lote resolveu.
  // Cada um no seu try pelo mesmo motivo do `falhas` acima: uma falha de e-mail
  // do bounce não pode impedir o squeeze de ser processado.
  const etapas: [string, () => Promise<void>][] = [
    ["spike", () => processarIntradaySpikes(lote.resultados.spike ?? [])],
    ["bounce", () => processarBounceAlerts(lote.resultados.bounce ?? [], settings)],
    ["squeeze", () => processarSqueezeAlerts(lote.resultados.squeeze ?? [], settings)],
  ];
  for (const [nome, etapa] of etapas) {
    try {
      await etapa();
    } catch (err) {
      logger.error({ err, check: nome }, "Checkers de mercado: falha ao processar o resultado");
    }
  }
}

let intervalHandle: ReturnType<typeof setInterval> | null = null;

const CICLO: { nome: string; run: () => Promise<void> }[] = [
  { nome: "Alert check", run: checkAlerts },
  { nome: "Market checkers", run: rodarCheckersDeMercado },
];

let inicioDoCiclo = 0;

/**
 * Dispara o lote girando quem entra primeiro na fila a cada ciclo.
 *
 * A fila é FIFO com descarte por idade (python-queue.ts). Sob pressão, quem
 * entra por último é sempre quem espera mais e, portanto, sempre o primeiro a
 * ser descartado -- numa ordem fixa isso significaria o squeeze (o último, e o
 * mais lento) nunca mais rodar durante uma manhã movimentada, silenciosamente.
 * Ver ciclo-rotativo.ts.
 */
function dispararCiclo(): void {
  const pendentes = filaPendentes();
  if (!deveDispararCiclo(pendentes)) {
    // Não avança inicioDoCiclo: a rotação existe pra distribuir o descarte
    // entre os checkers, e num ciclo que não rodou não houve o que distribuir.
    logger.warn(
      { pendentes, limite: TAREFAS_POR_CICLO },
      "Ciclo de checkers pulado -- a fila ainda não drenou a volta anterior",
    );
    return;
  }
  for (const { nome, run } of ordemRotacionada(CICLO, inicioDoCiclo)) {
    // Chave `err`, NUNCA `e`: o serializador de erro do pino só é aplicado à
    // chave "err". Sob "e" o Error vira JSON.stringify comum, e message e
    // stack são propriedades NÃO enumeráveis -- somem. Foi assim que os
    // "Alert check error" / "Market checkers error" de 04/08 chegaram ao log
    // como {"query":...,"params":[true],"cause":{}}: a causa raiz existia, só
    // não era impressa. Cinco ocorrências ficaram sem diagnóstico por isso.
    run().catch((err) => logger.error({ err }, `${nome} error`));
  }
  inicioDoCiclo += 1;
}

export function startAlertChecker(): void {
  if (intervalHandle) return;
  // First check after 30s startup grace period
  setTimeout(dispararCiclo, 30_000);
  // Then every 5 min
  intervalHandle = setInterval(dispararCiclo, CHECK_INTERVAL_MS);
  logger.info("Price alert checker started (interval: 5 min)");
}
