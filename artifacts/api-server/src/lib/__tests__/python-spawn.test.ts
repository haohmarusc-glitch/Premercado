/**
 * O contador existe para responder UMA pergunta em produção: quantos
 * interpretadores Python este processo sobe ao mesmo tempo?
 *
 * A fila (python-queue.ts) serializa os checkers de fundo, mas 13 dos 20 pontos
 * de spawn são rotas HTTP que não passam por ela. Medido no container ocioso:
 * boot de 0,111s e imports de 5,679s. Medido em produção sob carga: 7-12s e
 * 68-84s. A fila não explica essa diferença, porque nunca deixa mais de um
 * checker rodar por vez.
 *
 * O que estes testes travam é a contabilidade em si -- se ela errar, a medida
 * mente, e uma medida que mente é pior que nenhuma.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { EventEmitter } from "events";

const spawnMock = vi.fn();
const logs: { campos: Record<string, unknown>; msg: string }[] = [];

vi.mock("child_process", () => ({ spawn: (...a: unknown[]) => spawnMock(...a) }));
vi.mock("../logger", () => ({
  logger: {
    warn: (campos: Record<string, unknown>, msg: string) => logs.push({ campos, msg }),
    info: (campos: Record<string, unknown>, msg: string) => logs.push({ campos, msg }),
    error: () => {},
  },
}));

const {
  spawnPython,
  pythonVivos,
  consumirPicoPython,
  rotuloDoSpawn,
  _resetPythonSpawn,
} = await import("../python-spawn");

/** Processo falso: só precisa emitir 'close'/'error' como o de verdade. */
function processoFalso(): EventEmitter {
  return new EventEmitter();
}

beforeEach(() => {
  _resetPythonSpawn();
  logs.length = 0;
  spawnMock.mockReset();
  spawnMock.mockImplementation(() => processoFalso());
});

describe("rotuloDoSpawn", () => {
  it("usa o módulo quando o script roda via -m", () => {
    expect(rotuloDoSpawn(["-m", "agent.get_quotes", "NVDA"])).toBe("agent.get_quotes");
  });

  it("usa o nome do arquivo quando roda por caminho", () => {
    expect(rotuloDoSpawn(["/app/src/agent/get_technicals.py"])).toBe("get_technicals.py");
  });

  it("não confunde uma flag com o script", () => {
    expect(rotuloDoSpawn(["-u", "/app/agent/get_risk.py"])).toBe("get_risk.py");
  });

  it("não inventa rótulo quando não há argumento nenhum", () => {
    expect(rotuloDoSpawn([])).toBe("desconhecido");
  });
});

describe("contagem de vivos", () => {
  it("sobe no spawn e desce no close", () => {
    const py = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    expect(pythonVivos()).toBe(1);
    py.emit("close", 0);
    expect(pythonVivos()).toBe(0);
  });

  it("desce também quando o spawn falha sem nunca fechar", () => {
    const py = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    py.emit("error", new Error("ENOENT"));
    expect(pythonVivos()).toBe(0);
  });

  it("não conta duas vezes quando vêm error E close", () => {
    // O caso que faria o contador ficar negativo e a medida virar ficção.
    const py = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    py.emit("error", new Error("morreu"));
    py.emit("close", 1);
    expect(pythonVivos()).toBe(0);
  });

  it("acompanha vários processos simultâneos", () => {
    const a = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    const b = spawnPython("python3", ["-m", "agent.get_chart"]) as unknown as EventEmitter;
    const c = spawnPython("python3", ["/app/get_risk.py"]) as unknown as EventEmitter;
    expect(pythonVivos()).toBe(3);
    b.emit("close", 0);
    expect(pythonVivos()).toBe(2);
    a.emit("close", 0);
    c.emit("close", 0);
    expect(pythonVivos()).toBe(0);
  });
});

describe("aviso de concorrência", () => {
  it("fica calado em 1 e 2, avisa a partir de 3", () => {
    const a = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    const b = spawnPython("python3", ["-m", "agent.get_chart"]) as unknown as EventEmitter;
    expect(logs).toHaveLength(0);

    spawnPython("python3", ["-m", "agent.get_technicals"]);
    const aviso = logs.at(-1);
    expect(aviso?.msg).toBe("Python: vários subprocessos concorrendo");
    expect(aviso?.campos.vivos).toBe(3);
    // Nomear QUEM está concorrendo é o ponto: "3 processos" não diz onde olhar.
    expect(aviso?.campos.porRotulo).toMatchObject({
      "agent.get_quotes": 1,
      "agent.get_chart": 1,
      "agent.get_technicals": 1,
    });
    a.emit("close", 0);
    b.emit("close", 0);
  });

  it("agrupa repetições do mesmo script", () => {
    spawnPython("python3", ["-m", "agent.get_quotes"]);
    spawnPython("python3", ["-m", "agent.get_quotes"]);
    spawnPython("python3", ["-m", "agent.get_quotes"]);
    expect(logs.at(-1)?.campos.porRotulo).toEqual({ "agent.get_quotes": 3 });
  });
});

describe("pico da janela", () => {
  it("guarda o máximo, não o instantâneo", () => {
    // A razão de existir: amostrar de fora quase sempre pega zero.
    const a = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    const b = spawnPython("python3", ["-m", "agent.get_chart"]) as unknown as EventEmitter;
    a.emit("close", 0);
    b.emit("close", 0);
    expect(pythonVivos()).toBe(0);
    expect(consumirPicoPython()).toBe(2);
  });

  it("zera ao ser lido, para a próxima janela ser independente", () => {
    const a = spawnPython("python3", ["-m", "agent.get_quotes"]) as unknown as EventEmitter;
    a.emit("close", 0);
    expect(consumirPicoPython()).toBe(1);
    expect(consumirPicoPython()).toBe(0);
  });

  it("a janela nova começa contando quem já está vivo", () => {
    // Um processo longo (o agente) atravessa janelas: zerar pra 0 esconderia
    // que ele continua ocupando CPU na janela seguinte.
    spawnPython("python3", ["-m", "agent.run_agent"]);
    expect(consumirPicoPython()).toBe(1);
    expect(consumirPicoPython()).toBe(1);
  });
});
