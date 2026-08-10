import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";
import {
  decideProvider,
  fallbackOrderExcluding,
  PROVIDER_FALLBACK_ORDER,
} from "../agent-budget";

const runs = (
  ...linhas: [provedores: string | null, custo: number | string | null][]
) => linhas.map(([llmProvider, costUsd]) => ({ llmProvider, costUsd }));

describe("decideProvider — sem teto", () => {
  it("não mexe em nada quando dailyBudgetUsd é null", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: null,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", 99]),
    });
    expect(d.exceeded).toBe(false);
    expect(d.provider).toBeUndefined();
    expect(d.order).toBeUndefined();
  });

  it("preserva o provedor manual do usuário quando não há teto", () => {
    const d = decideProvider({
      agentProvider: "openai",
      dailyBudgetUsd: null,
      cheapProvider: "gemini",
      runsToday: [],
    });
    expect(d.provider).toBe("openai");
  });
});

describe("decideProvider — dentro do teto", () => {
  it("segue no primário enquanto o gasto está abaixo", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", 0.9], ["anthropic", 0.5]),
    });
    expect(d.spentToday).toBeCloseTo(1.4);
    expect(d.exceeded).toBe(false);
    expect(d.order).toBeUndefined();
  });

  it("ignora gasto de provedores que não são o primário", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["gemini", 5], ["openrouter", 5]),
    });
    expect(d.spentToday).toBe(0);
    expect(d.exceeded).toBe(false);
  });

  it("conta a run que TROCOU de provedor no meio (llmProvider é csv)", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["gemini,anthropic", 2.5]),
    });
    expect(d.spentToday).toBeCloseTo(2.5);
    expect(d.exceeded).toBe(true);
  });

  it("aceita numeric vindo como string do driver pg", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: "2.00",
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", "1.2000"], ["anthropic", "0.9000"]),
    });
    expect(d.spentToday).toBeCloseTo(2.1);
    expect(d.exceeded).toBe(true);
  });
});

describe("decideProvider — teto estourado", () => {
  it("rebaixa para o provedor barato", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", 3.29]),
    });
    expect(d.exceeded).toBe(true);
    expect(d.provider).toBe("gemini");
  });

  /**
   * O teste que existe por causa do bug real: o teto era decorativo porque o
   * provedor estourado continuava na cadeia, uma posição atrás. Com o modelo
   * "full" do gemini respondendo 404, a run voltava pro anthropic e o gasto
   * seguia subindo (US$ 3,29 contra teto de US$ 2,00 em produção).
   */
  it("REMOVE o provedor estourado da cadeia inteira, não só da primeira posição", () => {
    const d = decideProvider({
      agentProvider: null, // primário = anthropic
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", 3.29]),
    });
    const ordem = (d.order ?? "").split(",");
    expect(ordem[0]).toBe("gemini");
    expect(ordem).not.toContain("anthropic");
    expect(ordem).toEqual(["gemini", "deepseek", "openrouter", "openai", "kimi"]);
  });

  it("remove o primário mesmo quando ele não é o anthropic", () => {
    const d = decideProvider({
      agentProvider: "openai",
      dailyBudgetUsd: 1,
      cheapProvider: "openrouter",
      runsToday: runs(["openai", 1]),
    });
    const ordem = (d.order ?? "").split(",");
    expect(ordem).not.toContain("openai");
    expect(ordem[0]).toBe("openrouter");
  });

  it("estoura exatamente NO teto, não só acima dele", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", 2]),
    });
    expect(d.exceeded).toBe(true);
  });

  it("sinaliza rebaixamento inútil quando o barato É o primário", () => {
    const d = decideProvider({
      agentProvider: "gemini",
      dailyBudgetUsd: 1,
      cheapProvider: "gemini",
      runsToday: runs(["gemini", 2]),
    });
    expect(d.exceeded).toBe(true);
    expect(d.downgradeIneffective).toBe(true);
    // Não adianta sobrescrever a ordem: não há pra onde rebaixar.
    expect(d.order).toBeUndefined();
  });
});

describe("decideProvider — furo do custo desconhecido", () => {
  it("conta as runs sem preço em vez de deixá-las invisíveis", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["anthropic", null], ["anthropic", null], ["anthropic", 0.5]),
    });
    // Elas somam zero -- é o comportamento, e por isso precisa ser reportado.
    expect(d.spentToday).toBeCloseTo(0.5);
    expect(d.unpricedRuns).toBe(2);
  });

  it("não conta como sem-preço a run de outro provedor", () => {
    const d = decideProvider({
      agentProvider: null,
      dailyBudgetUsd: 2,
      cheapProvider: "gemini",
      runsToday: runs(["gemini", null]),
    });
    expect(d.unpricedRuns).toBe(0);
  });
});

describe("fallbackOrderExcluding", () => {
  it("não duplica quando barato e primário coincidem com a lista padrão", () => {
    const ordem = fallbackOrderExcluding("gemini", "anthropic");
    expect(new Set(ordem).size).toBe(ordem.length);
  });
});

/**
 * A ordem montada aqui é consumida por `_DEFAULT_ORDER` em provider.py. Se as
 * duas listas divergirem, um provedor some (ou entra) da cadeia de fallback
 * sem ninguém notar -- e é justamente a cadeia de fallback que fura o teto.
 */
describe("sincronia com provider.py", () => {
  it("PROVIDER_FALLBACK_ORDER bate com _DEFAULT_ORDER do provider.py", () => {
    const src = readFileSync(
      path.resolve(__dirname, "../../agent/provider.py"),
      "utf-8",
    );
    const m = src.match(/^_DEFAULT_ORDER\s*=\s*\[(.*?)\]/ms);
    expect(m, "_DEFAULT_ORDER não encontrado em provider.py").toBeTruthy();
    const doPython = [...m![1].matchAll(/["']([^"']+)["']/g)].map((x) => x[1]);
    expect(doPython).toEqual(PROVIDER_FALLBACK_ORDER);
  });
});
