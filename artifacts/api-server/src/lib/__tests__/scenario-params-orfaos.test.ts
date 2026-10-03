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

let posicoes: { id: number; ticker: string; isEtf: boolean; quantity: string }[] = [];
// Lotes por posição. Vazio = nenhum lote registrado, e aí posicaoAtivaPelosLotes
// cai de volta pro `quantity` armazenado -- que é o que os casos deste arquivo
// exercitam (posições importadas por script, sem lote).
let lotes: { positionId: number; saleDate: string | null; salePrice: string | null }[] = [];
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
  inArray: () => "inArray",
  // Devolve os tickers para o teste inspecionar o critério do DELETE.
  notInArray: (_col: unknown, valores: string[]) => ({ tipo: "notInArray", valores }),
}));

vi.mock("@workspace/db", () => ({
  db: {
    // O checker faz dois selects: as posições (sem .where) e, se houver
    // posição, os lotes (com .where). O retorno é um thenable que também
    // responde a .where, pra servir aos dois sem dois mocks.
    select: () => ({
      from: (t: unknown) => {
        const deLotes = !!(t && typeof t === "object" && "positionId" in (t as object));
        const dados: unknown[] = deLotes ? lotes : posicoes;
        const p = Promise.resolve(dados) as Promise<unknown[]> & { where: (w?: unknown) => Promise<unknown[]> };
        p.where = async () => dados;
        return p;
      },
    }),
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
  portfolioPositionsTable: { id: "id", ticker: "ticker", isEtf: "isEtf", quantity: "quantity" },
  portfolioPurchasesTable: { positionId: "positionId", saleDate: "saleDate", salePrice: "salePrice" },
  scenarioParamsTable: { ticker: "ticker" },
  scenarioAlertSettingsTable: {},
  sectorMomentumTable: { benchmark: "benchmark" },
}));

vi.mock("@workspace/scenario-math", () => ({ diasAteAlvo: () => 30 }));
vi.mock("../portfolio-math", () => ({
  posicaoAtivaPelosLotes: (q: string, ls: { saleDate: string | null; salePrice: string | null }[]) =>
    ls.length ? ls.some((l) => !(l.saleDate && l.salePrice)) : Number(q) > 0,
}));
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
  lotes = [];
  deleteWhere = null;
  deleteChamado = 0;
  linhasRemovidas = [];
});

describe("refreshScenarioParams — limpeza de órfãos", () => {
  it("remove a linha do ticker que saiu da lista ativa", async () => {
    // NVDA ativa, AVGO zerada: o caso real da auditoria.
    posicoes = [
      { id: 1, ticker: "NVDA", isEtf: false, quantity: "10" },
      { id: 2, ticker: "AVGO", isEtf: false, quantity: "0" },
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
    posicoes = [{ id: 1, ticker: "NVDA", isEtf: false, quantity: "10" }];
    linhasRemovidas = [];

    await refreshScenarioParams();

    expect(deleteChamado).toBe(1);
    expect(infos.find((i) => i.msg.includes("linhas removidas"))).toBeUndefined();
  });

  it("NÃO apaga nada quando não há posição ativa nenhuma", async () => {
    // A borda perigosa: notInArray(ticker, []) apagaria a tabela inteira.
    posicoes = [{ id: 2, ticker: "AVGO", isEtf: false, quantity: "0" }];

    await refreshScenarioParams();

    expect(deleteChamado).toBe(0);
  });

  it("ETF não entra na lista ativa e por isso não protege sua própria linha", async () => {
    // Documenta o comportamento: o checker só cuida de posições não-ETF, então
    // uma linha de ETF em scenario_params seria removida como órfã. Hoje nada
    // grava ETF ali — se passar a gravar, este teste avisa.
    posicoes = [
      { id: 1, ticker: "NVDA", isEtf: false, quantity: "10" },
      { id: 3, ticker: "SMH", isEtf: true, quantity: "5" },
    ];

    await refreshScenarioParams();

    expect(deleteWhere).toEqual({ tipo: "notInArray", valores: ["NVDA"] });
  });

  it("posição com quantity desatualizado mas todos os lotes vendidos sai da lista", async () => {
    // O defeito que este checker tinha: era o quinto consumidor de
    // portfolio_positions a decidir "ativo" pelo campo editável. Com
    // `quantity` travado num valor antigo (PUT /portfolio/:id edita direto,
    // sem recalcular), o ticker entrava no refresh E escapava da limpeza de
    // órfãos -- porque as duas coisas usam a mesma lista.
    posicoes = [
      { id: 1, ticker: "NVDA", isEtf: false, quantity: "10" },
      { id: 2, ticker: "AVGO", isEtf: false, quantity: "7.5" },
    ];
    lotes = [{ positionId: 2, saleDate: "2026-08-05", salePrice: "420.10" }];
    linhasRemovidas = [{ ticker: "AVGO" }];

    await refreshScenarioParams();

    // AVGO fora da lista ativa apesar de `quantity: "7.5"`.
    expect(deleteWhere).toEqual({ tipo: "notInArray", valores: ["NVDA"] });
  });

  it("lote com data de venda e preço nulo continua ABERTO", async () => {
    // A metade-vendida: `saleDate == null` sozinho diria fechado. O critério
    // é data E preço, igual ao resto do repo.
    posicoes = [{ id: 2, ticker: "AVGO", isEtf: false, quantity: "7.5" }];
    lotes = [{ positionId: 2, saleDate: "2026-08-05", salePrice: null }];

    await refreshScenarioParams();

    expect(deleteWhere).toEqual({ tipo: "notInArray", valores: ["AVGO"] });
  });
});
