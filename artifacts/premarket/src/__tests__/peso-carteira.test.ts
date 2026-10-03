/**
 * Ver o cabeçalho de lib/peso-carteira.ts para o incidente: a coluna "Peso" e
 * o gráfico "Alocação atual" mostravam números diferentes na mesma tela porque
 * dividiam por denominadores diferentes (investido x valor atual).
 *
 * O teste que importa aqui é o último: coluna e gráfico saem da MESMA conta.
 */
import { describe, it, expect } from "vitest";
import {
  somaDosValoresAtuais, pesoNaCarteira, pesosDaCarteira,
  linhasQueContamNoPeso, totalDoPeso, fatiasDaAlocacao,
} from "@/lib/peso-carteira";

describe("somaDosValoresAtuais", () => {
  it("soma o que tem cotação e ignora o que não tem", () => {
    expect(somaDosValoresAtuais([100, 200, 700])).toBe(1000);
    // null = sem cotação. Não é zero: zero afirmaria que a posição não vale
    // nada, e o que se sabe é que o preço não veio.
    expect(somaDosValoresAtuais([100, null, 200])).toBe(300);
  });

  it("não deixa lixo entrar no denominador", () => {
    expect(somaDosValoresAtuais([100, NaN, Infinity, -50, 0, 200])).toBe(300);
  });

  it("carteira vazia soma zero", () => {
    expect(somaDosValoresAtuais([])).toBe(0);
  });
});

describe("pesoNaCarteira", () => {
  it("é a fatia do valor atual", () => {
    expect(pesoNaCarteira(250, 1000)).toBeCloseTo(25, 6);
  });

  it("nunca devolve NaN", () => {
    // `NaN.toFixed(1)` imprime "NaN" na tela, e é o que sai de dividir por um
    // total zero numa carteira sem cotação nenhuma.
    for (const [v, t] of [[100, 0], [null, 1000], [NaN, 1000], [100, NaN]] as const) {
      const r = pesoNaCarteira(v as number | null, t as number);
      expect(Number.isFinite(r)).toBe(true);
      expect(Number.isNaN(r)).toBe(false);
    }
  });

  it("posição sem cotação pesa zero, não some", () => {
    expect(pesoNaCarteira(null, 1000)).toBe(0);
  });
});

describe("pesosDaCarteira", () => {
  it("os pesos somam 100 quando todas têm cotação", () => {
    const pesos = pesosDaCarteira([1469.5, 86.3, 95.5, 1430.43]);
    expect(pesos.reduce((s, p) => s + p, 0)).toBeCloseTo(100, 6);
  });

  it("os pesos somam 100 mesmo com posição sem cotação", () => {
    // A sem-cotação pesa 0 e sai do denominador, então as outras continuam
    // dividindo 100 entre si -- não sobra "peso perdido".
    const pesos = pesosDaCarteira([500, null, 500]);
    expect(pesos).toEqual([50, 0, 50]);
    expect(pesos.reduce((s, p) => s + p, 0)).toBeCloseTo(100, 6);
  });
});

describe("a coluna e o gráfico dão o mesmo número", () => {
  it("peso da tabela = fatia do gráfico, posição por posição", () => {
    // Valores no espírito do relato: NVDA dominante, BABA e AVGO pequenas.
    const valores = [1469.5, 86.3, 95.5, 1430.43];

    // Como a COLUNA calcula: peso de cada um sobre o total.
    const total = somaDosValoresAtuais(valores);
    const daColuna = valores.map((v) => pesoNaCarteira(v, total));

    // Como o GRÁFICO de fatias calcula: valor sobre a soma dos valores que ele
    // recebeu. Mesmo insumo, mesma conta.
    const doGrafico = valores.map((v) => (v / valores.reduce((s, x) => s + x, 0)) * 100);

    daColuna.forEach((p, i) => expect(p).toBeCloseTo(doGrafico[i], 10));
  });

  it("dividir pelo INVESTIDO dá outro número — o defeito que existia", () => {
    // Registro do que estava errado: com os mesmos valores atuais mas
    // denominador de custo, NVDA sai 47,7% num lugar e 46,1% no outro. É um
    // ponto e meio de diferença, suficiente para o usuário notar e não
    // suficiente para parecer bug óbvio.
    const atual = [1469.5, 86.3, 95.5, 1430.43];
    const investido = [1275.0, 100.0, 100.0, 1290.0];
    const porAtual = pesoNaCarteira(atual[0], somaDosValoresAtuais(atual));
    const porInvestido = (investido[0] / investido.reduce((s, x) => s + x, 0)) * 100;
    expect(Math.abs(porAtual - porInvestido)).toBeGreaterThan(1);
  });
});

