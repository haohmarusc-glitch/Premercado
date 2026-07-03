import { describe, it, expect } from "vitest";
import { rankScreenerItems } from "../pages/screener";
import type { TrendItem } from "../components/trend-card";

const item = (over: Partial<TrendItem>): TrendItem => ({ ticker: "X", ...over });

describe("rankScreenerItems", () => {
  it("sorts by score descending", () => {
    const items = [
      item({ ticker: "A", score: 10, sinal: "aguardar" }),
      item({ ticker: "B", score: 80, sinal: "compra" }),
      item({ ticker: "C", score: -60, sinal: "venda" }),
    ];
    const ranked = rankScreenerItems(items, "todos");
    expect(ranked.map((r) => r.ticker)).toEqual(["B", "A", "C"]);
  });

  it("drops items with no score or with an error", () => {
    const items = [
      item({ ticker: "A", score: 10 }),
      item({ ticker: "B", score: undefined }),
      item({ ticker: "C", score: 20, error: "Dados insuficientes" }),
    ];
    const ranked = rankScreenerItems(items, "todos");
    expect(ranked.map((r) => r.ticker)).toEqual(["A"]);
  });

  it("filters by sinal when not 'todos'", () => {
    const items = [
      item({ ticker: "A", score: 10, sinal: "compra" }),
      item({ ticker: "B", score: 80, sinal: "aguardar" }),
      item({ ticker: "C", score: -60, sinal: "venda" }),
    ];
    expect(rankScreenerItems(items, "compra").map((r) => r.ticker)).toEqual(["A"]);
    expect(rankScreenerItems(items, "venda").map((r) => r.ticker)).toEqual(["C"]);
  });

  it("returns an empty array when nothing matches", () => {
    const items = [item({ ticker: "A", score: 10, sinal: "compra" })];
    expect(rankScreenerItems(items, "venda")).toEqual([]);
  });
});
