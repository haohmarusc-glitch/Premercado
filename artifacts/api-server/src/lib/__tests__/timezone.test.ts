import { describe, it, expect } from "vitest";
import { startOfTodayBRT, todayBRTDateString } from "../timezone";

describe("startOfTodayBRT", () => {
  it("returns 03:00 UTC (00:00 BRT) for a moment in the middle of the BRT day", () => {
    // 2026-07-03T15:00:00Z = 12:00 BRT on 2026-07-03
    const now = new Date("2026-07-03T15:00:00Z");
    expect(startOfTodayBRT(now).toISOString()).toBe("2026-07-03T03:00:00.000Z");
  });

  it("does not roll over to the next BRT day 3 hours early like a naive UTC boundary would", () => {
    // Regression test: 2026-07-03T23:30:00Z = 20:30 BRT, still July 3rd BRT.
    // A naive `new Date(); setHours(0,0,0,0)` on a UTC-TZ process would treat
    // this as already being in the "2026-07-04" UTC bucket.
    const now = new Date("2026-07-03T23:30:00Z");
    expect(startOfTodayBRT(now).toISOString()).toBe("2026-07-03T03:00:00.000Z");
  });

  it("rolls over exactly at 03:00 UTC (00:00 BRT)", () => {
    const justBefore = new Date("2026-07-04T02:59:59Z"); // 23:59:59 BRT July 3
    const justAfter = new Date("2026-07-04T03:00:00Z"); // 00:00:00 BRT July 4
    expect(startOfTodayBRT(justBefore).toISOString()).toBe("2026-07-03T03:00:00.000Z");
    expect(startOfTodayBRT(justAfter).toISOString()).toBe("2026-07-04T03:00:00.000Z");
  });
});

describe("todayBRTDateString", () => {
  it("returns the BRT calendar date, not the UTC one, late in the BRT evening", () => {
    // 2026-07-04T01:00:00Z = 22:00 BRT on 2026-07-03 (still July 3rd for the user)
    const now = new Date("2026-07-04T01:00:00Z");
    expect(todayBRTDateString(now)).toBe("2026-07-03");
  });

  it("matches the UTC date during the middle of the BRT day", () => {
    const now = new Date("2026-07-03T15:00:00Z");
    expect(todayBRTDateString(now)).toBe("2026-07-03");
  });
});
