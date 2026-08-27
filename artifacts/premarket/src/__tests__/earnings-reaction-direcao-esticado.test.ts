/**
 * A palavra QUEDA/ALTA e a contagem "X de N" que a acompanha têm que
 * descrever o MESMO subconjunto de eventos.
 *
 * WOLF (auditoria de 27/08/2026) não foi o caso, mas a mesma tela mostrou
 * numa cesta NVDA/SMCI/AVGO/SKHY/ARM: "em 0 de 1 balanços em que o papel
 * subiu ≥10% no mês anterior, a reação foi de QUEDA (média +3.25%)". A
 * palavra estava fixa em "QUEDA" para todo o bucket "esticado" e a contagem
 * sempre mostrava `esticado_caiu_n` (quantos CAÍRAM) -- então quando a
 * maioria SUBIU (0 de 1 caiu aqui), a frase afirmava queda ao lado de uma
 * média positiva, contradizendo a si mesma. Mesmo bug espelhado no bucket
 * "descontado" com ALTA fixa.
 *
 * Rodar: pnpm --filter @workspace/premarket run test -- --run earnings-reaction-direcao-esticado
 */
import { describe, it, expect } from "vitest";
import { interpretResult } from "@/pages/earnings-reaction";

function comRunup(runup: Record<string, unknown>) {
  return {
    ticker: "NVDA",
    summary: {
      n_events: 8,
      runup,
    },
  } as Parameters<typeof interpretResult>[0];
}

describe("direção do bucket esticado/descontado bate com a contagem exibida", () => {
  it("NVDA real: 0 de 1 CAIU, média positiva -- não pode sair QUEDA", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 1,
      esticado_caiu_n: 0,
      esticado_reacao_media: 3.25,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("a reação foi de ALTA");
    expect(nota).not.toContain("a reação foi de QUEDA");
    // a contagem exibida tem que ser a de quem SUBIU (1), não a de quem caiu (0)
    expect(nota).toContain("em 1 de 1 balanços");
  });

  it("SMCI real: 1 de 4 caiu, média positiva -- maioria subiu, sai ALTA com contagem 3", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 4,
      esticado_caiu_n: 1,
      esticado_reacao_media: 9.38,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("em 3 de 4 balanços");
    expect(nota).toContain("a reação foi de ALTA");
  });

  it("AVGO real: 3 de 3 caiu, média negativa -- já estava certo, continua QUEDA", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 3,
      esticado_caiu_n: 3,
      esticado_reacao_media: -9.67,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("em 3 de 3 balanços");
    expect(nota).toContain("a reação foi de QUEDA");
  });

  it("maioria caiu (2 de 3) mas a média fica positiva -- rótulo segue a maioria, não a média", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 3,
      esticado_caiu_n: 2,
      esticado_reacao_media: 1.37,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("em 2 de 3 balanços");
    expect(nota).toContain("a reação foi de QUEDA");
    expect(nota).toContain("(média +1.37%)");
  });

  it("empate mantém a leitura conservadora original (QUEDA)", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 2,
      esticado_caiu_n: 1,
      esticado_reacao_media: 0,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("em 1 de 2 balanços");
    expect(nota).toContain("a reação foi de QUEDA");
  });

  it("bucket descontado espelha a mesma regra: maioria subiu bate com ALTA e contagem de quem subiu", () => {
    const nota = interpretResult(comRunup({
      descontado_n: 1,
      descontado_subiu_n: 1,
      descontado_reacao_media: 8.64,
    })).find((n) => n.startsWith("Chegando descontado"))!;
    expect(nota).toContain("1 de 1 reações foram de ALTA");
  });

  it("bucket descontado: maioria caiu vira QUEDA com a contagem de quem caiu", () => {
    const nota = interpretResult(comRunup({
      descontado_n: 3,
      descontado_subiu_n: 1,
      descontado_reacao_media: -4.2,
    })).find((n) => n.startsWith("Chegando descontado"))!;
    expect(nota).toContain("2 de 3 reações foram de QUEDA");
  });

  it("sem média (null), o rótulo cai no fallback pela contagem sem quebrar", () => {
    const nota = interpretResult(comRunup({
      esticado_corte_pct: 10,
      esticado_n: 2,
      esticado_caiu_n: 2,
      esticado_reacao_media: null,
    })).find((n) => n.startsWith('Padrão "chegou esticado"'))!;
    expect(nota).toContain("em 2 de 2 balanços");
    expect(nota).toContain("a reação foi de QUEDA");
    expect(nota).not.toContain("(média");
  });
});
