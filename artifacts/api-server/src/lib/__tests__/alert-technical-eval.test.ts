import { describe, it, expect } from "vitest";
import { evalTechnical } from "../alert-technical-eval";

describe("evalTechnical", () => {
  describe("rsi", () => {
    it("fires when RSI is at/above the threshold on 'above'", () => {
      const alert = { indicator: "rsi", condition: "above", thresholdValue: 70 };
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 72 })).toBe(72);
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 70 })).toBe(70);
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 69.9 })).toBeNull();
    });

    it("fires when RSI is at/below the threshold on 'below'", () => {
      const alert = { indicator: "rsi", condition: "below", thresholdValue: 30 };
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 25 })).toBe(25);
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 30.1 })).toBeNull();
    });

    it("does not fire when rsi or thresholdValue is missing", () => {
      const alert = { indicator: "rsi", condition: "below", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", rsi: 25 })).toBeNull();
      const alert2 = { indicator: "rsi", condition: "below", thresholdValue: 30 };
      expect(evalTechnical(alert2, { ticker: "NVDA", rsi: null })).toBeNull();
    });
  });

  describe("macd", () => {
    it("fires on 'above' only when the histogram is strictly positive (bullish)", () => {
      const alert = { indicator: "macd", condition: "above", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", macdHistogram: 0.5 })).toBe(0.5);
      expect(evalTechnical(alert, { ticker: "NVDA", macdHistogram: 0 })).toBeNull();
      expect(evalTechnical(alert, { ticker: "NVDA", macdHistogram: -0.1 })).toBeNull();
    });

    it("fires on 'below' only when the histogram is strictly negative (bearish)", () => {
      const alert = { indicator: "macd", condition: "below", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", macdHistogram: -0.3 })).toBe(-0.3);
      expect(evalTechnical(alert, { ticker: "NVDA", macdHistogram: 0.1 })).toBeNull();
    });
  });

  describe("sma20 / sma50", () => {
    it("fires when price crosses above the given SMA", () => {
      const alert = { indicator: "sma20", condition: "above", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", price: 105, sma20: 100 })).toBe(105);
      expect(evalTechnical(alert, { ticker: "NVDA", price: 95, sma20: 100 })).toBeNull();
    });

    it("fires when price crosses below the given SMA, and uses sma50 not sma20", () => {
      const alert = { indicator: "sma50", condition: "below", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", price: 90, sma50: 100, sma20: 90 })).toBe(90);
      expect(evalTechnical(alert, { ticker: "NVDA", price: 110, sma50: 100, sma20: 90 })).toBeNull();
    });

    it("does not fire when price or the sma is missing", () => {
      const alert = { indicator: "sma20", condition: "above", thresholdValue: null };
      expect(evalTechnical(alert, { ticker: "NVDA", price: 105, sma20: null })).toBeNull();
      expect(evalTechnical(alert, { ticker: "NVDA", price: null, sma20: 100 })).toBeNull();
    });
  });

  it("returns null for an unknown indicator", () => {
    const alert = { indicator: "unknown", condition: "above", thresholdValue: null };
    expect(evalTechnical(alert, { ticker: "NVDA", price: 105 })).toBeNull();
  });
});
