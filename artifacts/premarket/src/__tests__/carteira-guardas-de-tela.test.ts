/**
 * Guardas de call-site da tela de Carteira.
 *
 * `peso-carteira.test.ts` testava a função pura e passava — a função estava
 * certa. O defeito era o chamador: o denominador da coluna era somado sobre
 * TODAS as posições e o gráfico saía da lista já sem as encerradas. Teste de
 * função pura não alcança isso, então aqui o fonte é lido.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const aqui = dirname(fileURLToPath(import.meta.url));
const fonte = readFileSync(resolve(aqui, "../pages/portfolio.tsx"), "utf8");
const codigo = fonte
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split("\n")
  .filter((l) => !l.trim().startsWith("//"))
  .join("\n");

describe("pages/portfolio.tsx — coluna Peso e gráfico", () => {
  it("o denominador vem de totalDoPeso", () => {
    expect(codigo).toMatch(/const totalCurrentUsd = totalDoPeso\(/);
  });

  it("o gráfico vem de fatiasDaAlocacao", () => {
    expect(codigo).toMatch(/fatiasDaAlocacao\(/);
  });

  it("não há um somaDosValoresAtuais solto sobre `positions`", () => {
    // A forma antiga: somaDosValoresAtuais(positions.map(...)), que incluía as
    // posições já vendidas no denominador da coluna e em nada mais.
    expect(codigo).not.toMatch(/somaDosValoresAtuais\(\s*positions\.map/);
  });

  it("as linhas candidatas carregam `vendida`", () => {
    // É o campo que `linhasQueContamNoPeso` usa; sem ele o filtro não existe.
    expect(codigo).toMatch(/vendida:\s*soldPositionIds\.has\(p\.id\)/);
    expect(codigo).toMatch(/vendida:\s*r\.isSoldOut/);
  });

  it("derivePosition usa o predicado e a conta compartilhados", () => {
    expect(codigo).toMatch(/purchases\.filter\(loteEmAberto\)/);
    expect(codigo).toMatch(/totaisDosLotesAbertos\(open\)/);
    // E não reescreve a conta à mão:
    expect(codigo).not.toMatch(/quantity \+= p\.amount \/ \(p\.purchasePrice as number\)/);
  });

  it("não reescreve o predicado de lote em aberto", () => {
    expect(codigo).not.toMatch(/!\(\s*\w+\.saleDate\s*&&\s*\w+\.salePrice/);
  });

  it("importa de @workspace/carteira, a definição única", () => {
    expect(fonte).toMatch(/from "@workspace\/carteira"/);
  });
});
