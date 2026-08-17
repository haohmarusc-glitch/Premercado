import { describe, it, expect } from "vitest";
import { linhaDeGasto, type UsoLlm } from "../ai-spend-record";

const AGORA = new Date("2026-08-16T23:40:00Z");

const USO: UsoLlm = {
  calls: 1,
  input_tokens: 4200,
  output_tokens: 900,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  total_cost_usd: 0.0177,
  providers: [{ provider: "anthropic", model: "claude-sonnet-5" }],
};

describe("linhaDeGasto", () => {
  it("mapeia tokens, custo e provedor da chamada", () => {
    const l = linhaDeGasto("analise_rapida_ia:INTC", USO, 5_000, null, AGORA)!;
    expect(l.mode).toBe("analise_rapida_ia:INTC");
    expect(l.status).toBe("success");
    expect(l.inputTokens).toBe(4200);
    expect(l.outputTokens).toBe(900);
    expect(l.costUsd).toBe(0.0177);
    expect(l.llmProvider).toBe("anthropic");
    expect(l.llmModel).toBe("claude-sonnet-5");
    expect(l.durationMs).toBe(5_000);
    expect(l.startedAt.toISOString()).toBe("2026-08-16T23:39:55.000Z");
    expect(l.finishedAt).toBe(AGORA);
  });

  it("custo desconhecido continua null, nunca zero", () => {
    // Modelo fora de MODEL_PRICING devolve total_cost_usd null. Zerar aqui
    // transformaria gasto real em gasto invisível na tela de Gastos com IA.
    const l = linhaDeGasto("x", { ...USO, total_cost_usd: null }, 1_000, null, AGORA)!;
    expect(l.costUsd).toBeNull();
  });

  it("clique que não chegou ao provedor não vira linha", () => {
    expect(linhaDeGasto("x", undefined, 10, null, AGORA)).toBeNull();
    expect(linhaDeGasto("x", { calls: 0 }, 10, null, AGORA)).toBeNull();
  });

  it("falha depois de cobrar ainda registra, marcada como failed", () => {
    const l = linhaDeGasto("x", USO, 2_000, "resposta curta demais", AGORA)!;
    expect(l.status).toBe("failed");
    expect(l.errorMessage).toBe("resposta curta demais");
    expect(l.costUsd).toBe(0.0177);
  });

  it("erro sem uso nenhum também registra (timeout antes da resposta)", () => {
    const l = linhaDeGasto("x", undefined, 90_000, "timeout", AGORA)!;
    expect(l.status).toBe("failed");
    expect(l.inputTokens).toBeNull();
    expect(l.costUsd).toBeNull();
  });

  it("duração negativa é normalizada", () => {
    const l = linhaDeGasto("x", USO, -5, null, AGORA)!;
    expect(l.durationMs).toBe(0);
    expect(l.startedAt).toEqual(AGORA);
  });

  it("uso sem bloco de provedores não quebra", () => {
    const l = linhaDeGasto("x", { calls: 1, total_cost_usd: 0.01 }, 100, null, AGORA)!;
    expect(l.llmProvider).toBeNull();
    expect(l.llmModel).toBeNull();
  });
});
