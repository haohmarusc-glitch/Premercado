import cron, { ScheduledTask } from "node-cron";
import { logger } from "./logger";
import { runAgent, state } from "./runner";
import type { Settings } from "@workspace/db";

const TIMEZONE = "America/Sao_Paulo";

// ── Daily scheduler ───────────────────────────────────────────────────────────

let currentTask: ScheduledTask | null = null;
let currentHour = 8;
let currentMinute = 30;
let scheduleEnabled = true;

function cronExpr(hour: number, minute: number): string {
  return `${minute} ${hour} * * 1-5`;
}

// A gestão de alertas roda numa run própria, sempre um bom tempo depois do
// horário configurado do diário -- runAgent() só permite uma run por vez
// (state.running), então se agendarmos perto demais do horário do diário,
// corremos o risco de cair bem no meio dele e a run de alertas ser
// simplesmente pulada (o oposto do que essa separação deveria garantir).
// TIMEOUT_MS do diário é 30min (ver runner.ts) -- 45min de folga cobre isso
// com margem mesmo num dia de análise mais longa.
const ALERTS_OFFSET_MIN = 45;

function addMinutes(hour: number, minute: number, offsetMin: number): { hour: number; minute: number } {
  const total = (hour * 60 + minute + offsetMin) % (24 * 60);
  return { hour: Math.floor(total / 60), minute: total % 60 };
}

function nextOccurrence(hour: number, minute: number): Date {
  const now = new Date();
  const spNow = new Date(now.toLocaleString("en-US", { timeZone: TIMEZONE }));
  const candidate = new Date(spNow);
  candidate.setHours(hour, minute, 0, 0);
  if (candidate <= spNow) candidate.setDate(candidate.getDate() + 1);
  while (candidate.getDay() === 0 || candidate.getDay() === 6) {
    candidate.setDate(candidate.getDate() + 1);
  }
  // SP is UTC-3
  return new Date(candidate.getTime() + 3 * 60 * 60 * 1000);
}

function scheduleTask(hour: number, minute: number): void {
  if (currentTask) {
    currentTask.stop();
    currentTask = null;
  }
  currentHour = hour;
  currentMinute = minute;
  currentTask = cron.schedule(
    cronExpr(hour, minute),
    () => {
      logger.info("Scheduled pre-market agent run triggered");
      runAgent("scheduled");
      state.nextRunAt = nextOccurrence(currentHour, currentMinute).toISOString();
    },
    { timezone: TIMEZONE },
  );
}

// ── Alerts management scheduler (run própria, ver ALERTS_OFFSET_MIN acima) ────

let alertsTask: ScheduledTask | null = null;

function scheduleAlertsTask(hour: number, minute: number): void {
  if (alertsTask) {
    alertsTask.stop();
    alertsTask = null;
  }
  const { hour: alertsHour, minute: alertsMinute } = addMinutes(hour, minute, ALERTS_OFFSET_MIN);
  alertsTask = cron.schedule(
    cronExpr(alertsHour, alertsMinute),
    () => {
      logger.info("Scheduled alerts-management run triggered");
      runAgent("alerts");
    },
    { timezone: TIMEZONE },
  );
  logger.info({ hour: alertsHour, minute: alertsMinute, tz: TIMEZONE }, "Alerts-management scheduler started");
}

function stopAlertsTask(): void {
  if (alertsTask) {
    alertsTask.stop();
    alertsTask = null;
  }
}

// ── Intraday pre-market scheduler ─────────────────────────────────────────────

let premarketTask: ScheduledTask | null = null;

/**
 * Cron that fires every `intervalMin` minutes during [startHour, endHour) on weekdays.
 * Example: intervalMin=30, startHour=6, endHour=9 → `*\/30 6-8 * * 1-5`
 */
function premarketCronExpr(intervalMin: number, startHour: number, endHour: number): string {
  const clampedEnd = Math.max(startHour, endHour - 1);
  return `*/${intervalMin} ${startHour}-${clampedEnd} * * 1-5`;
}

function schedulePremarketTask(intervalMin: number, startHour: number, endHour: number): void {
  if (premarketTask) {
    premarketTask.stop();
    premarketTask = null;
  }
  const expr = premarketCronExpr(intervalMin, startHour, endHour);
  premarketTask = cron.schedule(
    expr,
    () => {
      logger.info({ expr }, "Intraday pre-market scan triggered");
      runAgent("premarket");
    },
    { timezone: TIMEZONE },
  );
  logger.info({ cron: expr, intervalMin, startHour, endHour, tz: TIMEZONE }, "Intraday pre-market scheduler started");
}

