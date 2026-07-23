import { describe, it, expect } from "vitest";
import { indicatorBadgeLabel, parseAlertPrefill } from "../pages/alerts";

describe("indicatorBadgeLabel", () => {
  it("formats a price alert with an absolute threshold", () => {
    const label = indicatorBadgeLabel({ condition: "above", thresholdPrice: 865, thresholdPct: null });
    expect(label).toBe("↑ acima de $865.00");
  });

  it("formats a price alert with a percent threshold", () => {
    const label = indicatorBadgeLabel({ condition: "below", thresholdPrice: null, thresholdPct: -5 });
    expect(label).toBe("↓ abaixo de -5%");
  });

  it("formats an rsi alert", () => {
    const label = indicatorBadgeLabel({ indicator: "rsi", condition: "below", thresholdValue: 30 });
    expect(label).toBe("RSI abaixo de 30");
  });

  it("formats a macd alert", () => {
    expect(indicatorBadgeLabel({ indicator: "macd", condition: "above" })).toBe("MACD bullish");
    expect(indicatorBadgeLabel({ indicator: "macd", condition: "below" })).toBe("MACD bearish");
  });

  it("formats sma20/sma50 alerts", () => {
    expect(indicatorBadgeLabel({ indicator: "sma20", condition: "above" })).toBe("preço cruzou acima da SMA20");
    expect(indicatorBadgeLabel({ indicator: "sma50", condition: "below" })).toBe("preço cruzou abaixo da SMA50");
  });
});

describe("parseAlertPrefill", () => {
  it("lê symbol/price/condition vindos do menu de botão direito no gráfico", () => {
    expect(parseAlertPrefill("symbol=AVGO&price=396.81&condition=below")).toEqual({
      symbol: "AVGO",
      condition: "below",
      price: "396.81",
    });
  });

  it("deixa em maiúsculas o symbol vindo da URL", () => {
    expect(parseAlertPrefill("symbol=avgo")).toEqual({ symbol: "AVGO" });
  });

  it("ignora condition inválida", () => {
    expect(parseAlertPrefill("condition=sideways")).toEqual({});
  });

  it("ignora price não numérico", () => {
    expect(parseAlertPrefill("price=abc")).toEqual({});
  });

  it("retorna objeto vazio pra query string vazia", () => {
    expect(parseAlertPrefill("")).toEqual({});
  });
});
