import { describe, it, expect } from "vitest";
import { computeOpenLotTotals } from "../portfolio-math";

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
