/**
 * Cache por TTL responde "já calculei isso?". Não responde "alguém já está
 * calculando isso agora?" -- e entre o começo e o fim de uma busca de 3s o
 * cache continua vazio, então toda request que chega nessa janela abre o seu
 * próprio interpretador.
 *
 * Medido em produção 04/08, no pico de 10 subprocessos simultâneos:
 *   porRotulo: { agent.get_quotes: 2, agent.get_market_alerts_snapshot: 2, ... }
 * com dois GET /api/market-alerts idênticos em voo (ids 72 e 78), 9s cada.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../logger", () => ({
  logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
}));

const { coalescer, emVooAgora, _resetEmVoo } = await import("../em-voo");

/** Promise que só resolve quando o teste mandar. */
function controlada<T>() {
  let resolver!: (v: T) => void;
  let rejeitar!: (e: unknown) => void;
  const promessa = new Promise<T>((res, rej) => { resolver = res; rejeitar = rej; });
  return { promessa, resolver, rejeitar };
}

beforeEach(() => { _resetEmVoo(); });

describe("coalescer", () => {
  it("roda a tarefa UMA vez para chamadas simultâneas com a mesma chave", async () => {
    const tarefa = vi.fn(() => Promise.resolve("dados"));

    const [a, b, c] = await Promise.all([
      coalescer("k", tarefa),
      coalescer("k", tarefa),
      coalescer("k", tarefa),
    ]);

    expect(tarefa).toHaveBeenCalledTimes(1);
    expect([a, b, c]).toEqual(["dados", "dados", "dados"]);
  });

  it("chaves diferentes não se misturam", async () => {
    const t1 = vi.fn(() => Promise.resolve(1));
    const t2 = vi.fn(() => Promise.resolve(2));

    const [a, b] = await Promise.all([coalescer("a", t1), coalescer("b", t2)]);

    expect(a).toBe(1);
    expect(b).toBe(2);
    expect(t1).toHaveBeenCalledTimes(1);
    expect(t2).toHaveBeenCalledTimes(1);
  });

  it("depois que a busca termina, a próxima roda de novo", async () => {
    // Isto NÃO é cache: coalescer só junta o que é simultâneo. Quem cuida de
    // "não refazer tão cedo" continua sendo o TTL de cada rota.
    const tarefa = vi.fn(() => Promise.resolve("x"));

    await coalescer("k", tarefa);
    await coalescer("k", tarefa);

    expect(tarefa).toHaveBeenCalledTimes(2);
  });

  it("a rejeição é compartilhada por quem entrou de carona", async () => {
    // Quem pegou carona teria falhado do mesmo jeito rodando sozinho.
    const { promessa, rejeitar } = controlada<string>();
    const tarefa = vi.fn(() => promessa);

    const p1 = coalescer("k", tarefa);
    const p2 = coalescer("k", tarefa);
    rejeitar(new Error("yfinance caiu"));

    await expect(p1).rejects.toThrow("yfinance caiu");
    await expect(p2).rejects.toThrow("yfinance caiu");
    expect(tarefa).toHaveBeenCalledTimes(1);
  });

  it("uma falha não envenena a chave -- a próxima tentativa roda", async () => {
    const tarefa = vi
      .fn()
      .mockRejectedValueOnce(new Error("primeira falhou"))
      .mockResolvedValueOnce("segunda deu certo");

    await expect(coalescer("k", tarefa)).rejects.toThrow("primeira falhou");
    await expect(coalescer("k", tarefa)).resolves.toBe("segunda deu certo");
  });

  it("libera a chave ao terminar, com sucesso ou erro", async () => {
    const ok = controlada<string>();
    const p1 = coalescer("ok", () => ok.promessa);
    const ruim = controlada<string>();
    const p2 = coalescer("ruim", () => ruim.promessa);
    expect(emVooAgora()).toBe(2);

    ok.resolver("pronto");
    ruim.rejeitar(new Error("falhou"));
    await p1;
    await expect(p2).rejects.toThrow();

    expect(emVooAgora()).toBe(0);
  });

  it("tarefa que lança de forma síncrona vira rejeição, sem prender a chave", async () => {
    const explode = () => { throw new Error("boom"); };

    await expect(coalescer("k", explode)).rejects.toThrow("boom");
    expect(emVooAgora()).toBe(0);

    // E a chave continua utilizável depois.
    await expect(coalescer("k", () => Promise.resolve("ok"))).resolves.toBe("ok");
  });

  it("quem chega no meio da busca ainda pega o resultado dela", async () => {
    // O caso real: request 2 chega 200ms depois da request 1, com o cache
    // ainda vazio porque a 1 não terminou.
    const { promessa, resolver } = controlada<string>();
    const tarefa = vi.fn(() => promessa);

    const primeira = coalescer("k", tarefa);
    await new Promise((r) => setTimeout(r, 5));
    const segunda = coalescer("k", tarefa);

    resolver("mesma resposta");

    expect(await primeira).toBe("mesma resposta");
    expect(await segunda).toBe("mesma resposta");
    expect(tarefa).toHaveBeenCalledTimes(1);
  });

  // ── idade máxima da carona ────────────────────────────────────────────────
  //
  // "Quem entrou de carona teria falhado do mesmo jeito rodando sozinho" só
  // vale para carona em trabalho NOVO. Produção 18/08/2026: a conexão caiu aos
  // 56s, o usuário clicou de novo, o segundo clique colou no trabalho antigo e
  // morreu com ele no teto de 150s. O log mostrou o absurdo -- uma requisição
  // de 68s morrendo por um timeout de 150s -- quando 58s bastavam.

  it("retardatário NÃO entra de carona em busca velha demais", async () => {
    vi.useFakeTimers();
    try {
      const { promessa, resolver } = controlada<string>();
      const primeiraTarefa = vi.fn(() => promessa);
      const segundaTarefa = vi.fn(() => Promise.resolve("nova"));

      const primeira = coalescer("k", primeiraTarefa, 1_000);
      vi.setSystemTime(Date.now() + 5_000);          // busca envelheceu

      const segunda = coalescer("k", segundaTarefa, 1_000);

      expect(segundaTarefa).toHaveBeenCalledTimes(1);  // rodou por fora
      expect(await segunda).toBe("nova");

      resolver("velha");
      expect(await primeira).toBe("velha");            // a antiga segue dona da chave
    } finally {
      vi.useRealTimers();
    }
  });

  it("dentro da idade máxima a carona continua valendo", async () => {
    vi.useFakeTimers();
    try {
      const { promessa, resolver } = controlada<string>();
      const tarefa = vi.fn(() => promessa);

      const primeira = coalescer("k", tarefa, 10_000);
      vi.setSystemTime(Date.now() + 1_000);           // ainda nova
      const segunda = coalescer("k", tarefa, 10_000);

      expect(tarefa).toHaveBeenCalledTimes(1);
      resolver("mesma");
      expect(await primeira).toBe("mesma");
      expect(await segunda).toBe("mesma");
    } finally {
      vi.useRealTimers();
    }
  });

  it("sem idadeMaxMs o comportamento antigo é preservado", async () => {
    // Os outros consumidores (chart, market_alerts) não passam o parâmetro e
    // não podem mudar de comportamento por causa desta rota.
    vi.useFakeTimers();
    try {
      const { promessa, resolver } = controlada<string>();
      const tarefa = vi.fn(() => promessa);

      const primeira = coalescer("k", tarefa);
      vi.setSystemTime(Date.now() + 600_000);         // dez minutos depois
      const segunda = coalescer("k", tarefa);

      expect(tarefa).toHaveBeenCalledTimes(1);        // ainda pega carona
      resolver("mesma");
      expect(await primeira).toBe("mesma");
      expect(await segunda).toBe("mesma");
    } finally {
      vi.useRealTimers();
    }
  });

  it("o retardatário que roda por fora não rouba nem apaga a chave alheia", async () => {
    // Se ele apagasse a entrada ao terminar, a busca original ficaria órfã e a
    // próxima chamada abriria uma TERCEIRA execução do mesmo trabalho.
    vi.useFakeTimers();
    try {
      const { promessa, resolver } = controlada<string>();
      const primeira = coalescer("k", () => promessa, 1_000);
      vi.setSystemTime(Date.now() + 5_000);

      await coalescer("k", () => Promise.resolve("por fora"), 1_000);

      expect(emVooAgora()).toBe(1);                   // a original continua lá
      resolver("original");
      await primeira;
      expect(emVooAgora()).toBe(0);                   // e só ela limpa
    } finally {
      vi.useRealTimers();
    }
  });
});
