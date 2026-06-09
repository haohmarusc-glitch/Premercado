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
    logger.info("Daily scheduler disabled via settings");
  } else {
    scheduleTask(settings.scheduleHour, settings.scheduleMinute);
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
      return;
    }
  } catch (_) {
    // DB not ready yet, fall back to defaults
  }
  // Defaults: 8:30 BRT, premarket disabled
  scheduleTask(8, 30);
  state.scheduleEnabled = true;
  state.nextRunAt = nextOccurrence(8, 30).toISOString();
  logger.info(
    { nextRunAt: state.nextRunAt, cron: cronExpr(8, 30), tz: TIMEZONE },
    "Pre-market scheduler started (defaults 08:30 BRT, intraday disabled)",
  );
}
