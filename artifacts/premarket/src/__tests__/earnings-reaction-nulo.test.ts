/**
 * Campo nulo não pode derrubar a tela — nem virar conclusão.
 *
 * Produção 18/08/2026. A tela de Reação a Earnings ficou PRETA: sem cabeçalho,
 * sem menu, sem mensagem. O console tinha uma linha só:
 *
 *     Uncaught TypeError: Cannot read properties of null (reading 'toFixed')
 *
 * A causa foi uma mudança minha do mesmo dia. O backend passou a serializar
 * por json_seguro: valor não-finito virou `null` em vez de `NaN`, porque
 * `NaN` não é JSON válido e derrubava a resposta inteira no JSON.parse.
 *
 * Isso consertou o backend e TROCOU o modo de falhar do frontend. Antes a
 * requisição morria e a tela mostrava erro; depois ela passa, o campo chega
 * nulo, e `null.toFixed()` lança no render — o que desmonta a árvore React
 * inteira.
 *
 * A interface TypeScript declarava `number` para esses campos. Tipo mentiroso
 * não é neutro: ele SILENCIA exatamente a chamada que quebra. Declarar a
 * verdade (`number | null`) fez o compilador listar 29 pontos de uso, que é
 * muito melhor que revisar trinta chamadas a olho.
 *
 * Rodar: pnpm --filter @workspace/premarket run test -- --run earnings-reaction-nulo
 */
import { describe, it, expect } from "vitest";
import {
  temNumero, fmtPct, fmtUsd, fmtNum, interpretResult,
} from "@/pages/earnings-reaction";

const TRACO = "—";

// ── formatadores ────────────────────────────────────────────────────────────

describe("formatadores aceitam ausência", () => {
  it("nulo e indefinido viram traço, não exceção", () => {
    expect(fmtPct(null)).toBe(TRACO);
    expect(fmtUsd(null)).toBe(TRACO);
    expect(fmtNum(null)).toBe(TRACO);
    expect(fmtPct(undefined)).toBe(TRACO);
  });

  it("NaN também — pode escapar de um cálculo no próprio front", () => {
    // O backend já não manda NaN, mas uma divisão por zero AQUI produziria um.
    // Guardar só contra null deixaria essa porta aberta.
    expect(fmtNum(NaN)).toBe(TRACO);
    expect(fmtPct(Infinity)).toBe(TRACO);
  });

  it("número bom continua formatado como antes", () => {
    expect(fmtPct(3.456)).toBe("+3.46%");
    expect(fmtPct(-2.1)).toBe("-2.10%");
    expect(fmtUsd(225.014)).toBe("$225.01");
    expect(fmtNum(1.666, 1)).toBe("1.7");
    expect(fmtNum(2.5, 2, "x")).toBe("2.50x");
  });

  it("ZERO não é tratado como ausência", () => {
    // `!0` é true em JS. Uma checagem por falsidade transformaria uma variação
    // de 0,00% num dia parado em "sem dado".
    expect(fmtPct(0)).toBe("+0.00%");
    expect(fmtNum(0)).toBe("0.00");
    expect(temNumero(0)).toBe(true);
  });
});

// ── as regras de leitura ────────────────────────────────────────────────────

function resumo(over: Record<string, unknown> = {}) {
  return {
    ticker: "NVDA",
    summary: {
      n_events: 8,
      gap_pct_mean: 1.08,
      gap_pct_abs_mean: 2.33,
      close_pct_mean: -2.94,
      close_pct_abs_mean: 3.75,
      close_pct_std: 3.74,
      intraday_range_pct_mean: 5.93,
      volume_ratio_mean: 1.66,
      suggested_threshold_pct: 7.5,
      current_price: 225.01,
      r1_price: 233.45,
      r2_price: 241.89,
      s1_price: 216.57,
      s2_price: 208.13,
      ...over,
    },
  } as Parameters<typeof interpretResult>[0];
}

describe("interpretação não inventa conclusão a partir de nulo", () => {
  it("com os números reais do NVDA, as frases saem normalmente", () => {
    const notas = interpretResult(resumo());
    expect(notas.length).toBeGreaterThan(2);
    expect(notas.join(" ")).toContain("Volatilidade histórica moderada");
  });

  it("threshold nulo NÃO vira 'volatilidade baixa'", () => {
    // A armadilha: `null >= 8` é false, `null >= 4` é false, e o código caía
    // no else anunciando "volatilidade histórica baixa" -- uma afirmação
    // confiante construída sobre dado ausente. Isso é pior que a tela preta:
    // a frase tem a mesma cara de uma conclusão medida.
    const notas = interpretResult(resumo({ suggested_threshold_pct: null })).join(" ");
    expect(notas).not.toContain("Volatilidade histórica baixa");
    expect(notas).toContain("sem dado suficiente");
  });

  it("média de fechamento nula NÃO vira 'sem viés direcional'", () => {
    // Mesma armadilha do outro lado: Math.abs(null) é 0, que é < 1, e a tela
    // afirmaria "reações historicamente equilibradas" sem ter medido nada.
    const notas = interpretResult(resumo({ close_pct_mean: null })).join(" ");
    expect(notas).not.toContain("equilibradas");
    expect(notas).toContain("sem dado suficiente");
  });

  it("comparação gap × fechamento fica em silêncio se faltar um lado", () => {
    // Aqui o silêncio é a leitura certa: a frase é sobre a RELAÇÃO entre os
    // dois números, e sem um deles não há relação a descrever.
    const notas = interpretResult(resumo({ gap_pct_abs_mean: null })).join(" ");
    expect(notas).not.toContain("tende a se ampliar");
    expect(notas).not.toContain("já na abertura");
  });

  it("resumo inteiro nulo não lança", () => {
    // O caso extremo: um ticker sem nenhum dado utilizável.
    const todosNulos = Object.fromEntries(
      Object.keys(resumo().summary!).map((k) => [k, k === "n_events" ? 0 : null]),
    );
    expect(() => interpretResult(resumo(todosNulos))).not.toThrow();
  });

  it("sem summary devolve lista vazia", () => {
    expect(interpretResult({ ticker: "NVDA" })).toEqual([]);
  });
});
