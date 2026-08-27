/**
 * O rótulo "ESTICADO — historicamente é o estado que precede reações
 * negativas" (ou o espelho "DESCONTADO ... surpresa positiva") era um texto
 * FIXO, independente do que o histórico daquele MESMO papel mostrava duas
 * frases acima.
 *
 * Auditoria de 27/08/2026: o card do NVDA dizia "em 1 de 1 balanços
 * esticados a reação foi de ALTA (média +3,25%)" e, logo em seguida,
 * "ESTICADO — historicamente é o estado que precede reações NEGATIVAS" --
 * contradição dentro do mesmo card. SMCI tinha o mesmo problema (3 de 4
 * esticados subiram, média +9,38%, rótulo continuava dizendo "negativas").
 *
 * Rodar: pnpm --filter @workspace/premarket run test -- --run earnings-reaction-rotulo-estado-atual
 */
import { describe, it, expect } from "vitest";
import { rotuloEstadoAtual } from "@/pages/earnings-reaction";

describe("rótulo do estado atual reflete o histórico do próprio papel", () => {
  it("NVDA real: maioria esticada SUBIU -- não pode afirmar 'precede negativas'", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "esticado",
      esticado_n: 1,
      esticado_caiu_n: 0,
      esticado_reacao_media: 3.25,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).toContain("ESTICADO");
    expect(rotulo).not.toContain("precede reações negativas");
    expect(rotulo).toContain("direção oposta");
    expect(rotulo).toContain("1 de 1");
  });

  it("SMCI real: 3 de 4 esticados subiram -- mesma contradição, mesmo fix", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "esticado",
      esticado_n: 4,
      esticado_caiu_n: 1,
      esticado_reacao_media: 9.38,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).not.toContain("precede reações negativas");
    expect(rotulo).toContain("direção oposta");
    expect(rotulo).toContain("3 de 4");
  });

  it("AVGO real: 3 de 3 esticados caíram -- hipótese SE confirma, mantém a frase original", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "esticado",
      esticado_n: 3,
      esticado_caiu_n: 3,
      esticado_reacao_media: -9.67,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).toContain("precede reações negativas");
    expect(rotulo).toContain("3 de 3");
  });

  it("SKHY real: sem eventos esticados no histórico (n=0) -- não afirma nada não sustentável", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "esticado",
      esticado_n: 0,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).toContain("sem eventos esticados");
    expect(rotulo).not.toContain("precede reações negativas");
  });

  it("descontado espelha a mesma regra: maioria a favor mantém a hipótese original", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "descontado",
      descontado_n: 1,
      descontado_subiu_n: 1,
      descontado_reacao_media: 8.64,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).toContain("DESCONTADO");
    expect(rotulo).toContain("surpresa positiva");
    expect(rotulo).toContain("1 de 1");
  });

  it("descontado: maioria caiu -- não pode afirmar 'espaço pra surpresa positiva'", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "descontado",
      descontado_n: 3,
      descontado_subiu_n: 1,
      descontado_reacao_media: -4.2,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).not.toContain("surpresa positiva");
    expect(rotulo).toContain("direção oposta");
    expect(rotulo).toContain("2 de 3");
  });

  it("neutro não afirma nada", () => {
    expect(rotuloEstadoAtual({ estado_atual: "neutro" } as Parameters<typeof rotuloEstadoAtual>[0]))
      .toBe("neutro");
  });

  it("empate mantém a leitura conservadora (hipótese original)", () => {
    const rotulo = rotuloEstadoAtual({
      estado_atual: "esticado",
      esticado_n: 2,
      esticado_caiu_n: 1,
      esticado_reacao_media: 0,
    } as Parameters<typeof rotuloEstadoAtual>[0]);
    expect(rotulo).toContain("precede reações negativas");
  });
});
