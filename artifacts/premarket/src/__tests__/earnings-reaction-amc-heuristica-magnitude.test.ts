/**
 * A frase "sinal de que o resultado tende a sair depois do fechamento (AMC)"
 * era inferida comparando qual sessão se moveu MAIS (magnitude) -- mesmo
 * quando o próprio evento já carrega `janela_reacao`, calculado no backend
 * (`_janela_da_reacao`) a partir do HORÁRIO real de divulgação da fonte.
 *
 * Auditoria de 27/08/2026: o backend já tinha abandonado a heurística por
 * magnitude nesse EXATO lugar por seleção-pelo-resultado (comentário em
 * `_janela_da_reacao`: "no momento em que o número seria útil, ninguém sabe
 * qual sessão vai andar mais") -- mas o frontend reintroduzia a mesma
 * heurística nesta tela em vez de usar o campo que o backend já mandava.
 *
 * Rodar: pnpm --filter @workspace/premarket run test -- --run earnings-reaction-amc-heuristica-magnitude
 */
import { describe, it, expect } from "vitest";
import { interpretResult } from "@/pages/earnings-reaction";

function comEventos(events: Record<string, unknown>[]) {
  return {
    ticker: "NVDA",
    summary: { n_events: events.length },
    events,
  } as Parameters<typeof interpretResult>[0];
}

describe("AMC/BMO usa o horário real (janela_reacao), não a magnitude do movimento", () => {
  it("maioria AMC (janela_reacao='seguinte') gera a frase certa, mesmo sem olhar close_pct", () => {
    const events = Array.from({ length: 3 }, () => ({
      janela_reacao: "seguinte",
      // magnitudes de propósito CONTRÁRIAS ao que a heurística antiga leria:
      // o dia do anúncio se move mais que o seguinte, e mesmo assim é AMC.
      announcement_day: { close_pct: -9.0 },
      next_day: { close_pct: 1.0 },
    }));
    const notas = interpretResult(comEventos(events)).join(" ");
    expect(notas).toContain("depois do fechamento (AMC)");
    expect(notas).not.toContain("antes da abertura (BMO)");
    expect(notas).toContain("3 de 3");
  });

  it("maioria BMO (janela_reacao='anuncio') gera a frase certa, mesmo com magnitude contrária", () => {
    const events = Array.from({ length: 3 }, () => ({
      janela_reacao: "anuncio",
      announcement_day: { close_pct: 1.0 },
      next_day: { close_pct: -9.0 },
    }));
    const notas = interpretResult(comEventos(events)).join(" ");
    expect(notas).toContain("antes da abertura (BMO)");
    expect(notas).not.toContain("depois do fechamento (AMC)");
  });

  it("eventos com janela inferida (sem horário confirmado) aparecem na ressalva", () => {
    const events = [
      { janela_reacao: "seguinte", janela_inferida: true },
      { janela_reacao: "seguinte", janela_inferida: false },
      { janela_reacao: "seguinte", janela_inferida: false },
    ];
    const notas = interpretResult(comEventos(events)).join(" ");
    expect(notas).toContain("1 de 3 sem horário confirmado");
  });

  it("sem janela_reacao em nenhum evento, a checagem se cala (não reintroduz a heurística antiga)", () => {
    const events = [
      { announcement_day: { close_pct: -9.0 }, next_day: { close_pct: 1.0 } },
      { announcement_day: { close_pct: 1.0 }, next_day: { close_pct: -9.0 } },
    ];
    const notas = interpretResult(comEventos(events)).join(" ");
    expect(notas).not.toContain("AMC");
    expect(notas).not.toContain("BMO");
  });

  it("empate exato não afirma nenhuma das duas direções", () => {
    const events = [
      { janela_reacao: "seguinte" },
      { janela_reacao: "anuncio" },
    ];
    const notas = interpretResult(comEventos(events)).join(" ");
    expect(notas).not.toContain("AMC)");
    expect(notas).not.toContain("BMO)");
  });
});
