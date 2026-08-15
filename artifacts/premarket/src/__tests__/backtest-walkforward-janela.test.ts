import { describe, it, expect } from "vitest";
import { pregoesAproximados } from "@/pages/backtest";

describe("pregoesAproximados", () => {
  it("aproxima um ano civil em ~252 pregões", () => {
    // O caso que travou o walk-forward na tela: 2025-08-15 → 2026-08-15
    // devolveu 251 pregões reais, um a menos que a janela de treino padrão.
    const n = pregoesAproximados("2025-08-15", "2026-08-15");
    expect(n).not.toBeNull();
    expect(n!).toBeGreaterThanOrEqual(248);
    expect(n!).toBeLessThanOrEqual(254);
  });

  it("escala com o tamanho do período", () => {
    const umAno = pregoesAproximados("2024-01-01", "2025-01-01")!;
    const doisAnos = pregoesAproximados("2023-01-01", "2025-01-01")!;
    expect(doisAnos).toBeGreaterThan(umAno * 1.9);
  });

  it("devolve null quando o fim não é depois do início", () => {
    expect(pregoesAproximados("2026-08-15", "2026-08-15")).toBeNull();
    expect(pregoesAproximados("2026-08-15", "2025-08-15")).toBeNull();
  });

  it("devolve null para data inválida", () => {
    expect(pregoesAproximados("", "2026-08-15")).toBeNull();
    expect(pregoesAproximados("2025-08-15", "nao-e-data")).toBeNull();
  });

  it("três anos cobrem treino + teste padrão com folga", () => {
    // 252 + 63 = 315 pregões é o mínimo do modo padrão.
    expect(pregoesAproximados("2023-08-15", "2026-08-15")!).toBeGreaterThan(315);
  });
});
