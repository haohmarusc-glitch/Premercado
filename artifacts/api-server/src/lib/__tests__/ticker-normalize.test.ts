import { describe, it, expect } from "vitest";
import { normalizeTicker } from "../ticker-normalize";

describe("normalizeTicker", () => {
  it("converte o sufixo :BVMF do Google Finanças pro .SA do Yahoo (B3)", () => {
    expect(normalizeTicker("AMBP3:BVMF")).toBe("AMBP3.SA");
  });

  it("é case-insensitive e ignora espaços nas pontas", () => {
    expect(normalizeTicker("  ambp3:bvmf  ")).toBe("AMBP3.SA");
  });

  it("remove sufixos de bolsa dos EUA (Yahoo não usa sufixo pra elas)", () => {
    expect(normalizeTicker("NVDA:NASDAQ")).toBe("NVDA");
    expect(normalizeTicker("MU:NYSE")).toBe("MU");
    expect(normalizeTicker("SPY:NYSEARCA")).toBe("SPY");
  });

  it("mantém tickers já no formato do Yahoo (com ponto) inalterados", () => {
    expect(normalizeTicker("AMBP3.SA")).toBe("AMBP3.SA");
    expect(normalizeTicker("NVDA")).toBe("NVDA");
    expect(normalizeTicker("BRK-B")).toBe("BRK-B");
  });

  it("bolsa desconhecida: só normaliza maiúsculas/espaços, não mexe no sufixo", () => {
    expect(normalizeTicker("PETR4:XLON")).toBe("PETR4:XLON");
  });
});
