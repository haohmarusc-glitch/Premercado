/**
 * A leitura interpretada do modo Carteira ($100k) diz as três frases que os
 * cards sozinhos escondem: quem venceu (carteira ou buy & hold equal-weight),
 * quanto custou a AUSÊNCIA (dias sem posição — o custo dominante no
 * diagnóstico de 20/08), e quando a "diversificação" é o mesmo trade contado
 * duas vezes (2+ posições do mesmo grupo setorial).
 */
import { describe, it, expect } from "vitest";
import { interpretPortfolioResult } from "../pages/backtest";

function base(sobrescreve: Record<string, unknown> = {}) {
  return {
    strategy: "confluencia", start: "2026-01-01", end: "2026-08-01",
    tickersRequested: 2, tickersOk: 2, failed: [],
    initialCapital: 100_000, finalValue: 110_000,
    totalReturn: 10, buyAndHoldReturn: 20, cagr: 12, sharpe: 0.8,
    sortino: null, calmar: null, maxDrawdown: -8,
    profitFactor: 1.2, expectancy: 0.5, payoff: 1.1,
    bootstrap: null, totalTrades: 12, winRate: 55, avgWin: 3, avgLoss: -2,
    trades: [], equityCurve: [],
    exposicao: { pctDiasSemPosicao: 10, mediaPosicoesAbertas: 1.5, maxPosicoesSimultaneas: 2, picoExposicaoPct: 90 },
    porTicker: [], porSetor: [],
    ...sobrescreve,
  } as never;
}

describe("interpretPortfolioResult", () => {
  it("diz quando o buy & hold equal-weight venceu a carteira operada", () => {
    const notas = interpretPortfolioResult(base()).join(" ");
    expect(notas).toContain("atrás do buy & hold equal-weight");
  });

  it("aponta ausência como custo quando a carteira fica muito tempo fora", () => {
    const notas = interpretPortfolioResult(base({
      exposicao: { pctDiasSemPosicao: 71, mediaPosicoesAbertas: 0.4, maxPosicoesSimultaneas: 1, picoExposicaoPct: 50 },
    })).join(" ");
    expect(notas).toContain("sem NENHUMA posição");
  });

  it("chama concentração setorial pelo nome", () => {
    const notas = interpretPortfolioResult(base({
      porSetor: [{ sector: "memory", label: "Memória", tickers: ["MU", "SNDK"],
                   maxSimultaneas: 2, pctDiasCom2ouMais: 45 }],
    })).join(" ");
    expect(notas).toContain("mesmo trade contado duas vezes");
    expect(notas).toContain("MU, SNDK");
  });

  it("IC do bootstrap cruzando o zero vira frase, não rodapé", () => {
    const notas = interpretPortfolioResult(base({
      bootstrap: { nTrades: 12, amostras: 2000, contribuicaoIc95: [-4.1, 9.8], winRateIc95: [30, 75] },
    })).join(" ");
    expect(notas).toContain("não se distingue de sorte de sequência");
  });

  it("capital parado em caixa vira frase quando a exposição média fica baixa", () => {
    const notas = interpretPortfolioResult(base({
      exposicao: { pctDiasSemPosicao: 0.8, mediaPosicoesAbertas: 11, maxPosicoesSimultaneas: 14,
                   picoExposicaoPct: 100, mediaExposicaoPct: 74 },
    })).join(" ");
    expect(notas).toContain("parado em caixa");
  });

  it("amostra pequena repassa o aviso do bootstrap", () => {
    const notas = interpretPortfolioResult(base({
      bootstrap: { aviso: "3 trades -- amostra pequena demais para intervalo de confiança (mínimo 10)" },
    })).join(" ");
    expect(notas).toContain("amostra pequena demais");
  });
});
