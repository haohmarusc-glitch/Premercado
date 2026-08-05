import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../logger", () => ({
  logger: { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}));

// O limite é lido do módulo na importação, então precisa estar no env ANTES.
process.env["PYTHON_HTTP_CONCORRENCIA"] = "2";
const { comVagaPython, estadoDasVagas, _resetVagas } = await import("../vaga-python");

/** Promise controlável de fora, para segurar uma vaga pelo tempo do teste. */
function represa() {
  let liberar!: () => void;
  let falhar!: (e: Error) => void;
  const p = new Promise<void>((res, rej) => {
    liberar = () => res();
    falhar = rej;
  });
  return { p, liberar, falhar };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

/**
 * Ocupa uma vaga sem que ninguém aguarde o resultado.
 *
 * O `.catch` não é decoração: `_resetVagas` rejeita quem ficou na fila, e uma
 * promise rejeitada sem handler vira unhandled rejection que derruba o runner
 * -- num teste que, fora isso, passa. Foi o que aconteceu na primeira versão.
 */
function solto(rotulo: string, tarefa: () => Promise<unknown>): void {
  void comVagaPython(rotulo, tarefa).catch(() => undefined);
}

beforeEach(() => {
  _resetVagas();
});

afterEach(() => {
  _resetVagas();
  vi.useRealTimers();
});

describe("comVagaPython", () => {
  it("deixa passar até o limite sem esperar", async () => {
    const a = represa();
    const b = represa();
    solto("a", () => a.p);
    solto("b", () => b.p);
    await tick();
    expect(estadoDasVagas()).toMatchObject({ emUso: 2, naFila: 0, limite: 2 });
    a.liberar();
    b.liberar();
  });

  it("segura o excedente na fila em vez de spawnar", async () => {
    const a = represa();
    const b = represa();
    let terceiraRodou = false;
    solto("a", () => a.p);
    solto("b", () => b.p);
    const c = comVagaPython("c", async () => { terceiraRodou = true; });
    await tick();

    expect(terceiraRodou).toBe(false);
    expect(estadoDasVagas()).toMatchObject({ emUso: 2, naFila: 1 });

    a.liberar();
    await c;
    expect(terceiraRodou).toBe(true);
  });

  it("libera a vaga mesmo quando a tarefa rejeita", async () => {
    const a = represa();
    const b = represa();
    solto("a", () => a.p);
    const falha = comVagaPython("b", () => b.p);
    await tick();

    b.falhar(new Error("boom"));
    await expect(falha).rejects.toThrow("boom");

    // Se a rejeição não devolvesse a vaga, o pool vazaria uma a cada erro --
    // e depois de N erros nada mais rodaria.
    expect(estadoDasVagas().emUso).toBe(1);
    a.liberar();
  });

  it("respeita a ordem de chegada", async () => {
    const a = represa();
    const b = represa();
    const ordem: string[] = [];
    solto("a", () => a.p);
    solto("b", () => b.p);
    const p1 = comVagaPython("primeiro", async () => { ordem.push("primeiro"); });
    const p2 = comVagaPython("segundo", async () => { ordem.push("segundo"); });
    await tick();

    a.liberar();
    b.liberar();
    await Promise.all([p1, p2]);
    expect(ordem).toEqual(["primeiro", "segundo"]);
  });

  it("desiste de esperar em vez de pendurar a request para sempre", async () => {
    vi.useFakeTimers();
    const a = represa();
    const b = represa();
    solto("a", () => a.p);
    solto("b", () => b.p);
    const tardia = comVagaPython("tardia", async () => "nunca");

    // A asserção é montada ANTES de avançar o relógio, de propósito. Com fake
    // timers o reject acontece dentro do advance; se o handler só fosse anexado
    // na linha seguinte, o Node marcaria unhandled rejection no checkpoint de
    // microtasks e o vitest falharia o arquivo inteiro -- com os seis testes
    // passando.
    const esperado = expect(tardia).rejects.toThrow(/Sem vaga/);
    await vi.advanceTimersByTimeAsync(30_001);
    await esperado;
    // Saiu da fila: não pode voltar a ocupar vaga quando alguém liberar.
    expect(estadoDasVagas().naFila).toBe(0);
  });

  it("devolve a vaga quando a tarefa estoura o prazo de posse", async () => {
    vi.useFakeTimers();
    const travada = represa();
    solto("travada", () => travada.p);
    await vi.advanceTimersByTimeAsync(1);
    expect(estadoDasVagas().emUso).toBe(1);

    // É o caso do routes/chart.ts, que spawna sem setTimeout: sem este prazo,
    // um processo pendurado prenderia a vaga para sempre.
    await vi.advanceTimersByTimeAsync(150_001);
    expect(estadoDasVagas().emUso).toBe(0);

    // E quando a tarefa finalmente termina, não libera uma segunda vez.
    travada.liberar();
    await vi.advanceTimersByTimeAsync(1);
    expect(estadoDasVagas().emUso).toBe(0);
  });
});
