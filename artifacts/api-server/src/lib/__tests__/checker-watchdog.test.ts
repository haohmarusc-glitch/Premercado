import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const avisos: { campos: Record<string, unknown>; msg: string }[] = [];
const erros: { campos: Record<string, unknown>; msg: string }[] = [];
let linhas: unknown[] = [];

vi.mock("../logger", () => ({
  logger: {
    info: () => {},
    warn: (campos: Record<string, unknown>, msg: string) => avisos.push({ campos, msg }),
    error: (campos: Record<string, unknown>, msg: string) => erros.push({ campos, msg }),
  },
}));

vi.mock("@workspace/db", () => ({
  db: { execute: async () => ({ rows: linhas }) },
}));

const { verificarUltimoCiclo } = await import("../checker-watchdog");

const AGORA = new Date("2026-08-05T12:00:00Z").getTime();
const MIN = 60_000;

beforeEach(() => {
  avisos.length = 0;
  erros.length = 0;
  linhas = [];
});

afterEach(() => {
  delete process.env["RUN_BACKGROUND_CHECKERS"];
});

describe("verificarUltimoCiclo", () => {
  it("silencia quando o último ciclo é recente", async () => {
    linhas = [{ last_cycle_at: new Date(AGORA - 6 * MIN).toISOString() }];
    const r = await verificarUltimoCiclo(AGORA);
    expect(r.alarmado).toBe(false);
    expect(erros).toHaveLength(0);
    expect(avisos).toHaveLength(0);
  });

  it("tolera alguns ciclos perdidos antes de alarmar", async () => {
    // 19 min = três gatilhos de 5 min falhados. Um 409 do agente diário ou um
    // atraso do agendador cabem aqui e não merecem alarme.
    linhas = [{ last_cycle_at: new Date(AGORA - 19 * MIN).toISOString() }];
    expect((await verificarUltimoCiclo(AGORA)).alarmado).toBe(false);
  });

  it("alarma como ERROR quando o gatilho externo parou", async () => {
    linhas = [{ last_cycle_at: new Date(AGORA - 45 * MIN).toISOString() }];
    const r = await verificarUltimoCiclo(AGORA);
    expect(r.alarmado).toBe(true);
    expect(r.idadeMs).toBe(45 * MIN);
    expect(erros).toHaveLength(1);
    expect(erros[0].msg).toContain("alertas NÃO estão rodando");
  });

  it("avisa (sem ERROR) quando nunca houve ciclo", async () => {
    // Estado esperado logo depois de um deploy -- vira ERROR só quando o
    // limite passar, o que o caso acima já cobre.
    linhas = [{ last_cycle_at: null }];
    const r = await verificarUltimoCiclo(AGORA);
    expect(r.alarmado).toBe(true);
    expect(r.idadeMs).toBeNull();
    expect(erros).toHaveLength(0);
    expect(avisos).toHaveLength(1);
  });

  it("trata a linha ausente como 'nunca rodou' em vez de estourar", async () => {
    linhas = [];
    const r = await verificarUltimoCiclo(AGORA);
    expect(r.alarmado).toBe(true);
    expect(r.idadeMs).toBeNull();
  });

  it("aceita Date além de string (o driver pode devolver os dois)", async () => {
    linhas = [{ last_cycle_at: new Date(AGORA - 45 * MIN) }];
    expect((await verificarUltimoCiclo(AGORA)).alarmado).toBe(true);
  });
});
