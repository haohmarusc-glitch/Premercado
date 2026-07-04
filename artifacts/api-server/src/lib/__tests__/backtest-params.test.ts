import { describe, it, expect } from "vitest";
import { clamp, optionalPct } from "../backtest-params";

describe("clamp", () => {
  it("returns the default when value is missing", () => {
    expect(clamp(undefined, 0, 1, 0.5)).toBe(0.5);
  });

  it("returns the default when value is not a number", () => {
    expect(clamp("abc", 0, 1, 0.5)).toBe(0.5);
  });

  it("clamps below the minimum", () => {
    expect(clamp(-5, 0, 100, 10)).toBe(0);
  });

  it("clamps above the maximum", () => {
    expect(clamp(500, 0, 100, 10)).toBe(100);
  });

  it("passes through a valid in-range value", () => {
    expect(clamp("42", 0, 100, 10)).toBe(42);
  });
});

describe("optionalPct", () => {
  it("returns undefined for missing/empty values (SL/TP off)", () => {
    expect(optionalPct(undefined)).toBeUndefined();
    expect(optionalPct(null)).toBeUndefined();
    expect(optionalPct("")).toBeUndefined();
  });

  it("returns undefined for zero or negative (doesn't make sense as a distance)", () => {
    expect(optionalPct(0)).toBeUndefined();
    expect(optionalPct(-0.05)).toBeUndefined();
  });

  it("returns undefined for non-numeric input", () => {
    expect(optionalPct("abc")).toBeUndefined();
  });

  it("passes through a valid percentage", () => {
    expect(optionalPct("0.08")).toBe(0.08);
    expect(optionalPct(0.15)).toBe(0.15);
  });

  it("caps at 0.95 to avoid a nonsensical >=100% stop/target distance", () => {
    expect(optionalPct(2)).toBe(0.95);
  });
});
