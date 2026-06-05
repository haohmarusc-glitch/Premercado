/**
 * Cron scheduler — fires the pre-market agent every weekday at 08:00 São Paulo time (UTC-3).
 * Cron expression: "0 8 * * 1-5" with timezone "America/Sao_Paulo"
 */
import cron from "node-cron";
import { logger } from "./logger";
import { runAgent, state } from "./runner";

const CRON_EXPR = "0 8 * * 1-5"; // Mon–Fri at 08:00
const TIMEZONE = "America/Sao_Paulo";

function nextOccurrence(): Date {
  const now = new Date();
  // Find next weekday 08:00 São Paulo time
  // We compute by advancing day-by-day until we hit a weekday with 08:00 > now
  const spNow = new Date(
    now.toLocaleString("en-US", { timeZone: TIMEZONE }),
  );
  let candidate = new Date(spNow);
  candidate.setSeconds(0);
  candidate.setMilliseconds(0);
  candidate.setHours(8, 0, 0, 0);
  if (candidate <= spNow) {
    candidate.setDate(candidate.getDate() + 1);
  }
  // Skip weekends
  while (candidate.getDay() === 0 || candidate.getDay() === 6) {
    candidate.setDate(candidate.getDate() + 1);
  }
  // Convert back to UTC: SP is UTC-3
  const offsetMs = 3 * 60 * 60 * 1000;
  return new Date(candidate.getTime() + offsetMs);
}

export function startScheduler(): void {
  state.nextRunAt = nextOccurrence().toISOString();
  logger.info(
    { nextRunAt: state.nextRunAt, cron: CRON_EXPR, tz: TIMEZONE },
    "Pre-market scheduler started",
  );

  cron.schedule(
    CRON_EXPR,
    () => {
      logger.info("Scheduled pre-market agent run triggered");
      runAgent();
      // Update next occurrence after firing
      state.nextRunAt = nextOccurrence().toISOString();
    },
    { timezone: TIMEZONE },
  );
}
