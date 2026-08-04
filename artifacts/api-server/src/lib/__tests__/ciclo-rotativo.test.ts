/**
 * Rotação do lote de checkers.
 *
 * O descarte por idade da fila Python resolve o backlog, mas cria starvation:
 * numa fila FIFO quem entra por último sempre espera mais e sempre é o
 * primeiro a ser descartado. Com ordem fixa, o último do lote pararia de rodar
 * durante toda uma manhã movimentada -- sem erro, sem log de falha, só
 * deixando de disparar alertas.
 */
import { describe, it, expect } from "vitest";
import { ordemRotacionada, deveDispararCiclo, TAREFAS_POR_CICLO } from "../ciclo-rotativo";

const LOTE = ["alertas", "spike", "bounce", "squeeze"];

describe("ordemRotacionada", () => {
  it("ciclo 0 mantém a ordem original", () => {
    expect(ordemRotacionada(LOTE, 0)).toEqual(LOTE);
  });

  it("cada ciclo avança o ponto de partida", () => {
    expect(ordemRotacionada(LOTE, 1)).toEqual(["spike", "bounce", "squeeze", "alertas"]);
    expect(ordemRotacionada(LOTE, 2)).toEqual(["bounce", "squeeze", "alertas", "spike"]);
    expect(ordemRotacionada(LOTE, 3)).toEqual(["squeeze", "alertas", "spike", "bounce"]);
  });

  it("dá a volta sem precisar que o chamador zere o contador", () => {
    // alert-checker.ts só faz `inicioDoCiclo += 1`, sem módulo -- a rotação
    // tem que aguentar contador crescendo pra sempre.
    expect(ordemRotacionada(LOTE, 4)).toEqual(LOTE);
    expect(ordemRotacionada(LOTE, 401)).toEqual(ordemRotacionada(LOTE, 1));
  });

  it("nunca perde nem duplica item", () => {
    for (let i = 0; i < 12; i++) {
      const girado = ordemRotacionada(LOTE, i);
      expect(girado).toHaveLength(LOTE.length);
      expect([...girado].sort()).toEqual([...LOTE].sort());
    }
  });

  /**
   * A propriedade que importa: ao longo de N ciclos, cada item ocupa a última
   * posição (a que é descartada primeiro) exatamente uma vez. É isso que
   * transforma "o squeeze nunca roda" em "cada um perde um ciclo em quatro".
   */
  it("distribui a última posição igualmente entre todos", () => {
    const ultimos = Array.from({ length: LOTE.length }, (_, i) =>
      ordemRotacionada(LOTE, i).at(-1),
    );
    expect([...ultimos].sort()).toEqual([...LOTE].sort());
  });

  it("índice negativo não devolve buraco", () => {
    expect(ordemRotacionada(LOTE, -1)).toEqual(["squeeze", "alertas", "spike", "bounce"]);
  });

  it("lista vazia não quebra", () => {
    expect(ordemRotacionada([], 3)).toEqual([]);
  });
});

describe("deveDispararCiclo", () => {
  it("dispara com a fila vazia", () => {
    expect(deveDispararCiclo(0)).toBe(true);
  });

  it("dispara enquanto a fila não acumulou uma volta inteira", () => {
    expect(deveDispararCiclo(1)).toBe(true);
    expect(deveDispararCiclo(TAREFAS_POR_CICLO - 1)).toBe(true);
  });

  it("para quando a volta anterior ainda não drenou", () => {
    // O caso de produção: get_quotes(60s) + get_technicals(60s) +
    // run_checkers(180s) = 300s de trabalho por ciclo de 300s. Sem folga,
    // qualquer atraso vira dívida, e empilhar mais um lote só empurra a espera.
    expect(deveDispararCiclo(TAREFAS_POR_CICLO)).toBe(false);
  });

  it("continua parado enquanto a fila estiver funda", () => {
    // Produção 04/08: esperas de 211s e 208s com 5 e 6 pendentes.
    expect(deveDispararCiclo(5)).toBe(false);
    expect(deveDispararCiclo(6)).toBe(false);
  });

  it("aceita limite explícito para quem quiser outra política", () => {
    expect(deveDispararCiclo(2, 10)).toBe(true);
    expect(deveDispararCiclo(10, 10)).toBe(false);
  });
});
