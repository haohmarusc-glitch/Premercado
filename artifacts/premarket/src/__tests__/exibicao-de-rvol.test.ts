/**
 * O RVOL não conclusivo não pode aparecer como número em destaque.
 *
 * O que a tela fazia: `5,81` grande, e "ainda não conclusivo (início do
 * pregão)" em letra miúda embaixo. Quem bate o olho lê o número — foi assim que
 * o 5,81 do NBIS (sete minutos de pregão, 17/08/2026) virou "volume muito acima
 * do normal" na análise com IA.
 */
import { describe, it, expect } from "vitest";
import { exibicaoDeRvol, rotuloRvol } from "@/lib/indicators";

describe("exibicaoDeRvol", () => {
  it("na abertura, o destaque diz indisponível e o número desce para a nota", () => {
    const e = exibicaoDeRvol(5.81, "indefinido_abertura");
    expect(e.valor).toBe("indisponível");
    // O número continua visível: esconder dado é o outro erro.
    expect(e.nota).toContain("5.81x");
    expect(e.nota).toContain("menos de 30 min");
  });

  it("na abertura sem valor nenhum, não inventa número", () => {
    expect(exibicaoDeRvol(null, "indefinido_abertura").nota).toContain("sem valor");
  });

  it("ausência de RVOL é travessão, nunca 0", () => {
    // `0` afirma volume nenhum. O que se sabe é que não há barra para medir.
    for (const caso of [
      exibicaoDeRvol(null, null),
      exibicaoDeRvol(null, "indisponivel"),
      exibicaoDeRvol(undefined, undefined),
    ]) {
      expect(caso.valor).toBe("—");
      expect(caso.valor).not.toBe("0");
      expect(caso.nota).toContain("sem barra do pregão");
    }
  });

  it("com o pregão andado, é o número e o sinal", () => {
    expect(exibicaoDeRvol(1.35, "alto")).toEqual({ valor: "1.35", nota: "alto" });
    expect(exibicaoDeRvol(0.84, "normal")).toEqual({ valor: "0.84", nota: "normal" });
  });
});

describe("rotuloRvol", () => {
  it("cobre o sinal novo sem vazar o valor cru", () => {
    expect(rotuloRvol("indisponivel")).toBe("sem dado do pregão");
    expect(rotuloRvol("indefinido_abertura")).toContain("não conclusivo");
    expect(rotuloRvol(null)).toBe("—");
  });
});
