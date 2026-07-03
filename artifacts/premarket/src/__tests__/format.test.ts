import { describe, it, expect } from "vitest";
import { formatDate, formatDateTime, todayBRTDateString } from "../lib/format";

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
