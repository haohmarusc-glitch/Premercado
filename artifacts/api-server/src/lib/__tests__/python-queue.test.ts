/**
 * Fila global de subprocessos Python (concorrência 1).
 *
 * Medido em produção 02/08 com o startup_probe da #200: o agente sozinho sobe
 * em 2,18s (boot 0,29s + imports 1,89s); na rajada de seis spawns quase
 * simultâneos, o `boot` sozinho ia a 30-44s e os imports não fechavam nem em
 * 120s. Serializar troca contenção por espera.
 *
 * O que estes testes travam é o que faz a fila não virar um novo problema:
 * ordem preservada, uma tarefa por vez, e -- o mais importante -- uma tarefa
 * que falha não pode parar as seguintes.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { runExclusive, _resetQueue } from "../python-queue";

beforeEach(() => {
  _resetQueue();
});

const espera = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("runExclusive", () => {
  it("roda uma tarefa por vez", async () => {
    let simultaneas = 0;
    let pico = 0;

    const tarefa = async () => {
      simultaneas += 1;
      pico = Math.max(pico, simultaneas);
      await espera(10);
      simultaneas -= 1;
    };

    await Promise.all([
      runExclusive("a", tarefa),
      runExclusive("b", tarefa),
      runExclusive("c", tarefa),
      runExclusive("d", tarefa),
    ]);

    expect(pico).toBe(1);
  });

  it("preserva a ordem de entrada", async () => {
    const ordem: string[] = [];
    const tarefa = (nome: string) => async () => {
      await espera(nome === "a" ? 20 : 1);
      ordem.push(nome);
    };

    await Promise.all([
      runExclusive("a", tarefa("a")),
      runExclusive("b", tarefa("b")),
      runExclusive("c", tarefa("c")),
    ]);

    expect(ordem).toEqual(["a", "b", "c"]);
  });

  it("devolve o valor da tarefa a quem chamou", async () => {
    await expect(runExclusive("x", async () => 42)).resolves.toBe(42);
  });

  it("propaga a falha só para quem chamou", async () => {
    const falha = runExclusive("ruim", async () => {
      throw new Error("boom");
    });
    await expect(falha).rejects.toThrow("boom");
  });

  it("uma tarefa que falha não trava a fila", async () => {
    // O caso real: todo checker pode estourar timeout, e um timeout não pode
    // impedir os próximos ciclos de rodarem.
    const resultados: string[] = [];

    const a = runExclusive("a", async () => {
      throw new Error("timeout");
    }).catch(() => resultados.push("a-falhou"));

    const b = runExclusive("b", async () => {
      resultados.push("b-rodou");
    });

    await Promise.all([a, b]);
    expect(resultados).toEqual(["a-falhou", "b-rodou"]);
  });

  it("sobrevive a várias falhas seguidas", async () => {
    const falhar = () => runExclusive("f", async () => { throw new Error("x"); }).catch(() => "erro");
    const resultados = await Promise.all([falhar(), falhar(), falhar(), runExclusive("ok", async () => "ok")]);
    expect(resultados).toEqual(["erro", "erro", "erro", "ok"]);
  });

  it("uma tarefa lenta atrasa a seguinte, não a cancela", async () => {
    const inicio = Date.now();
    let terminouSegunda = 0;

    await Promise.all([
      runExclusive("lenta", () => espera(40)),
      runExclusive("rapida", async () => { terminouSegunda = Date.now(); }),
    ]);

    expect(terminouSegunda - inicio).toBeGreaterThanOrEqual(35);
  });
});
