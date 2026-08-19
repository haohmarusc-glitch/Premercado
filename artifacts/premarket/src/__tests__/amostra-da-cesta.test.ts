/**
 * A legenda tem de dizer a amostra REAL, não a pedida.
 *
 * Em 19/08/2026 a tela rodou com WOLF, que tem 4 earnings no histórico, e a
 * legenda dizia "~8 earnings por ticker" -- porque usava o `lookback` do
 * formulário. O texto do modelo declarava 4 corretamente, e a tela o
 * contradizia logo acima.
 *
 * Papel novo, recém-listado ou com histórico curto entrega menos do que se
 * pede, e a força da leitura depende justamente desse número.
 */
import { describe, it, expect } from "vitest";
import { amostraDaCesta } from "../pages/earnings-reaction";

const comN = (ticker: string, n: number) =>
  ({ ticker, summary: { n_events: n } }) as never;

describe("amostraDaCesta", () => {
  it("usa o n_events real quando todos têm o mesmo", () => {
    expect(amostraDaCesta([comN("NVDA", 8), comN("AMD", 8)])).toBe(
      "Amostra de 8 earnings por ticker",
    );
  });

  it("mostra a FAIXA quando os papéis divergem", () => {
    // O caso que importa: uma cesta com 8 e 4 não tem "amostra de 8", e
    // arredondar para o maior venderia confiança que não existe.
    expect(amostraDaCesta([comN("NVDA", 8), comN("WOLF", 4)])).toBe(
      "Amostra de 4 a 8 earnings por ticker",
    );
  });

  it("ignora ticker sem estatística em vez de contá-lo como zero", () => {
    const semDados = { ticker: "XYZ", error: "sem histórico" } as never;
    expect(amostraDaCesta([comN("NVDA", 8), semDados])).toBe(
      "Amostra de 8 earnings por ticker",
    );
  });

  it("diz que não sabe em vez de inventar número", () => {
    expect(amostraDaCesta([])).toBe("Amostra por ticker indisponível");
    expect(amostraDaCesta(null)).toBe("Amostra por ticker indisponível");
  });
});