function stopPremarketTask(): void {
  if (premarketTask) {
    premarketTask.stop();
    premarketTask = null;
  }
}

// ── Retrato diário do risco macro ────────────────────────────────────────────
//
// Fixo, não configurável: diferente do diário e dos alertas, este não gasta
// LLM nem depende de preferência do usuário -- é uma leitura de mercado que ou
// acontece todo pregão ou não vira série. Um botão para desligá-lo só criaria
// buraco silencioso no histórico meses depois.
//
// 07:50 BRT = 06:50 ET, antes do pré-mercado abrir. A Ásia já fechou (é isso
// que dá as 6-8h de dianteira ao sinal de contágio) e o FRED já publicou a
// observação do dia anterior.
//
// NÃO usa runAgent(): aquilo serializa por state.running para não rodar duas
// análises de LLM ao mesmo tempo. Esta coleta é só rede e cabe em ~20s -- passar
// por ali faria o retrato ser PULADO nos dias em que o diário atrasa, que são
// justamente os dias movimentados.
const MACRO_RISK_CRON = "50 7 * * 1-5";

let macroRiskTask: ScheduledTask | null = null;

function scheduleMacroRiskTask(): void {
  if (macroRiskTask) return;
  macroRiskTask = cron.schedule(
    MACRO_RISK_CRON,
    () => {
      // coletarEPersistir nunca levanta: exceção não tratada dentro de um task
      // do node-cron derruba o agendamento em silêncio até o próximo boot, e o
      // sintoma seria a série parando de crescer sem nenhum erro no log.
      void import("./macro-risk").then(({ coletarEPersistir }) => coletarEPersistir("cron"));
    },
    { timezone: TIMEZONE },
  );
  logger.info({ cron: MACRO_RISK_CRON, tz: TIMEZONE }, "Macro risk snapshot scheduler started");
}

// ── Unified settings application ─────────────────────────────────────────────

type SchedulerSettings = Pick<
  Settings,
  | "scheduleEnabled"
  | "scheduleHour"
  | "scheduleMinute"
  | "premarketEnabled"
  | "premarketIntervalMin"
  | "premarketWindowStartHour"
  | "premarketWindowEndHour"
>;

export function applySettings(settings: SchedulerSettings): void {
  // Daily scheduler
  scheduleEnabled = settings.scheduleEnabled;
  if (!scheduleEnabled) {
    if (currentTask) { currentTask.stop(); currentTask = null; }
    state.scheduleEnabled = false;
    state.nextRunAt = null;
    stopAlertsTask();
    logger.info("Daily scheduler disabled via settings");
  } else {
    scheduleTask(settings.scheduleHour, settings.scheduleMinute);
    scheduleAlertsTask(settings.scheduleHour, settings.scheduleMinute);
    state.scheduleEnabled = true;
    state.nextRunAt = nextOccurrence(settings.scheduleHour, settings.scheduleMinute).toISOString();
    logger.info(
      { nextRunAt: state.nextRunAt, hour: settings.scheduleHour, minute: settings.scheduleMinute },
      "Daily scheduler updated",
    );
  }

  // Intraday pre-market scheduler
  if (!settings.premarketEnabled) {
    stopPremarketTask();
    logger.info("Intraday pre-market scheduler disabled via settings");
  } else {
    schedulePremarketTask(
      settings.premarketIntervalMin,
      settings.premarketWindowStartHour,
      settings.premarketWindowEndHour,
    );
  }
}

export async function startScheduler(): Promise<void> {
  try {
    const { db, settingsTable } = await import("@workspace/db");
    const [row] = await db.select().from(settingsTable).limit(1);
    if (row) {
      applySettings(row);
      scheduleMacroRiskTask();
      return;
    }
  } catch (_) {
    // DB not ready yet, fall back to defaults
  }
  // Defaults: 8:30 BRT, premarket disabled
  scheduleTask(8, 30);
  scheduleAlertsTask(8, 30);
  scheduleMacroRiskTask();
  state.scheduleEnabled = true;
  state.nextRunAt = nextOccurrence(8, 30).toISOString();
  logger.info(
    { nextRunAt: state.nextRunAt, cron: cronExpr(8, 30), tz: TIMEZONE },
    "Pre-market scheduler started (defaults 08:30 BRT, intraday disabled)",
  );
}
