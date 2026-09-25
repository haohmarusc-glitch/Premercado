/**
 * Ver o cabeçalho de lib/peso-carteira.ts para o incidente: a coluna "Peso" e
 * o gráfico "Alocação atual" mostravam números diferentes na mesma tela porque
 * dividiam por denominadores diferentes (investido x valor atual).
 *
 * O teste que importa aqui é o último: coluna e gráfico saem da MESMA conta.
 */
import { describe, it, expect } from "vitest";
import {
  pesoNaCarteira, pesosDaCarteira, somaDosValoresAtuais,
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