// ── a POPULAÇÃO, não só a métrica ──────────────────────────────────────────
//
// A correção de 25/09 unificou o que o peso MEDE (valor atual nas duas
// pontas) e deixou passar SOBRE QUAIS posições. O denominador da coluna era
// somado sobre todas as posições; o gráfico, sobre as não-vendidas. Os testes
// acima passavam -- eles exercitam a função pura, e a função pura estava
// certa. Era o chamador que entregava conjuntos diferentes.
//
// Os números deste bloco são a carteira de 02/07/2026, com MU e INTC já
// totalmente vendidas e `quantity` armazenado ainda em 0,4609 e 3,3558
// (o estado em que `PUT /portfolio/:id` deixa a posição).

describe("posição vendida não entra no peso — nem na coluna, nem no gráfico", () => {
  const carteira = [
    { ticker: "NVDA", valorAtualUsd: 1276.60, vendida: false },
    { ticker: "GOOGL", valorAtualUsd: 628.77, vendida: false },
    { ticker: "SMCI", valorAtualUsd: 591.94, vendida: false },
    { ticker: "SGOV", valorAtualUsd: 500.21, vendida: false },
    { ticker: "ARM", valorAtualUsd: 267.21, vendida: false },
    { ticker: "TSLA", valorAtualUsd: 197.94, vendida: false },
    { ticker: "MU", valorAtualUsd: 495.42, vendida: true },   // vendida 18/06
    { ticker: "INTC", valorAtualUsd: 400.45, vendida: true },  // vendida 24/06
  ];

  it("o denominador é só das posições vivas", () => {
    expect(totalDoPeso(carteira)).toBeCloseTo(3462.67, 2);
    // O que era antes: 4.358,54, inflado em 895,86 pelas duas fantasmas.
    expect(somaDosValoresAtuais(carteira.map((l) => l.valorAtualUsd))).toBeCloseTo(4358.54, 2);
  });

  it("os pesos somam 100, não 79,4", () => {
    const total = totalDoPeso(carteira);
    const soma = linhasQueContamNoPeso(carteira)
      .reduce((s, l) => s + pesoNaCarteira(l.valorAtualUsd, total), 0);
    expect(soma).toBeCloseTo(100, 6);
  });

  it("NVDA pesa 36,9% e não 29,3%", () => {
    expect(pesoNaCarteira(1276.60, totalDoPeso(carteira))).toBeCloseTo(36.9, 1);
    expect(pesoNaCarteira(1276.60, 4358.54)).toBeCloseTo(29.3, 1);
  });

  it("a coluna e o gráfico dão o mesmo número, posição por posição", () => {
    const total = totalDoPeso(carteira);
    const fatias = fatiasDaAlocacao(carteira);
    const totalDoGrafico = fatias.reduce((s, f) => s + f.value, 0);
    for (const f of fatias) {
      const naColuna = pesoNaCarteira(f.value, total);
      const noGrafico = (f.value / totalDoGrafico) * 100;
      expect(naColuna).toBeCloseTo(noGrafico, 10);
    }
  });

  it("a vendida não aparece no gráfico", () => {
    expect(fatiasDaAlocacao(carteira).map((f) => f.name)).toEqual(
      ["NVDA", "GOOGL", "SMCI", "SGOV", "ARM", "TSLA"],
    );
  });

  it("posição sem cotação também fica fora das duas pontas", () => {
    const comLacuna = [...carteira, { ticker: "WOLF", valorAtualUsd: null, vendida: false }];
    expect(totalDoPeso(comLacuna)).toBeCloseTo(3462.67, 2);
    expect(fatiasDaAlocacao(comLacuna).map((f) => f.name)).not.toContain("WOLF");
  });

  it("carteira toda vendida: denominador zero e nenhuma fatia, sem NaN", () => {
    const tudoVendido = carteira.map((l) => ({ ...l, vendida: true }));
    expect(totalDoPeso(tudoVendido)).toBe(0);
    expect(fatiasDaAlocacao(tudoVendido)).toEqual([]);
    expect(pesoNaCarteira(495.42, 0)).toBe(0);
  });
});
