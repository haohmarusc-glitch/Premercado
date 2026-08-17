import { describe, it, expect } from "vitest";
import { benchmarkSugerido, temSugestaoConhecida, BENCHMARK_PADRAO } from "@/lib/benchmark-setor";

describe("benchmarkSugerido", () => {
  it("mapeia os setores da carteira", () => {
    expect(benchmarkSugerido("NVDA")).toBe("SMH");
    expect(benchmarkSugerido("INTC")).toBe("SMH");
    expect(benchmarkSugerido("MU")).toBe("SMH");
    expect(benchmarkSugerido("BIDU")).toBe("KWEB");
    expect(benchmarkSugerido("PDD")).toBe("KWEB");
    expect(benchmarkSugerido("TOL")).toBe("ITB");
    expect(benchmarkSugerido("EL")).toBe("XLP");
  });

  it("classifica pelo que move o papel no dia, não pelo setor formal", () => {
    // CEG/VST são tese de IA, mas quem dita o dia delas é o setor elétrico.
    expect(benchmarkSugerido("CEG")).toBe("XLU");
    expect(benchmarkSugerido("VST")).toBe("XLU");
    // GOOGL/META são "tech" no senso comum, mas andam com comunicação.
    expect(benchmarkSugerido("GOOGL")).toBe("XLC");
    expect(benchmarkSugerido("META")).toBe("XLC");
    // AMZN é consumo discricionário, não XLK.
    expect(benchmarkSugerido("AMZN")).toBe("XLY");
  });

  it("ETF e índice viram SPY — beta contra si mesmo seria 1,00 e não diria nada", () => {
    expect(benchmarkSugerido("SMH")).toBe("SPY");
    expect(benchmarkSugerido("KWEB")).toBe("SPY");
    expect(benchmarkSugerido("EWY")).toBe("SPY");
    expect(benchmarkSugerido("^GSPC")).toBe("SPY");
  });

  it("normaliza caixa e espaço", () => {
    expect(benchmarkSugerido("  nvda  ")).toBe("SMH");
    expect(benchmarkSugerido("bidu")).toBe("KWEB");
  });

  it("ticker desconhecido cai no padrão do sistema", () => {
    expect(benchmarkSugerido("XYZQ")).toBe(BENCHMARK_PADRAO);
    expect(benchmarkSugerido("")).toBe(BENCHMARK_PADRAO);
  });
});

describe("temSugestaoConhecida", () => {
  it("separa 'eu conheço este ticker' de 'usei o fallback'", () => {
    // A tela só diz "sugerido para X" quando de fato conhece o papel —
    // não pode fingir certeza sobre um ticker que caiu no padrão.
    expect(temSugestaoConhecida("NVDA")).toBe(true);
    expect(temSugestaoConhecida("SPY")).toBe(true);
    expect(temSugestaoConhecida("XYZQ")).toBe(false);
    expect(temSugestaoConhecida("")).toBe(false);
  });
});
