/**
 * O corpo enviado para /earnings-reaction/ia não pode carregar os `events`.
 *
 * Incidente de 20/08/2026: a tela mandava os results INTEIROS (eventos +
 * trajetórias diárias), e a cesta padrão do dia deu 107.650 bytes contra o
 * limite de ~100KB (102.400) do express.json global -- 413 que o handler
 * genérico ainda transformava em 500 "Internal server error". O servidor
 * descarta os eventos de qualquer jeito (o prompt Python usa só ticker,
 * summary, error e stale), então o peso todo era transporte de lixo.
 *
 * Estes testes fixam o formato enxuto E a propriedade que resolve o
 * incidente: o tamanho do corpo não cresce com o número de eventos.
 */
import { describe, it, expect } from "vitest";
import { payloadDaInterpretacao } from "../pages/earnings-reaction";

const evento = (i: number) => ({
  earnings_date: `2024-0${(i % 9) + 1}-15`,
  runup_pct: 12.3456789,
  announcement_day: {
    date: "x", gap_pct: 1.23, close_pct: 4.56, intraday_range_pct: 7.89, volume: 123456789,
  },
  next_day: null,
  trajetoria: Array.from({ length: 10 }, (_, d) => ({
    dia: d + 1, date: `2024-01-${d + 10}`, acum_pct: d * 1.1, dia_pct: 0.3,
    bench_pct: 0.1, excesso_pct: 0.2,
  })),
});

const resultCheio = (ticker: string) => ({
  ticker,
  summary: { n_events: 8, close_pct_mean: 1.2 },
  events: Array.from({ length: 8 }, (_, i) => evento(i)),
}) as never;

describe("payloadDaInterpretacao", () => {
  it("derruba os events e mantém só o que o prompt usa", () => {
    const p = payloadDaInterpretacao([resultCheio("NVDA")], 8, "SMH") as {
      results: Record<string, unknown>[]; lookback: number; benchmark: string;
    };
    expect(p.results[0]).toEqual({ ticker: "NVDA", summary: { n_events: 8, close_pct_mean: 1.2 } });
    expect(p.results[0]).not.toHaveProperty("events");
    expect(p.lookback).toBe(8);
    expect(p.benchmark).toBe("SMH");
  });

  it("preserva error e stale quando existem; omite stale falso", () => {
    const p = payloadDaInterpretacao([
      { ticker: "XYZ", error: "sem histórico" } as never,
      { ticker: "SKHY", summary: { n_events: 3 }, stale: true } as never,
      { ticker: "ARM", summary: { n_events: 5 }, stale: false } as never,
    ], 8, "SMH") as { results: Record<string, unknown>[] };
    expect(p.results[0]).toEqual({ ticker: "XYZ", error: "sem histórico" });
    expect(p.results[1]).toEqual({ ticker: "SKHY", summary: { n_events: 3 }, stale: true });
    // `stale: false` fora do corpo: o Python só distingue presente/ausente, e
    // mandar o falso é byte que não informa nada.
    expect(p.results[2]).toEqual({ ticker: "ARM", summary: { n_events: 5 } });
  });

  it("o tamanho do corpo não cresce com o número de eventos", () => {
    // A propriedade do incidente. Não fixamos "cabe em 100KB" (frágil a
    // mudança de fixture); fixamos a causa: com o dobro de eventos por
    // ticker, o corpo enviado fica IDÊNTICO.
    const cesta5 = ["NVDA", "SMCI", "AVGO", "SKHY", "ARM"].map(resultCheio);
    const corpo = JSON.stringify(payloadDaInterpretacao(cesta5, 8, "SMH"));

    const cestaDobrada = cesta5.map((r) => ({
      ...(r as object),
      events: [...(r as { events: unknown[] }).events, ...(r as { events: unknown[] }).events],
    })) as never[];
    const corpoDobrado = JSON.stringify(payloadDaInterpretacao(cestaDobrada, 8, "SMH"));

    expect(corpoDobrado).toBe(corpo);
    // E a ordem de grandeza que faz o 413 ser impossível: o cru da mesma
    // cesta é várias vezes maior que o enviado.
    const cru = JSON.stringify({ results: cesta5, lookback: 8, benchmark: "SMH" });
    expect(corpo.length * 5).toBeLessThan(cru.length);
  });

  it("cesta vazia produz corpo válido em vez de quebrar", () => {
    expect(payloadDaInterpretacao([], 8, "SMH")).toEqual({
      results: [], lookback: 8, benchmark: "SMH",
    });
  });
});
