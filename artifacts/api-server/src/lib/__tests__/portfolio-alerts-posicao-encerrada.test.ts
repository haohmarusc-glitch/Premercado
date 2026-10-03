/**
 * O checker de carteira contra os dados que o quebraram.
 *
 * Varredura de 03/10/2026 em `portfolio_alert_firings`: dos 204 disparos
 * gravados, 98 eram comprovadamente falsos. Nenhum vinha da lógica de preço --
 * vinham de ler `portfolio_positions` sem olhar os lotes. Este arquivo fixa os
 * três defeitos, cada cenário montado com os números reais de produção.
 *
 * Os quatro casos foram rodados contra o código SEM a correção antes de a
 * correção existir. O resultado:
 *
 *   ✗ BABA: 6 alertas de ganho (10/15/20/30/40/50) numa posição vendida com
 *     PREJUÍZO de 15,95%, todos com changePct = Infinity
 *   ✗ holding:META:180 disparando 132 dias depois da venda
 *   ✗ ARM #22 (viva, -7,93%) sem poder disparar nada
 *   ✗ o segundo lote de SMCI da mesma data sem marco de holding próprio
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

interface Pos {
  id: number; ticker: string; avgCost: string; quantity: string;
  upAlertPcts: number[]; downAlertPcts: number[]; notifyEmail: string;
}
interface Lot {
  id: number; positionId: number; purchaseDate: string; amount: string;
  purchasePrice: string | null; saleDate: string | null; salePrice: string | null;
}

let posicoes: Pos[] = [];
let lotes: Lot[] = [];
let chavesGravadas: string[] = [];
let quotes: { symbol: string; price: number | null; error: string | null }[] = [];
/** Os tickers que cada chamada de get_quotes recebeu, para o caso da dedupe. */
const tickersPedidos: string[][] = [];

const ganhos: { symbol: string; pct: number; thr: number | null }[] = [];
const perdas: { symbol: string; pct: number; thr: number | null }[] = [];
const holdings: { ticker: string; purchaseDate: string; milestone: number }[] = [];
const recompras: { ticker: string }[] = [];

vi.mock("../logger", () => ({
  logger: { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}));

// `sql` só é usada como template tag em persistKey; o valor não importa aqui.
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ...vals: unknown[]) => {
    chavesGravadas.push(String(vals[0]));
    return { strings, vals };
  },
}));

vi.mock("@workspace/db", () => ({
  db: {
    select: () => ({
      from: (t: unknown) => {
        const nome = t && typeof t === "object" ? Object.keys(t as object)[0] : "";
        if (nome === "alertKey") return Promise.resolve(chavesGravadas.map((alertKey) => ({ alertKey })));
        if (nome === "positionId") return Promise.resolve(lotes);
        return Promise.resolve(posicoes);
      },
    }),
    execute: async () => {},
  },
  portfolioPositionsTable: { id: "id", ticker: "ticker" },
  portfolioPurchasesTable: { positionId: "positionId", id: "id" },
  portfolioAlertFiringsTable: { alertKey: "alertKey" },
}));

vi.mock("../mailer", () => ({
  sendAlertEmail: async (o: { symbol: string; condition: string; currentChangePct: number; thresholdPct: number | null }) => {
    (o.condition === "above" ? ganhos : perdas).push({ symbol: o.symbol, pct: o.currentChangePct, thr: o.thresholdPct });
  },
  sendPortfolioHoldingEmail: async (o: { ticker: string; purchaseDate: string; milestone: number }) => {
    holdings.push({ ticker: o.ticker, purchaseDate: o.purchaseDate, milestone: o.milestone });
  },
  sendRecompraEmail: async (o: { ticker: string }) => { recompras.push({ ticker: o.ticker }); },
}));

// O subprocesso é falso, mas o caminho até ele é o real: runExclusive chama a
// função, que spawna e lê o stdout. É assim que o teste consegue observar QUAIS
// tickers foram pedidos -- o caso da dedupe depende disso.
vi.mock("../python-queue", () => ({
  runExclusive: async (_nome: string, fn: () => Promise<unknown>) => fn(),
}));
vi.mock("../python-spawn", () => ({
  spawnPython: (_bin: string, args: string[]) => {
    tickersPedidos.push(args.slice(2));
    return {
      stdout: { on: (_e: string, cb: (b: Buffer) => void) => cb(Buffer.from(JSON.stringify(quotes))) },
      stderr: { on: () => {} },
      on: (e: string, cb: (c: number) => void) => { if (e === "close") setTimeout(() => cb(0), 0); },
      kill: () => {},
    };
  },
}));
vi.mock("../runner", () => ({ state: { running: false }, agentDir: "/tmp", getPythonBin: () => "python3" }));

const { checkPortfolioAlerts } = await import("../portfolio-alerts");

const LIMIARES_CIMA = [10, 15, 20, 30, 40, 50];
const LIMIARES_BAIXO = [10, 15, 20, 30];

