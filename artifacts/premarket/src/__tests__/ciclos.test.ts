/**
 * Ver o cabeçalho de lib/ciclos.ts para o incidente.
 *
 * Os lotes daqui são os REAIS da carteira em 25/09/2026, os mesmos que a
 * especificação usou como critério de aceite. Fixture inventada não teria
 * produzido o caso que motivou tudo: a ARM com duas compras vendidas no MESMO
 * dia (21/09), que é onde a ordem dos eventos decide se sai um ciclo ou dois.
 */
import { describe, it, expect } from "vitest";
import { construirCiclos, rotuloDoCiclo, type LoteDeCiclo } from "@/lib/ciclos";

const CENTAVO = 0.02;

function lote(
  ticker: string, purchaseDate: string, purchasePrice: number, amount: number,
  saleDate?: string, salePrice?: number,
): LoteDeCiclo {
  return { ticker, purchaseDate, purchasePrice, amount, saleDate, salePrice };
}

// A carteira real, na ordem em que a especificação a descreve.
const ARM_1 = lote("ARM", "2026-06-02", 399.73, 350, "2026-06-16", 406.56);
const ARM_2A = lote("ARM", "2026-07-09", 333.98, 350, "2026-09-21", 314.83);
const ARM_2B = lote("ARM", "2026-09-17", 259.14, 250, "2026-09-21", 314.83);
const INTC_1 = lote("INTC", "2026-06-02", 104.30, 350, "2026-06-24", 132.89);
const INTC_2 = lote("INTC", "2026-08-17", 105.24, 200, "2026-09-18", 107.16);

describe("construirCiclos — os casos reais da carteira", () => {
  it("ARM: dois ciclos, não um", () => {
    // O defeito exato do relato: a tabela juntava junho com julho→setembro e
    // publicava a média dos dois como "preço de compra".
    const { ciclos } = construirCiclos([ARM_1, ARM_2A, ARM_2B]);
    expect(ciclos).toHaveLength(2);

    const um = ciclos.find((c) => c.seq === 1)!;
    expect(um.lotes).toEqual([ARM_1]);
    expect(um.investido).toBeCloseTo(350, 2);
    expect(um.receita).toBeCloseTo(355.98, 1);
    expect(um.lucro).toBeCloseTo(5.98, 1);
    expect(um.lucroPct).toBeCloseTo(1.71, 1);
    expect(um.inicio).toBe("2026-06-02");
    expect(um.fim).toBe("2026-06-16");
    expect(um.dias).toBe(14);

    const dois = ciclos.find((c) => c.seq === 2)!;
    // As DUAS compras no mesmo ciclo, porque a posição só voltou a zero na
    // segunda venda de 21/09.
    expect(dois.lotes).toHaveLength(2);
    expect(dois.investido).toBeCloseTo(600, 2);
    expect(dois.receita).toBeCloseTo(633.66, 1);
    expect(dois.lucro).toBeCloseTo(33.66, 1);
    expect(dois.lucroPct).toBeCloseTo(5.61, 1);
    expect(dois.inicio).toBe("2026-07-09");
    expect(dois.fim).toBe("2026-09-21");
  });

  it("INTC: dois ciclos separados, como já aparecia certo", () => {
    const { ciclos } = construirCiclos([INTC_1, INTC_2]);
    expect(ciclos).toHaveLength(2);
    const um = ciclos.find((c) => c.seq === 1)!;
    // A especificação tabula +95,95; a fórmula dela dá 95,9366. A diferença de
    // 1,3 centavo é arredondamento da própria tabela, não da conta.
    expect(um.lucro).toBeCloseTo(95.94, 1);
    expect(um.lucroPct).toBeCloseTo(27.41, 1);
    const dois = ciclos.find((c) => c.seq === 2)!;
    expect(dois.lucro).toBeCloseTo(3.65, 1);
    expect(dois.lucroPct).toBeCloseTo(1.82, 1);
  });

  it("SMCI: seis lotes e UM ciclo só", () => {
    // Três compras e duas datas de venda (12 e 13/08). A posição só chega a
    // zero no fim, então é um ciclo — partir em dois seria inventar operação.
    const smci = [
      lote("SMCI", "2026-05-14", 31.66, 150, "2026-08-12", 44.00),
      lote("SMCI", "2026-05-14", 33.03, 150, "2026-08-13", 44.00),
      lote("SMCI", "2026-06-02", 49.26, 150, "2026-08-12", 44.00),
      lote("SMCI", "2026-06-02", 50.17, 150, "2026-08-13", 44.00),
      lote("SMCI", "2026-07-22", 31.47, 125, "2026-08-12", 44.00),
      lote("SMCI", "2026-07-22", 30.56, 125, "2026-08-13", 44.00),
    ];
    const { ciclos } = construirCiclos(smci);
    expect(ciclos).toHaveLength(1);
    expect(ciclos[0].lotes).toHaveLength(6);
    expect(ciclos[0].investido).toBeCloseTo(850, 2);
    expect(ciclos[0].inicio).toBe("2026-05-14");
    expect(ciclos[0].fim).toBe("2026-08-13");
  });
});

