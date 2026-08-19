/**
 * Limpeza de órfãos em scenario_params.
 *
 * Auditoria 17/08/2026: AVGO tinha posição zerada mas a linha de
 * scenario_params continuava lá, e o Painel de Cenários seguia carregando
 * vol/beta de um papel que o usuário não tem mais. A linha precisou ser
 * apagada à mão no VPS.
 *
 * O upsert do checker só ESCREVE — nada removia. Como o dado é derivado (o
 * próprio ciclo recria a linha se a posição voltar), DELETE é seguro; marcar
 * stale só adiaria a mesma decisão.
 *
 * A borda que o teste fixa junto: com ZERO tickers ativos a limpeza NÃO roda.
 * `notInArray(ticker, [])` apagaria a tabela inteira, e "carteira vazia" é
 * mais provável de ser anomalia momentânea que estado real.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const infos: { campos: Record<string, unknown>; msg: string }[] = [];

let posicoes: { ticker: string; isEtf: boolean; quantity: string }[] = [];
let deleteWhere: unknown = null;
let deleteChamado = 0;
let linhasRemovidas: { ticker: string }[] = [];

vi.mock("../logger", () => ({
  logger: {
    info: (campos: Record<string, unknown>, msg: string) => infos.push({ campos, msg }),
    warn: () => {},
    error: () => {},
  },
}));

vi.mock("drizzle-orm", () => ({
  eq: () => "eq",
  // Devolve os tickers para o teste inspecionar o critério do DELETE.
  notInArray: (_col: unknown, valores: string[]) => ({ tipo: "notInArray", valores }),
}));

vi.mock("@workspace/db", () => ({
  db: {
    select: () => ({ from: async () => posicoes }),
    insert: () => ({ values: () => ({ onConflictDoUpdate: async () => {} }) }),
    update: () => ({ set: () => ({ where: async () => {} }) }),
    delete: () => {
      deleteChamado++;
      return {
        where: (w: unknown) => {
          deleteWhere = w;
          return { returning: async () => linhasRemovidas };
        },
      };
    },
  },
  portfolioPositionsTable: { ticker: "ticker", isEtf: "isEtf", quantity: "quantity" },
  scenarioParamsTable: { ticker: "ticker" },
  scenarioAlertSettingsTable: {},
  sectorMomentumTable: { benchmark: "benchmark" },
}));

vi.mock("@workspace/scenario-math", () => ({ diasAteAlvo: () => 30 }));
vi.mock("../portfolio-math", () => ({ isActivePosition: (q: string) => Number(q) > 0 }));
vi.mock("../runner", () => ({ state: { running: false } }));

// O script devolve params para todo ticker pedido; o foco aqui é o DELETE.
vi.mock("../../routes/scenarios", () => ({
  runScript: async (_s: string, args: string[]) =>
    JSON.stringify({
      params: Object.fromEntries(
        args[0].split(",").map((t) => [t, { volAnnual: 0.4, betaSector: 1.1 }]),
      ),
    }),
}));

const { refreshScenarioParams } = await import("../scenario-params-checker");

beforeEach(() => {
  infos.length = 0;
  posicoes = [];
  deleteWhere = null;
  deleteChamado = 0;
  linhasRemovidas = [];
});

describe("refreshScenarioParams — limpeza de órfãos", () => {
  it("remove a linha do ticker que saiu da lista ativa", async () => {
    // NVDA ativa, AVGO zerada: o caso real da auditoria.
    posicoes = [
      { ticker: "NVDA", isEtf: false, quantity: "10" },
      { ticker: "AVGO", isEtf: false, quantity: "0" },
    ];
    linhasRemovidas = [{ ticker: "AVGO" }];

    await refreshScenarioParams();

    expect(deleteChamado).toBe(1);
    // O critério é "tudo que NÃO está na lista ativa" — AVGO não aparece nele.
    expect(deleteWhere).toEqual({ tipo: "notInArray", valores: ["NVDA"] });

    const log = infos.find((i) => i.msg.includes("linhas removidas"));
    expect(log?.campos["removidos"]).toEqual(["AVGO"]);
  });

  it("não loga nada quando não há órfão", async () => {
    posicoes = [{ ticker: "NVDA", isEtf: false, quantity: "10" }];
    linhasRemovidas = [];

    await refreshScenarioParams();

    expect(deleteChamado).toBe(1);
    expect(infos.find((i) => i.msg.includes("linhas removidas"))).toBeUndefined();
  });

  it("NÃO apaga nada quando não há posição ativa nenhuma", async () => {
    // A borda perigosa: notInArray(ticker, []) apagaria a tabela inteira.
    posicoes = [{ ticker: "AVGO", isEtf: false, quantity: "0" }];

    await refreshScenarioParams();

    expect(deleteChamado).toBe(0);
  });

  it("ETF não entra na lista ativa e por isso não protege sua própria linha", async () => {
    // Documenta o comportamento: o checker só cuida de posições não-ETF, então
    // uma linha de ETF em scenario_params seria removida como órfã. Hoje nada
    // grava ETF ali — se passar a gravar, este teste avisa.
    posicoes = [
      { ticker: "NVDA", isEtf: false, quantity: "10" },
      { ticker: "SMH", isEtf: true, quantity: "5" },
    ];

    await refreshScenarioParams();

    expect(deleteWhere).toEqual({ tipo: "notInArray", valores: ["NVDA"] });
  });
});
