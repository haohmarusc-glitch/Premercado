import { describe, it, expect } from "vitest";
import { computeOpenLotTotals, isActivePosition } from "../portfolio-math";

describe("computeOpenLotTotals", () => {
  it("returns zeroed totals for no open lots", () => {
    expect(computeOpenLotTotals([])).toEqual({ quantity: 0, avgCost: 0, investedAmount: 0 });
  });

  it("computes quantity/avgCost/investedAmount when every lot has a price", () => {
    const totals = computeOpenLotTotals([
      { amount: 300, purchasePrice: 100 }, // 3 shares
      { amount: 200, purchasePrice: 50 },  // 4 shares
    ]);
    expect(totals.quantity).toBeCloseTo(7, 6);
    expect(totals.investedAmount).toBeCloseTo(500, 6);
    expect(totals.avgCost).toBeCloseTo(500 / 7, 6);
  });

  it("counts an unpriced lot's money in investedAmount but not in quantity or avgCost", () => {
    // Regression test for the bug where an unpriced lot's amount inflated
    // avgCost for the whole position because it was included in the
    // avgCost numerator (totalInvested) but not in the denominator (shares).
    const totals = computeOpenLotTotals([
      { amount: 300, purchasePrice: 100 }, // 3 shares, priced
      { amount: 200, purchasePrice: null }, // unpriced: money invested, shares unknown
    ]);
    expect(totals.quantity).toBeCloseTo(3, 6);
    expect(totals.investedAmount).toBeCloseTo(500, 6);
    // avgCost must reflect only the priced lot (100), NOT (300+200)/3 = 166.67
    expect(totals.avgCost).toBeCloseTo(100, 6);
  });

  it("treats a zero purchasePrice the same as an unpriced lot (avoids division by zero)", () => {
    const totals = computeOpenLotTotals([
      { amount: 300, purchasePrice: 100 },
      { amount: 50, purchasePrice: 0 },
    ]);
    expect(totals.quantity).toBeCloseTo(3, 6);
    expect(totals.investedAmount).toBeCloseTo(350, 6);
    expect(totals.avgCost).toBeCloseTo(100, 6);
  });

  it("returns zero avgCost/quantity when no lot has a usable price, but keeps investedAmount", () => {
    const totals = computeOpenLotTotals([
      { amount: 300, purchasePrice: null },
      { amount: 200, purchasePrice: null },
    ]);
    expect(totals.quantity).toBe(0);
    expect(totals.avgCost).toBe(0);
    expect(totals.investedAmount).toBeCloseTo(500, 6);
  });
});

describe("isActivePosition", () => {
  it("treats zero quantity as not active (fully sold)", () => {
    expect(isActivePosition(0)).toBe(false);
  });

  it("treats a real held quantity as active", () => {
    expect(isActivePosition(1.5)).toBe(true);
  });

  it("treats a tiny floating-point residual near zero as not active", () => {
    // O driver pg devolve `numeric` como string -- e recomputePosition pode
    // deixar um resíduo de ponto flutuante em vez de exatamente 0 quando
    // todos os lotes são vendidos (ver comentário da função).
    expect(isActivePosition("0.0000001")).toBe(false);
  });

  it("accepts string quantities from the pg numeric column", () => {
    expect(isActivePosition("2.5")).toBe(true);
  });
});