function pos(id: number, ticker: string, avgCost: string, quantity = "0.0000"): Pos {
  return {
    id, ticker, avgCost, quantity,
    upAlertPcts: LIMIARES_CIMA, downAlertPcts: LIMIARES_BAIXO,
    notifyEmail: "dono@exemplo.com",
  };
}
/** Data a N dias atrás, para o marco de holding não depender do calendário. */
function diasAtras(n: number): string {
  return new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);
}

beforeEach(() => {
  posicoes = []; lotes = []; chavesGravadas = []; quotes = [];
  tickersPedidos.length = 0;
  ganhos.length = 0; perdas.length = 0; holdings.length = 0; recompras.length = 0;
});

describe("posição encerrada com avg_cost zerado", () => {
  beforeEach(() => {
    // BABA em produção: comprada 20/08 a 125,97, vendida 02/10 a 105,88.
    // Prejuízo de 15,95%. `recomputePosition` zerou os três campos na venda.
    posicoes = [pos(20, "BABA", "0.0000")];
    lotes = [{
      id: 32, positionId: 20, purchaseDate: "2026-08-20", amount: "100.0000",
      purchasePrice: "125.9700", saleDate: "2026-10-02", salePrice: "105.8800",
    }];
    quotes = [{ symbol: "BABA", price: 105.88, error: null }];
  });

  it("não manda nenhum alerta de ganho", async () => {
    await checkPortfolioAlerts();
    // Sem a correção: 6 e-mails, de gain:BABA:10 a gain:BABA:50.
    expect(ganhos).toEqual([]);
  });

  it("não manda alerta de perda tampouco — a posição não existe mais", async () => {
    await checkPortfolioAlerts();
    expect(perdas).toEqual([]);
  });

  it("nenhum percentual enviado é Infinity ou NaN", async () => {
    await checkPortfolioAlerts();
    for (const e of [...ganhos, ...perdas]) {
      expect(Number.isFinite(e.pct)).toBe(true);
    }
  });

  it("a conta que produzia Infinity era esta", () => {
    // O que o código fazia, com os valores exatos do banco. Fica explícito
    // para ninguém "simplificar" a guarda de volta.
    const avgCost = "0.0000";
    const price = 105.88;
    expect(((price - Number(avgCost)) / Number(avgCost)) * 100).toBe(Infinity);
    expect(Infinity >= 50).toBe(true);
  });

  it("mas o alerta de RECOMPRA continua funcionando — ele quer posição encerrada", async () => {
    // Não é efeito colateral tolerado: é o motivo de o filtro de lote aberto
    // valer só para ganho/perda/holding. BABA a 105,88 contra venda a 105,88
    // não cruza limiar; com queda de 20% cruza.
    quotes = [{ symbol: "BABA", price: 84.70, error: null }];
    await checkPortfolioAlerts();
    expect(recompras.map((r) => r.ticker)).toEqual(["BABA"]);
  });
});

describe("mesmo ticker em duas posições: uma encerrada, uma viva", () => {
  beforeEach(() => {
    // ARM em produção, 03/10: #12 encerrada em 21/09, #22 viva desde 08/07
    // (comprada a 333,98, cotada a 307,49 = -7,93%).
    posicoes = [pos(12, "ARM", "0.0000"), pos(22, "ARM", "333.9800", "1.0480")];
    lotes = [
      { id: 20, positionId: 12, purchaseDate: "2026-07-09", amount: "350.0000", purchasePrice: "333.9800", saleDate: "2026-09-21", salePrice: "314.8300" },
      { id: 35, positionId: 22, purchaseDate: "2026-07-08", amount: "350.0000", purchasePrice: "333.9800", saleDate: null, salePrice: null },
    ];
    // As chaves v1 que a posição morta gravou e que travavam a viva.
    chavesGravadas = [
      ...LIMIARES_CIMA.map((t) => `gain:ARM:${t}`),
      ...LIMIARES_BAIXO.map((t) => `loss:ARM:${t}`),
    ];
  });

  it("a posição viva dispara mesmo com todas as chaves antigas gravadas", async () => {
    // ARM #22 a -12%: cruza loss 10. Sem o `v2:<positionId>`, `loss:ARM:10`
    // já existia desde 09/06/2026 e esta perda nunca seria avisada.
    quotes = [{ symbol: "ARM", price: 293.9, error: null }];
    await checkPortfolioAlerts();
    expect(perdas.map((p) => p.thr)).toEqual([-10]);
    expect(perdas[0].pct).toBeCloseTo(-12.0, 1);
  });

  it("a posição encerrada não dispara nada junto", async () => {
    quotes = [{ symbol: "ARM", price: 293.9, error: null }];
    await checkPortfolioAlerts();
    expect(ganhos).toEqual([]);
    // Uma perda só: a da #22. Sem a correção, a #12 mandaria ganhos com
    // Infinity no mesmo ciclo.
    expect(perdas).toHaveLength(1);
  });

  it("a chave gravada carrega o id da posição", async () => {
    quotes = [{ symbol: "ARM", price: 293.9, error: null }];
    await checkPortfolioAlerts();
    expect(chavesGravadas).toContain("loss:v2:22:ARM:10");
  });

  it("o preço é pedido uma vez por ticker, não uma por posição", async () => {
    // Com ARM, AVGO e INTC repetidos em 03/10, get_quotes recebia o mesmo
    // símbolo duas vezes -- duas posições de ARM, dois "ARM" na linha de
    // comando do subprocesso.
    quotes = [{ symbol: "ARM", price: 307.49, error: null }];
    await checkPortfolioAlerts();
    expect(tickersPedidos).toEqual([["ARM"]]);
  });
});

