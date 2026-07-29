/**
 * Testes do núcleo matemático compartilhado do Painel de Cenários
 * (@workspace/scenario-math) -- vivem aqui (não num pacote de teste próprio)
 * porque é o api-server que também consome esse pacote (scenario-alert-checker.ts),
 * e o repo já tem vitest configurado neste workspace.
 */
import { describe, expect, it } from "vitest";
import {
  computeScenarioMetrics, volComSalto, probEmpateIndividual, diasAteAlvo,
  pctConfirmacao, cicloBateu,
  type ScenarioPosition,
} from "@workspace/scenario-math";

function pos(overrides: Partial<ScenarioPosition> = {}): ScenarioPosition {
  return {
    t: "NVDA", nome: "Nvidia", value: 1000, cost: 1000, vol: 0.5, beta: 1.1,
    evento: "—", eventoISO: null, jumpStdPct: null,
    ...overrides,
  };
}

describe("computeScenarioMetrics", () => {
  it("todas as posições vendidas: caixa = total, risco = 0, empatar = 100%", () => {
    const lista = [pos({ t: "NVDA", value: 1000, cost: 1000 }), pos({ t: "SMCI", value: 500, cost: 500 })];
    const dataAlvo = new Date(Date.now() + 60 * 86400000);
    const m = computeScenarioMetrics(lista, { NVDA: true, SMCI: true }, {}, 0, 1, dataAlvo);
    expect(m.caixa).toBe(1500);
    expect(m.risco).toBe(0);
    expect(m.pEmpate).toBe(1);
    expect(m.pQueda).toBe(0);
  });

  it("nenhuma posição vendida: caixa = 0, risco = total", () => {
    const lista = [pos({ t: "NVDA", value: 1000, cost: 1000 })];
    const dataAlvo = new Date(Date.now() + 60 * 86400000);
    const m = computeScenarioMetrics(lista, {}, {}, 0, 1, dataAlvo);
    expect(m.caixa).toBe(0);
    expect(m.risco).toBe(1000);
  });

  it("override manual em `valores` prevalece sobre o value original", () => {
    const lista = [pos({ t: "NVDA", value: 1000, cost: 1000 })];
    const dataAlvo = new Date(Date.now() + 60 * 86400000);
    const m = computeScenarioMetrics(lista, {}, { NVDA: 0 }, 0, 1, dataAlvo);
    expect(m.risco).toBe(0);
    expect(m.valorTotalHoje).toBe(0);
    // risco=0 não deve gerar NaN/Infinity em nenhum campo
    expect(Number.isFinite(m.pEmpate)).toBe(true);
    expect(Number.isFinite(m.central)).toBe(true);
  });

  it("slider de setor no extremo negativo não gera central negativo (piso em zero)", () => {
    const lista = [pos({ t: "SMCI", value: 1000, cost: 1000, beta: 1.6 })];
    const dataAlvo = new Date(Date.now() + 60 * 86400000);
    const m = computeScenarioMetrics(lista, {}, {}, -100, 1, dataAlvo); // -100% de movimento setorial
    expect(m.central).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(m.central)).toBe(true);
  });
});

describe("volComSalto", () => {
  const dataAlvo = new Date("2026-09-01T00:00:00");
  const agora = new Date("2026-08-01T00:00:00");

  it("sem jumpStdPct, retorna o vol original", () => {
    expect(volComSalto(0.5, null, "2026-08-15", dataAlvo, 0.1, agora)).toBe(0.5);
  });

  it("sem eventoISO, retorna o vol original", () => {
    expect(volComSalto(0.5, 8, null, dataAlvo, 0.1, agora)).toBe(0.5);
  });

  it("evento fora da janela (depois da data-alvo), retorna o vol original", () => {
    expect(volComSalto(0.5, 8, "2026-10-15", dataAlvo, 0.1, agora)).toBe(0.5);
  });

  it("evento no passado (antes de agora), retorna o vol original", () => {
    expect(volComSalto(0.5, 8, "2026-07-01", dataAlvo, 0.1, agora)).toBe(0.5);
  });

  it("evento dentro da janela, aumenta o vol efetivo", () => {
    const efetivo = volComSalto(0.5, 8, "2026-08-15", dataAlvo, 0.1, agora);
    expect(efetivo).toBeGreaterThan(0.5);
    expect(Number.isFinite(efetivo)).toBe(true);
  });
});

describe("probEmpateIndividual", () => {
  const dataAlvo = new Date(Date.now() + 60 * 86400000);

  it("custo <= 0 sempre retorna 100% (já empatou)", () => {
    expect(probEmpateIndividual(pos({ cost: 0 }), 1000, dataAlvo, 60 / 365, 0, 1)).toBe(1);
  });

  it("valor atual <= 0 retorna null (indeterminado)", () => {
    expect(probEmpateIndividual(pos(), 0, dataAlvo, 60 / 365, 0, 1)).toBeNull();
  });

  it("caso normal retorna um número entre 0 e 1", () => {
    const p = probEmpateIndividual(pos({ value: 1000, cost: 1000, vol: 0.5 }), 1000, dataAlvo, 60 / 365, 0, 1);
    expect(p).not.toBeNull();
    expect(p!).toBeGreaterThanOrEqual(0);
    expect(p!).toBeLessThanOrEqual(1);
  });
});

describe("diasAteAlvo", () => {
  it("nunca retorna menos que 1 (mesmo com data-alvo no passado)", () => {
    const passado = new Date(Date.now() - 10 * 86400000);
    expect(diasAteAlvo(passado)).toBe(1);
  });

  it("arredonda corretamente pra uma data futura", () => {
    const futuro = new Date(Date.now() + 30 * 86400000);
    expect(diasAteAlvo(futuro)).toBeGreaterThanOrEqual(29);
    expect(diasAteAlvo(futuro)).toBeLessThanOrEqual(31);
  });
});

describe("pctConfirmacao", () => {
  it("sem histórico, retorna null", () => {
    expect(pctConfirmacao([], 50)).toBeNull();
  });

  it("todos os dias acima do limiar: 100%", () => {
    const snaps = [{ pEmpate: 0.6 }, { pEmpate: 0.7 }, { pEmpate: 0.55 }];
    expect(pctConfirmacao(snaps, 50)).toBe(100);
  });

  it("nenhum dia acima do limiar: 0%", () => {
    const snaps = [{ pEmpate: 0.2 }, { pEmpate: 0.3 }];
    expect(pctConfirmacao(snaps, 50)).toBe(0);
  });

  it("mistura de dias acima/abaixo do limiar calcula a fração correta", () => {
    const snaps = [{ pEmpate: 0.6 }, { pEmpate: 0.4 }, { pEmpate: 0.6 }, { pEmpate: 0.4 }];
    expect(pctConfirmacao(snaps, 50)).toBe(50);
  });

  it("pEmpate igual ao limiar conta como confirmado (>=)", () => {
    expect(pctConfirmacao([{ pEmpate: 0.5 }], 50)).toBe(100);
  });
});

describe("cicloBateu", () => {
  it("valor final >= custo total: bateu", () => {
    expect(cicloBateu(1000, 1000)).toBe(true);
    expect(cicloBateu(1200, 1000)).toBe(true);
  });

  it("valor final < custo total: não bateu", () => {
    expect(cicloBateu(800, 1000)).toBe(false);
  });
});
