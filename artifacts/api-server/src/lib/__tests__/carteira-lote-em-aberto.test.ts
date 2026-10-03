/**
 * `loteEmAberto` e vizinhos — a definição única de @workspace/carteira.
 *
 * Existiam duas respostas para "o lote está em aberto?" no repo:
 * `recomputePosition` olhava só `saleDate`, e os outros quatro leitores
 * olhavam data E preço. Um lote com data e preço nulo saía da quantidade num
 * e continuava aberto nos outros. Este arquivo fixa a regra e as bordas que o
 * `PATCH` passou a recusar.
 */
import { describe, it, expect } from "vitest";
import {
  loteEmAberto,
  vendaRegistrada,
  custoMedioUtilizavel,
  variacaoContraCusto,
  erroNoEstadoDeVenda,
  totaisDaPosicao,
} from "../portfolio-math";

describe("loteEmAberto / vendaRegistrada", () => {
  it("os dois campos preenchidos: vendido", () => {
    const l = { saleDate: "2026-10-02", salePrice: 105.88 };
    expect(vendaRegistrada(l)).toBe(true);
    expect(loteEmAberto(l)).toBe(false);
  });

  it("nenhum dos dois: em aberto", () => {
    expect(loteEmAberto({ saleDate: null, salePrice: null })).toBe(true);
    expect(loteEmAberto({})).toBe(true);
  });

  it("data sem preço: EM ABERTO -- era aqui que as duas definições divergiam", () => {
    // `recomputePosition` dizia fechado (`saleDate == null` era falso) e tirava
    // o lote da quantidade; os outros quatro leitores diziam aberto. A posição
    // desaparecia da Carteira e seguia viva no Painel de Cenários.
    expect(loteEmAberto({ saleDate: "2026-10-02", salePrice: null })).toBe(true);
  });

  it("preço sem data: em aberto", () => {
    expect(loteEmAberto({ saleDate: null, salePrice: 105.88 })).toBe(true);
  });

  it("preço zero ou negativo não fecha o lote", () => {
    // `!(saleDate && salePrice)` tratava 0 como aberto por acidente de falsy.
    // Agora é por regra -- e negativo, que a forma antiga tratava como
    // VENDIDO, também não fecha.
    expect(loteEmAberto({ saleDate: "2026-10-02", salePrice: 0 })).toBe(true);
    expect(loteEmAberto({ saleDate: "2026-10-02", salePrice: -5 })).toBe(true);
  });

  it("data em branco não conta como data", () => {
    expect(loteEmAberto({ saleDate: "   ", salePrice: 105.88 })).toBe(true);
  });

  it("preço como STRING, que é como o Postgres devolve numeric", () => {
    // Playbook §7: coluna numeric chega como string via Drizzle mesmo com
    // .$type<number>(). `"0.0000" > 0` comparado como string daria outra
    // resposta.
    expect(vendaRegistrada({ saleDate: "2026-10-02", salePrice: "105.8800" })).toBe(true);
    expect(loteEmAberto({ saleDate: "2026-10-02", salePrice: "0.0000" })).toBe(true);
  });
});

describe("custoMedioUtilizavel / variacaoContraCusto", () => {
  it("custo zerado é inutilizável — o defeito dos 69 e-mails", () => {
    // `recomputePosition` zera avg_cost ao vender tudo. Em produção isso
    // chegava aqui como a string "0.0000".
    expect(custoMedioUtilizavel("0.0000")).toBeNull();
    expect(custoMedioUtilizavel(0)).toBeNull();
    expect(custoMedioUtilizavel(null)).toBeNull();
    expect(custoMedioUtilizavel(-1)).toBeNull();
    expect(custoMedioUtilizavel("333.9800")).toBe(333.98);
  });

  it("variação contra custo zerado é null, nunca Infinity", () => {
    // BABA em 02/10/2026: comprada a 125,97, vendida a 105,88, prejuízo de
    // 15,95% -- e `gain:BABA:50` disparou porque a conta deu Infinity.
    expect(variacaoContraCusto(105.88, "0.0000")).toBeNull();
    // A conta antiga, explícita, para não ser "simplificada" de volta:
    expect(((105.88 - 0) / 0) * 100).toBe(Infinity);
  });

  it("a variação normal continua exata", () => {
    expect(variacaoContraCusto(230, 200)).toBeCloseTo(15, 10);
    // ARM #22 em 02/10/2026: 350 em 1,0480 shares a 333,98, cotada a 307,49.
    expect(variacaoContraCusto(307.49, "333.9800")).toBeCloseTo(-7.93, 2);
  });

  it("preço ausente ou não-finito devolve null", () => {
    expect(variacaoContraCusto(null, 200)).toBeNull();
    expect(variacaoContraCusto(Infinity, 200)).toBeNull();
    expect(variacaoContraCusto(NaN, 200)).toBeNull();
    expect(variacaoContraCusto("abc", 200)).toBeNull();
  });
});