describe("marcos de holding", () => {
  it("lote vendido não acumula tempo de posse", async () => {
    // META: comprada 20/03, vendida 07/05, e `holding:META:180` disparou em
    // 16/09 -- 132 dias depois da venda.
    posicoes = [pos(16, "META", "0.0000")];
    lotes = [{
      id: 25, positionId: 16, purchaseDate: diasAtras(200), amount: "198.0000",
      purchasePrice: "599.0000", saleDate: diasAtras(150), salePrice: "622.4172",
    }];
    quotes = [{ symbol: "META", price: 700, error: null }];

    await checkPortfolioAlerts();

    // Sem a correção: 30, 60, 90 e 180 dias, quatro e-mails.
    expect(holdings).toEqual([]);
  });

  it("lote em aberto continua recebendo os marcos", async () => {
    posicoes = [pos(23, "AVGO", "368.3000", "0.2715")];
    lotes = [{
      id: 36, positionId: 23, purchaseDate: diasAtras(35), amount: "100.0000",
      purchasePrice: "368.3000", saleDate: null, salePrice: null,
    }];
    quotes = [{ symbol: "AVGO", price: 355.14, error: null }];

    await checkPortfolioAlerts();

    expect(holdings.map((h) => h.milestone)).toEqual([30]);
  });

  it("dois lotes do mesmo ticker na MESMA data ganham um marco cada", async () => {
    // SKHY em produção: dois lotes de 15/07/2026, a 183,20 e a 178,15 --
    // lote dividido para venda parcial. A chave antiga era
    // `holding:TICKER:DATA:dias`, então o segundo lote nunca disparava.
    const data = diasAtras(40);
    posicoes = [pos(14, "SKHY", "180.6750", "1.1072")];
    lotes = [
      { id: 22, positionId: 14, purchaseDate: data, amount: "100.0000", purchasePrice: "178.1500", saleDate: null, salePrice: null },
      { id: 23, positionId: 14, purchaseDate: data, amount: "100.0000", purchasePrice: "183.2000", saleDate: null, salePrice: null },
    ];
    quotes = [{ symbol: "SKHY", price: 198.77, error: null }];

    await checkPortfolioAlerts();

    expect(holdings).toHaveLength(2);
    expect(chavesGravadas).toContain("holding:v2:22:SKHY:30");
    expect(chavesGravadas).toContain("holding:v2:23:SKHY:30");
  });
});

describe("posição sem lote nenhum", () => {
  it("cai no avg_cost armazenado — é a única fonte que existe", async () => {
    // SGOV em produção, 03/10: 4,9802 shares, custo 100,39, nenhum lote
    // registrado (importada por script). Não pode virar posição surda.
    posicoes = [pos(11, "SGOV", "100.3900", "4.9802")];
    lotes = [];
    quotes = [{ symbol: "SGOV", price: 115.45, error: null }];

    await checkPortfolioAlerts();

    expect(ganhos.map((g) => g.thr)).toEqual([10, 15]);
  });

  it("mas com avg_cost zerado nem isso dispara", async () => {
    posicoes = [pos(11, "SGOV", "0.0000", "4.9802")];
    lotes = [];
    quotes = [{ symbol: "SGOV", price: 100.44, error: null }];

    await checkPortfolioAlerts();

    expect(ganhos).toEqual([]);
  });
});

describe("custo médio vem dos lotes, não do campo armazenado", () => {
  it("avg_cost editado à mão não decide o alerta", async () => {
    // `PUT /portfolio/:id` edita avg_cost direto. Com os lotes dizendo 200 e
    // o campo dizendo 100, o preço de 230 é +15% pelos lotes e +130% pelo
    // campo -- a diferença entre avisar o limiar de 15 e varrer até o de 50.
    posicoes = [pos(1, "NVDA", "100.0000", "5.0000")];
    lotes = [{
      id: 5, positionId: 1, purchaseDate: diasAtras(10), amount: "1000.0000",
      purchasePrice: "200.0000", saleDate: null, salePrice: null,
    }];
    quotes = [{ symbol: "NVDA", price: 230, error: null }];

    await checkPortfolioAlerts();

    expect(ganhos.map((g) => g.thr)).toEqual([10, 15]);
    expect(ganhos[0].pct).toBeCloseTo(15, 6);
  });
});
