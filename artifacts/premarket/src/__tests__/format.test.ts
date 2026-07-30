import { describe, it, expect } from "vitest";
import { formatDate, formatDateTime, todayBRTDateString, daysUntilBRT } from "../lib/format";

describe("formatDate", () => {
  it("formata data ISO corretamente", () => {
    // Usa T12:00 para evitar ambiguidade de timezone (UTC midnight pode virar dia anterior)
    expect(formatDate("2024-01-15T12:00:00")).toBe("Jan 15, 2024");
  });

  it("retorna string original quando data é inválida", () => {
    expect(formatDate("data-invalida")).toBe("data-invalida");
  });
});

describe("formatDateTime", () => {
  it("formata data e hora corretamente", () => {
    const result = formatDateTime("2024-06-09T14:30:00");
    expect(result).toMatch(/Jun 09, 2024 14:30/);
  });

  it("retorna string original quando data é inválida", () => {
    expect(formatDateTime("nao-e-data")).toBe("nao-e-data");
  });
});

describe("todayBRTDateString", () => {
  it("usa o dia em horário de Brasília, não o dia UTC, no fim da noite BRT", () => {
    // 2026-07-04T01:00:00Z = 22:00 BRT em 2026-07-03 (ainda dia 3 pro usuário)
    const now = new Date("2026-07-04T01:00:00Z");
    expect(todayBRTDateString(now)).toBe("2026-07-03");
  });

  it("bate com a data UTC no meio do dia", () => {
    const now = new Date("2026-07-03T15:00:00Z");
    expect(todayBRTDateString(now)).toBe("2026-07-03");
  });
});

describe("daysUntilBRT", () => {
  it("conta os dias corretamente pra uma data futura", () => {
    const now = new Date("2026-07-30T19:27:00Z"); // 16:27 BRT
    expect(daysUntilBRT("2026-08-10", now)).toBe(11);
  });

  it("conta os dias corretamente pra uma data passada (negativo)", () => {
    const now = new Date("2026-07-30T19:27:00Z");
    expect(daysUntilBRT("2026-07-28", now)).toBe(-2);
  });

  it("retorna 0 pra hoje", () => {
    const now = new Date("2026-07-30T19:27:00Z");
    expect(daysUntilBRT("2026-07-30", now)).toBe(0);
  });

  it("não muda com o fuso horário local de quem está vendo a tela -- só importa o BRT", () => {
    // Mesmo instante, mas se o código usasse `new Date()` local (em vez de
    // todayBRTDateString) pra "hoje", um navegador fora de BRT já bastava pra
    // desalinhar o contador em 1 dia (bug real que motivou este teste).
    const now = new Date("2026-07-30T19:27:00Z");
    const originalTZ = process.env.TZ;
    process.env.TZ = "Pacific/Kiritimati"; // UTC+14 -- já seria "31/07" em Date() local
    try {
      expect(daysUntilBRT("2026-08-10", now)).toBe(11);
      expect(daysUntilBRT("2026-07-28", now)).toBe(-2);
    } finally {
      process.env.TZ = originalTZ;
    }
  });
});
