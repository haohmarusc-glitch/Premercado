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
import { spawn } from "child_process";
import path from "path";
import { and, eq, gte } from "drizzle-orm";
import { db, alertsTable, alertFiringsTable, intradaySpikesTable, bounceAlertFiringsTable, squeezeAlertFiringsTable, type Alert } from "@workspace/db";
import { agentDir, getPythonBin, state as agentState } from "./runner";
import { sendAlertEmail, sendBounceAlertEmail, sendSqueezeAlertEmail } from "./mailer";
import { logger } from "./logger";
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

interface Quote {
  symbol: string;
  changePct: number | null;
  price: number | null;
}

function fetchQuotes(tickers: string[]): Promise<Quote[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_quotes", ...tickers], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    py.on("close", (code) => {
      if (code !== 0) { reject(new Error(`get_quotes: ${err}`)); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

function fetchTechnicals(tickers: string[]): Promise<Technicals[]> {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "get_technicals.py");
    const py = spawn(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_technicals: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { items?: Technicals[] };
        resolve(parsed.items ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
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

async function checkAlerts(): Promise<void> {
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
    let quotes: Quote[] = [];
    try {
      quotes = await fetchQuotes(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch quotes");
    }
    const quoteMap = new Map(quotes.map((q) => [q.symbol, q]));

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
    let technicals: Technicals[] = [];
    try {
      technicals = await fetchTechnicals(symbols);
    } catch (err) {
      logger.warn({ err }, "Alert checker: failed to fetch technicals");
    }
    const techMap = new Map(technicals.map((t) => [t.ticker, t]));

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

// get_intraday_spikes.py precisa rodar via `-m agent.xxx` (import absoluto
// do pacote) -- market_alerts.py faz `from .cache import cached`, import
// relativo que só resolve nesse contexto (mesmo motivo/padrão de
// routes/analysis.ts::runMarketAlertsSnapshot).
function fetchIntradaySpikes(tickers: string[]): Promise<IntradaySpikeAlert[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_intraday_spikes"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_intraday_spikes: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { alerts?: IntradaySpikeAlert[] };
        resolve(parsed.alerts ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

// Persiste os picos intraday detectados (candle de 1min) pra aparecerem no
// card "Alertas de Mercado" mesmo entre polls -- sem isso, um pico que
// aconteceu no minuto X só apareceria se o usuário estivesse com a página
// aberta bem naquele momento. Dedup por (ticker, title) dentro do cooldown
// evita repetir a mesma linha a cada 5min enquanto a condição persistir.
async function checkIntradaySpikes(): Promise<void> {
  // Mesmo motivo do checkAlerts acima -- evita competir por CPU/rede com o
  // agente diário e estourar o timeout do subprocesso get_intraday_spikes.
  if (agentState.running) {
    logger.info("Intraday spike checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const settings = await getOrCreateSettings();
  if (!settings.tickers.length) return;

  let spikes: IntradaySpikeAlert[] = [];
  try {
    spikes = await fetchIntradaySpikes(settings.tickers);
  } catch (err) {
    logger.warn({ err }, "Intraday spike checker: failed to fetch spikes");
    return;
  }
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

// get_bounce_alerts.py precisa rodar via `-m agent.xxx` (import absoluto do
// pacote) -- mesmo motivo de fetchIntradaySpikes acima.
function fetchBounceAlerts(tickers: string[]): Promise<IntradaySpikeAlert[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_bounce_alerts"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 60_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_bounce_alerts: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { alerts?: IntradaySpikeAlert[] };
        resolve(parsed.alerts ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

// Notifica por e-mail quando market_alerts.py::check_dead_cat_bounce detecta
// um "repique" (recuperação técnica dentro de queda maior, ou o espelho --
// realização de lucro dentro de alta maior). Diferente de checkIntradaySpikes
// (persiste pro card, sem e-mail): esse sinal é baseado em fechamento diário
// (hoje vs. mesmo dia da semana passada), então dedup por dia BRT em vez de um
// cooldown corrido -- evita reenviar e-mail a cada poll de 5min enquanto o
// preço intradiário oscila em torno do limiar dentro do mesmo pregão, mas
// permite um novo e-mail se a direção do sinal virar no mesmo dia.
async function checkBounceAlerts(): Promise<void> {
  if (agentState.running) {
    logger.info("Bounce alert checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const settings = await getOrCreateSettings();
  if (!settings.tickers.length) return;

  let alerts: IntradaySpikeAlert[] = [];
  try {
    alerts = await fetchBounceAlerts(settings.tickers);
  } catch (err) {
    logger.warn({ err }, "Bounce alert checker: failed to fetch bounce alerts");
    return;
  }
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

// get_squeeze_alerts.py precisa rodar via `-m agent.xxx` (import absoluto do
// pacote) -- mesmo motivo de fetchIntradaySpikes/fetchBounceAlerts acima.
function fetchSqueezeAlerts(tickers: string[]): Promise<SqueezeAlert[]> {
  return new Promise((resolve, reject) => {
    const py = spawn(getPythonBin(), ["-m", "agent.get_squeeze_alerts"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify({ tickers }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // check_squeeze_setup faz várias chamadas de rede por ticker (yfinance,
    // iBorrowDesk, FINRA, Unusual Whales opcional) -- mesmo cacheado por
    // 30min, o primeiro poll depois de um restart pode ser lento com a
    // watchlist inteira. Timeout mais generoso que os demais checkers.
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 120_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "get_squeeze_alerts: script failed")); return; }
      try {
        const parsed = JSON.parse(out) as { alerts?: SqueezeAlert[] };
        resolve(parsed.alerts ?? []);
      } catch { reject(new Error(`Bad JSON: ${out}`)); }
    });
  });
}

// Notifica por e-mail o progresso de um setup de squeeze (tools.py::
// check_squeeze_setup) em dois níveis: "near" (falta só 1-2 dos 4
// requisitos -- risco de squeeze alto exige 2+ sinais perigosos, reversão
// técnica exige 2+ confirmações -- lista o que ainda falta) e "confirmed"
// (os 4 batidos). Dedup por (ticker, tier, dia BRT): evita reenviar e-mail
// a cada poll de 5min enquanto o nível não muda, mas dispara de novo assim
// que "near" vira "confirmed" (chave diferente) mesmo no mesmo dia.
async function checkSqueezeAlerts(): Promise<void> {
  if (agentState.running) {
    logger.info("Squeeze alert checker: pulando ciclo -- agente diário em execução");
    return;
  }

  const settings = await getOrCreateSettings();
  if (!settings.tickers.length) return;

  let alerts: SqueezeAlert[] = [];
  try {
    alerts = await fetchSqueezeAlerts(settings.tickers);
  } catch (err) {
    logger.warn({ err }, "Squeeze alert checker: failed to fetch squeeze alerts");
    return;
  }
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

let intervalHandle: ReturnType<typeof setInterval> | null = null;

export function startAlertChecker(): void {
  if (intervalHandle) return;
  // First check after 30s startup grace period
  const firstCheck = setTimeout(() => {
    checkAlerts().catch((e) => logger.error({ e }, "Alert check error"));
    checkIntradaySpikes().catch((e) => logger.error({ e }, "Intraday spike check error"));
    checkBounceAlerts().catch((e) => logger.error({ e }, "Bounce alert check error"));
    checkSqueezeAlerts().catch((e) => logger.error({ e }, "Squeeze alert check error"));
  }, 30_000);
  // Then every 5 min
  intervalHandle = setInterval(() => {
    checkAlerts().catch((e) => logger.error({ e }, "Alert check error"));
    checkIntradaySpikes().catch((e) => logger.error({ e }, "Intraday spike check error"));
    checkBounceAlerts().catch((e) => logger.error({ e }, "Bounce alert check error"));
    checkSqueezeAlerts().catch((e) => logger.error({ e }, "Squeeze alert check error"));
  }, CHECK_INTERVAL_MS);
  logger.info("Price alert checker started (interval: 5 min)");
}