describe("construirCiclos — as regras", () => {
  it("no mesmo dia, compra entra antes de venda", () => {
    // Sem esse desempate a venda fecharia o ciclo antes de a compra do mesmo
    // dia entrar, e um ciclo viraria dois.
    const { ciclos } = construirCiclos([
      lote("X", "2026-01-05", 10, 100, "2026-01-10", 12),
      lote("X", "2026-01-10", 11, 110, "2026-01-20", 13),
    ]);
    expect(ciclos).toHaveLength(1);
    expect(ciclos[0].lotes).toHaveLength(2);
    expect(ciclos[0].fim).toBe("2026-01-20");
  });

  it("ciclo ainda aberto não vira linha", () => {
    const { ciclos } = construirCiclos([
      lote("X", "2026-01-05", 10, 100, "2026-01-10", 12),
      lote("X", "2026-02-01", 11, 110),   // sem venda
    ]);
    expect(ciclos).toHaveLength(1);
    expect(ciclos[0].fim).toBe("2026-01-10");
  });

  it("venda parcial não fecha o ciclo", () => {
    const { ciclos } = construirCiclos([
      lote("X", "2026-01-05", 10, 100, "2026-01-10", 12),
      lote("X", "2026-01-05", 10, 100, "2026-03-01", 15),
    ]);
    expect(ciclos).toHaveLength(1);
    expect(ciclos[0].fim).toBe("2026-03-01");
    expect(ciclos[0].investido).toBeCloseTo(200, 2);
  });

  it("a quantidade residual não deixa o ciclo aberto para sempre", () => {
    // 350/399.73 vendido inteiro não fecha em zero exato: sobra resíduo na
    // 16ª casa. É por isso que a comparação usa EPS e não zero.
    const { ciclos } = construirCiclos([ARM_1]);
    expect(ciclos).toHaveLength(1);
  });

  it("seq numera na ordem de abertura, e a lista vem do mais recente", () => {
    const { ciclos } = construirCiclos([ARM_1, ARM_2A, ARM_2B, INTC_1, INTC_2]);
    // Por data de FIM, do mais recente: 21/09, 18/09, 24/06, 16/06 — então
    // INTC#1 vem antes de ARM#1, apesar de o ARM ter aberto primeiro.
    expect(ciclos.map((c) => `${c.ticker}#${c.seq}`))
      .toEqual(["ARM#2", "INTC#2", "INTC#1", "ARM#1"]);
  });

  it("tickers diferentes não se misturam", () => {
    const { ciclos } = construirCiclos([ARM_1, INTC_1]);
    expect(ciclos.map((c) => c.ticker).sort()).toEqual(["ARM", "INTC"]);
    for (const c of ciclos) expect(c.seq).toBe(1);
  });

  it("preço médio é ponderado pela quantidade", () => {
    // 1000 a US$ 100 = 10 ações; 1000 a US$ 500 = 2 ações. Média ponderada da
    // compra = 2000/12 = 166,67, não (100+500)/2 = 300.
    const { ciclos } = construirCiclos([
      lote("X", "2026-01-05", 100, 1000, "2026-02-01", 120),
      lote("X", "2026-01-06", 500, 1000, "2026-02-01", 600),
    ]);
    expect(ciclos[0].precoMedioCompra).toBeCloseTo(166.67, 1);
    expect(ciclos[0].precoMedioVenda).toBeCloseTo(200, 1);
  });

  it("lote sem preço de compra é IGNORADO e relatado", () => {
    // Descartar calado faria a soma dos ciclos não fechar com o total do card,
    // sem nada na tela explicando a diferença.
    const semPreco = { ticker: "X", purchaseDate: "2026-01-05", amount: 100,
                       purchasePrice: null, saleDate: "2026-02-01", salePrice: 12 };
    const { ciclos, lotesIgnorados } = construirCiclos([ARM_1, semPreco]);
    expect(ciclos).toHaveLength(1);
    expect(lotesIgnorados).toEqual([semPreco]);
  });

  it("lote vendido sem preço de venda não fecha ciclo", () => {
    // Sem preço de venda não há receita; tratá-lo como venda produziria um
    // ciclo com lucro -100%.
    const { ciclos } = construirCiclos([
      { ticker: "X", purchaseDate: "2026-01-05", purchasePrice: 10, amount: 100,
        saleDate: "2026-02-01", salePrice: null },
    ]);
    expect(ciclos).toEqual([]);
  });

  it("carteira vazia não explode", () => {
    expect(construirCiclos([])).toEqual({ ciclos: [], lotesIgnorados: [] });
  });
});

