import cron, { ScheduledTask } from "node-cron";
import { logger } from "./logger";
import { runAgent, state } from "./runner";
import type { Settings } from "@workspace/db";

const TIMEZONE = "America/Sao_Paulo";

let currentTask: ScheduledTask | null = null;
let currentHour = 8;
let scheduleEnabled = true;

function cronExpr(hour: number): string {
  return `0 ${hour} * * 1-5`;
}

function nextOccurrence(hour: number): Date {
  const now = new Date();
  const spNow = new Date(now.toLocaleString("en-US", { timeZone: TIMEZONE }));
  const candidate = new Date(spNow);
  candidate.setHours(hour, 0, 0, 0);
  if (candidate <= spNow) candidate.setDate(candidate.getDate() + 1);
  while (candidate.getDay() === 0 || candidate.getDay() === 6) {
    candidate.setDate(candidate.getDate() + 1);
  }
  // SP is UTC-3
  return new Date(candidate.getTime() + 3 * 60 * 60 * 1000);
}

function scheduleTask(hour: number): void {
  if (currentTask) {
    currentTask.stop();
    currentTask = null;
  }
  currentHour = hour;
  currentTask = cron.schedule(
    cronExpr(hour),
    () => {
      logger.info("Scheduled pre-market agent run triggered");
      runAgent();
      state.nextRunAt = nextOccurrence(currentHour).toISOString();
    },
    { timezone: TIMEZONE },
  );
}

export function applySettings(settings: Pick<Settings, "scheduleEnabled" | "scheduleHour">): void {
  scheduleEnabled = settings.scheduleEnabled;
  if (!scheduleEnabled) {
    if (currentTask) { currentTask.stop(); currentTask = null; }
    state.scheduleEnabled = false;
    state.nextRunAt = null;
    logger.info("Scheduler disabled via settings");
    return;
  }
  scheduleTask(settings.scheduleHour);
  state.scheduleEnabled = true;
  state.nextRunAt = nextOccurrence(settings.scheduleHour).toISOString();
  logger.info({ nextRunAt: state.nextRunAt, hour: settings.scheduleHour }, "Scheduler updated");
}

export async function startScheduler(): Promise<void> {
  // Load settings from DB at startup, with env fallback
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
  // Defaults
  scheduleTask(8);
  state.scheduleEnabled = true;
  state.nextRunAt = nextOccurrence(8).toISOString();
  logger.info({ nextRunAt: state.nextRunAt, cron: cronExpr(8), tz: TIMEZONE }, "Pre-market scheduler started (defaults)");
}
