/**
 * Uma chamada de API, um lançamento no livro de gastos.
 *
 * A Análise com IA devolve o mesmo texto para mais de uma requisição por dois
 * caminhos -- o cache por TTL e a carona do coalescer -- e o `usage` volta
 * junto nos dois, porque a tela mostra o que a análise custou. O registro de
 * gasto lia esse `usage` e gravava uma linha nova em agent_runs, para uma
 * chamada de API que nunca aconteceu.
 *
 * Medido em produção (19/08/2026), duas linhas com 5299 tokens de saída e
 * US$ 0,062852 cada, terminando no MESMO instante (17:26:31.7). A tela Gastos
 * com IA superestimava.
 *
 * A marca de "houve gasto" é feita DENTRO da closure que o coalescer executa,
 * porque só ela distingue quem trabalhou de quem pegou carona. Estes testes
 * exercitam o coalescer de verdade -- é o mecanismo que sustenta a regra, e
 * dublá-lo testaria a dublagem.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../logger", () => ({
  logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
}));

const { coalescer, _resetEmVoo } = await import("../em-voo");

interface Execucao { gastoNovo: boolean }

function controlada<T>() {
  let resolver!: (v: T) => void;
  let rejeitar!: (e: unknown) => void;
  const promessa = new Promise<T>((res, rej) => { resolver = res; rejeitar = rej; });
  return { promessa, resolver, rejeitar };
}

/** O mesmo desenho da rota: a closure marca, o chamador lê. */
function rodar(chave: string, exec: Execucao, tarefa: () => Promise<string>, idadeMaxMs?: number) {
  return coalescer(chave, () => { exec.gastoNovo = true; return tarefa(); }, idadeMaxMs);
}

beforeEach(() => { _resetEmVoo(); });

describe("gasto contabilizado uma vez só", () => {
  it("quem pega carona não marca gasto", async () => {
    const { promessa, resolver } = controlada<string>();
    const dono: Execucao = { gastoNovo: false };
    const carona: Execucao = { gastoNovo: false };

    const p1 = rodar("k", dono, () => promessa);
    const p2 = rodar("k", carona, () => promessa);
    resolver("analise");

    expect(await p1).toBe("analise");
    expect(await p2).toBe("analise");
    // O texto é o mesmo para os dois; o gasto, não.
    expect(dono.gastoNovo).toBe(true);
    expect(carona.gastoNovo).toBe(false);
  });

  it("dez caronas produzem UM gasto", async () => {
    const { promessa, resolver } = controlada<string>();
    const execs = Array.from({ length: 10 }, () => ({ gastoNovo: false }));
    const ps = execs.map((e) => rodar("k", e, () => promessa));
    resolver("analise");
    await Promise.all(ps);
    expect(execs.filter((e) => e.gastoNovo)).toHaveLength(1);
  });

  it("carona também não marca gasto quando a análise FALHA", async () => {
    // Contar a falha de quem não spawnou nada inflaria a taxa de erro do
    // painel com execuções que nunca existiram.
    const { promessa, rejeitar } = controlada<string>();
    const dono: Execucao = { gastoNovo: false };
    const carona: Execucao = { gastoNovo: false };

    const p1 = rodar("k", dono, () => promessa);
    const p2 = rodar("k", carona, () => promessa);
    rejeitar(new Error("timeout"));

    await expect(p1).rejects.toThrow("timeout");
    await expect(p2).rejects.toThrow("timeout");
    expect(dono.gastoNovo).toBe(true);
    expect(carona.gastoNovo).toBe(false);
  });

  it("retardatário que roda POR FORA marca gasto próprio", async () => {
    // Carona velha demais roda sozinha (ver idadeMaxMs em em-voo.ts): ela
    // spawna e paga de verdade, então TEM que aparecer no livro. É o inverso
    // do caso acima, e confundir os dois trocaria um erro por outro.
    vi.useFakeTimers();
    try {
      const primeira = controlada<string>();
      const segunda = controlada<string>();
      const dono: Execucao = { gastoNovo: false };
      const tardio: Execucao = { gastoNovo: false };

      const p1 = rodar("k", dono, () => primeira.promessa, 1_000);
      vi.advanceTimersByTime(5_000);
      const p2 = rodar("k", tardio, () => segunda.promessa, 1_000);

      primeira.resolver("primeira");
      segunda.resolver("segunda");
      expect(await p1).toBe("primeira");
      expect(await p2).toBe("segunda");
      expect(dono.gastoNovo).toBe(true);
      expect(tardio.gastoNovo).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("chaves diferentes são trabalhos diferentes", async () => {
    const a: Execucao = { gastoNovo: false };
    const b: Execucao = { gastoNovo: false };
    await Promise.all([
      rodar("ADI", a, async () => "a"),
      rodar("NVDA", b, async () => "b"),
    ]);
    expect(a.gastoNovo).toBe(true);
    expect(b.gastoNovo).toBe(true);
  });
});