describe("os totais não mudam", () => {
  it("a soma dos ciclos bate com o lucro realizado do card", () => {
    // Critério de aceite da especificação: agrupar por ciclo não pode mexer no
    // total. ARM (5,98 + 33,66) + INTC (95,94 + 3,65) = 139,23.
    const { ciclos } = construirCiclos([ARM_1, ARM_2A, ARM_2B, INTC_1, INTC_2]);
    const lucro = ciclos.reduce((s, c) => s + c.lucro, 0);
    const investido = ciclos.reduce((s, c) => s + c.investido, 0);
    const receita = ciclos.reduce((s, c) => s + c.receita, 0);
    expect(lucro).toBeCloseTo(139.23, 1);
    expect(investido).toBeCloseTo(1500, 2);
    expect(receita).toBeCloseTo(1639.23, 1);
    // E o lucro é sempre receita menos investido, ciclo a ciclo.
    for (const c of ciclos) {
      expect(c.lucro).toBeCloseTo(c.receita - c.investido, 6);
    }
  });

  it("agrupar por ciclo não muda a soma — a conta do card, lote a lote", () => {
    // O card soma os lotes vendidos direto, sem passar por ciclo nenhum (ver
    // `realized` em portfolio.tsx). O agrupamento só reparte os MESMOS lotes,
    // então as duas somas têm de coincidir. Se um dia divergirem, é porque
    // algum lote está caindo em dois ciclos ou em nenhum.
    const todos = [ARM_1, ARM_2A, ARM_2B, INTC_1, INTC_2];
    const doCard = todos.reduce((s, l) => {
      const qty = l.amount / (l.purchasePrice as number);
      return s + qty * ((l.salePrice as number) - (l.purchasePrice as number));
    }, 0);

    const { ciclos } = construirCiclos(todos);
    const dosCiclos = ciclos.reduce((s, c) => s + c.lucro, 0);
    expect(dosCiclos).toBeCloseTo(doCard, 6);

    // E nenhum lote se perdeu nem entrou duas vezes.
    const nosCiclos = ciclos.flatMap((c) => c.lotes);
    expect(nosCiclos).toHaveLength(todos.length);
    expect(new Set(nosCiclos).size).toBe(todos.length);
  });
});

describe("rotuloDoCiclo", () => {
  it("numera só quando o ticker tem mais de um ciclo", () => {
    const { ciclos } = construirCiclos([ARM_1, ARM_2A, ARM_2B, INTC_1]);
    const arm = ciclos.filter((c) => c.ticker === "ARM");
    expect(rotuloDoCiclo(arm[0], ciclos)).toMatch(/^ARM #\d$/);
    const intc = ciclos.find((c) => c.ticker === "INTC")!;
    expect(rotuloDoCiclo(intc, ciclos)).toBe("INTC");
  });
});

// Guarda contra a tolerância virar decoração: se alguém trocar a fórmula por
// uma que erre centavos, os `toBeCloseTo` acima ainda passariam em alguns
// casos. Este fixa o valor exato de um ciclo simples.
describe("precisão", () => {
  it("a receita de um lote é quantidade x preço de venda, sem atalho", () => {
    const { ciclos } = construirCiclos([ARM_1]);
    const esperado = (350 / 399.73) * 406.56;
    expect(ciclos[0].receita).toBe(esperado);
    expect(Math.abs(ciclos[0].receita - 355.98)).toBeLessThan(CENTAVO);
  });
});
