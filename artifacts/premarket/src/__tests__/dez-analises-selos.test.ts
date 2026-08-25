/**
 * O contrato de honestidade do menu "As 10 Análises".
 *
 * O valor da tela não são os dez cartões — é o SELO. Os prompts originais
 * misturam, na mesma frase, o que o app mede (reação a earnings, correlação)
 * e o que nenhuma fonte aqui tem (market share de 3 anos, qualidade de
 * gestão, previsão do Fed). Estes testes impedem a erosão silenciosa desse
 * selo: promover uma análise a MEDIDA, ou pendurar um link numa que está
 * FORA, passa a quebrar o build.
 */
import { describe, it, expect } from "vitest";
import { ANALISES } from "../pages/analises";

describe("As 10 Análises — selos de origem", () => {
  it("são exatamente dez, numeradas de 1 a 10", () => {
    expect(ANALISES.map((a) => a.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("toda análise declara a pergunta e o que é medido", () => {
    for (const a of ANALISES) {
      expect(a.pergunta.length, `análise ${a.n}`).toBeGreaterThan(10);
      expect(a.medido.length, `análise ${a.n}`).toBeGreaterThan(20);
    }
  });

  it("análise FORA não pode ter destino — link sugere que o app faz aquilo", () => {
    for (const a of ANALISES.filter((x) => x.selo === "FORA")) {
      expect(a.destinos, `análise ${a.n} está FORA mas tem link`).toBeUndefined();
      // E precisa dizer POR QUE ficou de fora: sumir com a análise ou deixá-la
      // muda são as duas formas de o leitor achar que foi esquecimento.
      expect(a.ressalva, `análise ${a.n} está FORA sem motivo`).toBeTruthy();
    }
  });

  it("toda análise PARCIAL nomeia o que ficou de fora", () => {
    for (const a of ANALISES.filter((x) => x.selo === "PARCIAL")) {
      expect(a.ressalva, `análise ${a.n} é PARCIAL sem ressalva`).toBeTruthy();
    }
  });

  it("toda análise que não está FORA leva a algum lugar (link ou execução aqui)", () => {
    for (const a of ANALISES.filter((x) => x.selo !== "FORA")) {
      const temSaida = Boolean(a.destinos?.length) || a.inline === true;
      expect(temSaida, `análise ${a.n} não leva a lugar nenhum`).toBe(true);
    }
  });

  it("só a análise 9 roda dentro desta tela", () => {
    // As outras nove já existem em telas próprias; duplicar a implementação
    // aqui criaria a segunda conta do mesmo indicador — a armadilha nº 2b do
    // playbook do repo.
    expect(ANALISES.filter((a) => a.inline).map((a) => a.n)).toEqual([9]);
  });

  it("as duas fora de escopo continuam fora: portfólio e dividendos", () => {
    // Alocação entre classes de ativo é aconselhamento pessoal sem dado no
    // app; dividendos numa carteira de semis, com regra fiscal americana,
    // daria conselho errado com cara de precisão.
    expect(ANALISES.filter((a) => a.selo === "FORA").map((a) => a.n)).toEqual([5, 7]);
  });
});
