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
import { runExclusive, runExclusiveFresh, filaPendentes, _resetQueue } from "../python-queue";

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

/**
 * Serializar sozinho trocou contenção por backlog: em produção as esperas
 * cresceram 60s -> 330s ao longo de uma manhã e nunca voltaram. A causa é
 * estrutural -- o setInterval dos checkers enfileira um lote novo a cada 5min
 * independentemente de o anterior ter drenado, então enquanto a drenagem for
 * mais lenta que o intervalo a fila não tem ponto de equilíbrio.
 *
 * O descarte por idade é o freio: tarefa periódica que esperou mais que o
 * próprio período é obsoleta por construção (o ciclo seguinte já está atrás
 * dela na fila), e rodá-la só atrasa quem vem depois.
 */
describe("runExclusiveFresh — descarte por obsolescência", () => {
  it("roda normalmente quando a espera cabe no prazo", async () => {
    const r = await runExclusiveFresh("ok", async () => "valor", 1_000);
    expect(r).toBe("valor");
  });

  it("descarta e devolve null quando a espera estoura o prazo", async () => {
    let rodou = false;
    const lenta = runExclusive("lenta", () => espera(40));
    const obsoleta = runExclusiveFresh(
      "obsoleta",
      async () => {
        rodou = true;
        return "nao devia rodar";
      },
      10, // prazo menor que a tarefa da frente
    );

    await lenta;
    expect(await obsoleta).toBeNull();
    // O ponto do descarte é NÃO gastar o processo Python.
    expect(rodou).toBe(false);
  });

  it("descartar é barato: não atrasa quem vem depois", async () => {
    const marcos: string[] = [];
    const lenta = runExclusive("lenta", async () => {
      await espera(40);
      marcos.push("lenta");
    });
    const velha = runExclusiveFresh("velha", async () => { marcos.push("velha"); }, 10);
    const nova = runExclusiveFresh("nova", async () => { marcos.push("nova"); }, 10_000);

    await Promise.all([lenta, velha, nova]);
    expect(marcos).toEqual(["lenta", "nova"]);
  });

  it("uma tarefa descartada não interrompe a fila", async () => {
    const lenta = runExclusive("lenta", () => espera(30));
    const descartada = runExclusiveFresh("descartada", async () => "x", 1);
    const seguinte = runExclusive("seguinte", async () => "cheguei");

    await lenta;
    expect(await descartada).toBeNull();
    expect(await seguinte).toBe("cheguei");
  });

  it("prazo não afeta quem já está na frente da fila", async () => {
    // Primeira da fila não espera nada, então nunca é descartada -- mesmo com
    // prazo ridiculamente curto.
    const r = await runExclusiveFresh("primeira", async () => "rodou", 0);
    expect(r).toBe("rodou");
  });

  it("runExclusive não ganha prazo nenhum: espera o que for preciso", async () => {
    let rodou = false;
    const lenta = runExclusive("lenta", () => espera(40));
    const semPrazo = runExclusive("sem-prazo", async () => { rodou = true; return "ok"; });

    await lenta;
    expect(await semPrazo).toBe("ok");
    expect(rodou).toBe(true);
  });
});

describe("filaPendentes", () => {
  it("conta enfileiradas + a que roda, e volta a zero ao drenar", async () => {
    expect(filaPendentes()).toBe(0);

    const a = runExclusive("a", () => espera(20));
    const b = runExclusive("b", () => espera(20));
    expect(filaPendentes()).toBe(2);

    await Promise.all([a, b]);
    expect(filaPendentes()).toBe(0);
  });

  it("decrementa também quando a tarefa falha ou é descartada", async () => {
    const falha = runExclusive("falha", async () => { throw new Error("x"); }).catch(() => null);
    const lenta = runExclusive("lenta", () => espera(30));
    const descartada = runExclusiveFresh("descartada", async () => "x", 1);

    await Promise.all([falha, lenta, descartada]);
    expect(filaPendentes()).toBe(0);
  });
});
