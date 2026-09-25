/**
 * `dataDaBolsa` decide se um rvol é "de hoje". Errar o dia por uma hora
 * significa avaliar um alerta composto contra o pregão de ontem, com um número
 * que chega completo e plausível.
 *
 * Os casos são os dois lados do horário de verão de Nova York, que é
 * exatamente o que um offset fixo (a convenção usada para Brasília, que não
 * observa DST) erraria em metade do calendário.
 */
import { describe, it, expect } from "vitest";
import { dataDaBolsa } from "../timezone";

describe("dataDaBolsa", () => {
  it("no horário de verão (EDT, UTC-4) a virada é 04:00Z", () => {
    // 25/09/2026 é EDT. 03:59Z ainda é dia 24 em Nova York.
    expect(dataDaBolsa(new Date("2026-09-25T03:59:00Z"))).toBe("2026-09-24");
    expect(dataDaBolsa(new Date("2026-09-25T04:00:00Z"))).toBe("2026-09-25");
  });

  it("no horário padrão (EST, UTC-5) a virada é 05:00Z", () => {
    // Janeiro é EST. Com offset fixo de -4 o dia viraria uma hora cedo.
    expect(dataDaBolsa(new Date("2026-01-15T04:59:00Z"))).toBe("2026-01-14");
    expect(dataDaBolsa(new Date("2026-01-15T05:00:00Z"))).toBe("2026-01-15");
  });

  it("durante o pregão, a data é a do dia em curso", () => {
    // 16:00Z = 12:00 ET em EDT, meio de sessão.
    expect(dataDaBolsa(new Date("2026-09-25T16:00:00Z"))).toBe("2026-09-25");
    // 22:00Z = 18:00 ET, pós-mercado do MESMO dia -- não do seguinte.
    expect(dataDaBolsa(new Date("2026-09-25T22:00:00Z"))).toBe("2026-09-25");
  });

  it("o formato é YYYY-MM-DD, comparável com o que o Python emite", () => {
    // `str(datetime.date)` do Python é YYYY-MM-DD. A comparação em
    // alert-conditions é de string, então o formato faz parte do contrato.
    expect(dataDaBolsa(new Date("2026-03-09T16:00:00Z"))).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
