import { describe, it, expect } from "vitest";
import { computeOpenLotTotals, isActivePosition, isPositionActiveFromLots, carteiraParaOAgente } from "../portfolio-math";

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

describe("isPositionActiveFromLots", () => {
  it("treats a position as inactive when every lot is sold, regardless of stale stored quantity", () => {
    // Regression test: MU apareceu no Painel de Cenários com os 2 lotes já
    // vendidos porque a posição tinha um `quantity` armazenado desatualizado
    // (PUT /portfolio/:id permite editar esse campo direto). Os lotes reais
    // são a fonte de verdade -- devem vencer o campo travado.
    const lots = [
      { saleDate: "2026-06-18", salePrice: 1133.99 },
      { saleDate: "2026-06-18", salePrice: 1133.99 },
    ];
    expect(isPositionActiveFromLots(5, lots)).toBe(false);
  });

  it("treats a position as active when at least one lot is still open", () => {
    const lots = [
      { saleDate: "2026-06-18", salePrice: 1133.99 }, // vendido
      { saleDate: null, salePrice: null },            // ainda em aberto
    ];
    expect(isPositionActiveFromLots(0, lots)).toBe(true);
  });

  it("treats a lot with only saleDate or only salePrice set as still open", () => {
    // recomputePosition/routes/portfolio.ts só considera um lote fechado
    // quando os dois campos estão preenchidos -- mesma regra aqui.
    const lots = [{ saleDate: "2026-06-18", salePrice: null }];
    expect(isPositionActiveFromLots(0, lots)).toBe(true);
  });

  it("falls back to the stored quantity when the position has no purchase lots at all", () => {
    // Caso raro: falha ao criar o primeiro lote junto com a posição (ver
    // PositionDialog no frontend) -- sem nenhum lote pra consultar, a única
    // fonte de verdade disponível é o campo armazenado.
    expect(isPositionActiveFromLots(3, [])).toBe(true);
    expect(isPositionActiveFromLots(0, [])).toBe(false);
  });
});

describe("carteiraParaOAgente", () => {
  it("o banco manda quando tem posição", () => {
    expect(carteiraParaOAgente(["NVDA", "HCC"], "GOOGL,TSLA")).toBe("NVDA,HCC");
  });

  it("carteira vazia cai na env var (escape hatch de carteira hipotética)", () => {
    expect(carteiraParaOAgente([], "GOOGL,TSLA")).toBe("GOOGL,TSLA");
  });

  it("sem banco e sem env, devolve vazio pro Python usar o default dele", () => {
    expect(carteiraParaOAgente([], undefined)).toBe("");
    expect(carteiraParaOAgente([], "")).toBe("");
  });

  it("uma posição só continua sendo a carteira, não cai no fallback", () => {
    // O bug de origem era justamente a lista do código vencer a realidade.
    expect(carteiraParaOAgente(["HCC"], "NVDA,SMCI,GOOGL,ARM,AVGO,MRVL,SKHY,TSLA")).toBe("HCC");
  });

  // ── o vazamento de 26/08/2026 ──────────────────────────────────────────
  //
  // Uma conta SEM posições abriu o Veredito do Dia e recebeu um veredito
  // sobre NVDA, SMCI, GOOGL, ARM, AVGO, MRVL, SKHY e TSLA -- a carteira do
  // operador, que mora em AGENT_PORTFOLIO_TICKERS. O texto trazia o plano de
  // saída dele, os cenários dele e os valores em reais dele, enquanto os
  // painéis estruturados da MESMA tela diziam "Sem posições na carteira".
  //
  // `getPortfolioTickers` já devolvia [] de propósito, com um comentário
  // dizendo por quê. Esta função desfazia isso uma camada acima.

  it("carteira vazia de um USUÁRIO não cai na env var", () => {
    const doOperador = "NVDA,SMCI,GOOGL,ARM,AVGO,MRVL,SKHY,TSLA";
    expect(carteiraParaOAgente([], doOperador, true)).toBe("");
  });

  it("o escape hatch continua valendo para as runs não escopadas", () => {
    // Uma run agendada não tem "usuário da requisição", e a env var segue
    // sendo o jeito de rodar contra uma carteira hipotética.
    expect(carteiraParaOAgente([], "GOOGL,TSLA", false)).toBe("GOOGL,TSLA");
    expect(carteiraParaOAgente([], "GOOGL,TSLA")).toBe("GOOGL,TSLA");
  });

  it("com posições o escopo não muda nada", () => {
    expect(carteiraParaOAgente(["HCC"], "NVDA,TSLA", true)).toBe("HCC");
    expect(carteiraParaOAgente(["HCC"], "NVDA,TSLA", false)).toBe("HCC");
  });
});