describe("erroNoEstadoDeVenda — os 400 da API", () => {
  it("os dois nulos passam: é o 'desfazer venda' da tela", () => {
    expect(erroNoEstadoDeVenda(null, null)).toBeNull();
    expect(erroNoEstadoDeVenda(undefined, undefined)).toBeNull();
  });

  it("os dois preenchidos passam", () => {
    expect(erroNoEstadoDeVenda("2026-10-02", 105.88)).toBeNull();
    expect(erroNoEstadoDeVenda("2026-10-02", "105.8800")).toBeNull();
  });

  it("data sem preço é 400", () => {
    expect(erroNoEstadoDeVenda("2026-10-02", null)).toMatch(/saleDate sem salePrice/);
  });

  it("preço sem data é 400", () => {
    expect(erroNoEstadoDeVenda(null, 105.88)).toMatch(/salePrice sem saleDate/);
  });

  it("preço zero ou negativo é 400, mesmo com data", () => {
    // O schema zod aceita: `salePrice: zod.number().nullish()`, sem .gt(0).
    expect(erroNoEstadoDeVenda("2026-10-02", 0)).toMatch(/maior que zero/);
    expect(erroNoEstadoDeVenda("2026-10-02", -5)).toMatch(/maior que zero/);
  });

  it("preço não-finito é 400", () => {
    // O zod 4 já recusa Infinity em z.number() -- esta guarda cobre o caminho
    // que não passa pelo zod: valor vindo do banco como string.
    expect(erroNoEstadoDeVenda("2026-10-02", Infinity)).toMatch(/finito/);
    expect(erroNoEstadoDeVenda("2026-10-02", NaN)).toMatch(/finito/);
    expect(erroNoEstadoDeVenda("2026-10-02", "nao-e-numero")).toMatch(/finito/);
  });

  it("JSON com número que estoura o double vira Infinity", () => {
    // `JSON.parse('{"a":1e400}').a === Infinity` -- é por aqui que um corpo
    // HTTP consegue entregar não-finito sem escrever "Infinity".
    expect(JSON.parse('{"a":1e400}').a).toBe(Infinity);
  });
});

describe("totaisDaPosicao", () => {
  it("ignora os lotes vendidos", () => {
    // ARM #12 em produção: três lotes, todos vendidos em 21/09.
    const totais = totaisDaPosicao([
      { amount: 350, purchasePrice: 399.73, saleDate: "2026-06-16", salePrice: 406.56 },
      { amount: 350, purchasePrice: 333.98, saleDate: "2026-09-21", salePrice: 314.83 },
      { amount: 250, purchasePrice: 259.1372, saleDate: "2026-09-21", salePrice: 314.83 },
    ]);
    expect(totais).toEqual({ quantity: 0, avgCost: 0, investedAmount: 0 });
  });

  it("o lote meio-vendido continua contando", () => {
    const totais = totaisDaPosicao([
      { amount: 350, purchasePrice: 333.98, saleDate: "2026-09-21", salePrice: null },
    ]);
    expect(totais.quantity).toBeCloseTo(350 / 333.98, 6);
    expect(totais.investedAmount).toBe(350);
  });

  it("aceita amount e purchasePrice como string", () => {
    const totais = totaisDaPosicao([
      { amount: "350.0000", purchasePrice: "333.9800", saleDate: null, salePrice: null },
    ]);
    expect(totais.quantity).toBeCloseTo(1.04797, 5);
    expect(totais.avgCost).toBeCloseTo(333.98, 6);
  });
});
