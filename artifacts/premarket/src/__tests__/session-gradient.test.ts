import { describe, it, expect } from "vitest";
import { sessionGradientStops, hasExtendedSession, SESSION_COLORS } from "../components/session-gradient";

describe("hasExtendedSession", () => {
  it("retorna false quando todos os candles são do pregão regular", () => {
    expect(hasExtendedSession([{ session: "regular" }, { session: "regular" }])).toBe(false);
  });

  it("retorna true quando há pelo menos um candle de pré ou pós-mercado", () => {
    expect(hasExtendedSession([{ session: "regular" }, { session: "post" }])).toBe(true);
    expect(hasExtendedSession([{ session: "pre" }, { session: "regular" }])).toBe(true);
  });

  it("retorna false pra lista vazia", () => {
    expect(hasExtendedSession([])).toBe(false);
  });
});

describe("sessionGradientStops", () => {
  it("gera uma cor só quando não há candles", () => {
    const stops = sessionGradientStops([], "#4ade80");
    expect(stops).toEqual([
      { offset: "0%", color: "#4ade80" },
      { offset: "100%", color: "#4ade80" },
    ]);
  });

  it("gera uma cor só quando todos os candles são regulares", () => {
    const candles = [{ session: "regular" }, { session: "regular" }, { session: "regular" }];
    const stops = sessionGradientStops(candles, "#4ade80");
    expect(stops).toEqual([
      { offset: "0%", color: "#4ade80" },
      { offset: "100%", color: "#4ade80" },
    ]);
  });

  it("insere hardstops exatamente no índice onde a sessão muda", () => {
    // 4 candles: pre, pre, regular, post -- transições no índice 2 e 3
    const candles = [{ session: "pre" }, { session: "pre" }, { session: "regular" }, { session: "post" }];
    const stops = sessionGradientStops(candles, "#4ade80");
    expect(stops).toEqual([
      { offset: "0%", color: SESSION_COLORS.pre },
      { offset: `${(2 / 3) * 100}%`, color: SESSION_COLORS.pre },
      { offset: `${(2 / 3) * 100}%`, color: "#4ade80" },
      { offset: "100%", color: "#4ade80" },
      { offset: "100%", color: SESSION_COLORS.post },
    ]);
  });

  it("trata session ausente/desconhecida como regular", () => {
    const candles = [{ session: undefined }, { session: "unknown" }];
    const stops = sessionGradientStops(candles, "#f87171");
    expect(stops).toEqual([
      { offset: "0%", color: "#f87171" },
      { offset: "100%", color: "#f87171" },
    ]);
  });
});
