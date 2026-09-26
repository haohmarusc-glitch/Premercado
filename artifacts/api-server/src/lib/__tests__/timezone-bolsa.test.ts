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
import { dataDaBolsa, minutosDoDiaNaBolsa, pregaoEncerrado } from "../timezone";

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

describe("pregaoEncerrado — a opção 'confirmar no fechamento'", () => {
  it("antes das 16:00 ET, não", () => {
    // 19:59Z = 15:59 ET em EDT.
    expect(pregaoEncerrado(new Date("2026-09-25T19:59:00Z"))).toBe(false);
  });

  it("às 16:00 ET, sim", () => {
    expect(pregaoEncerrado(new Date("2026-09-25T20:00:00Z"))).toBe(true);
  });

  it("no horário padrão o corte acompanha o fuso", () => {
    // Em EST (UTC-5), 16:00 ET = 21:00Z. Um offset fixo de -4 diria que 20:00Z
    // já é fechamento, e o alerta de confirmação avaliaria com o pregão aberto
    // -- exatamente o rompimento falso que a opção existe para evitar.
    expect(pregaoEncerrado(new Date("2026-01-15T20:00:00Z"))).toBe(false);
    expect(pregaoEncerrado(new Date("2026-01-15T21:00:00Z"))).toBe(true);
  });

  it("de madrugada em ET, ainda é 'antes do fechamento' do dia novo", () => {
    // 05:00Z de 26/09 = 01:00 ET do dia 26. Não é "depois das 16h" de dia
    // nenhum: um alerta de confirmação não pode disparar na madrugada com o dado
    // do dia anterior. O guarda de data (rvolData) é o que fecha essa porta;
    // este teste fixa que o de hora não a abre.
    expect(pregaoEncerrado(new Date("2026-09-26T05:00:00Z"))).toBe(false);
  });
});

describe("minutosDoDiaNaBolsa", () => {
  it("conta da meia-noite ET", () => {
    expect(minutosDoDiaNaBolsa(new Date("2026-09-25T13:30:00Z"))).toBe(9 * 60 + 30);
    expect(minutosDoDiaNaBolsa(new Date("2026-09-25T20:00:00Z"))).toBe(16 * 60);
  });

  it("meia-noite ET é 0, não 24*60", () => {
    // `hour12: false` no Intl produz "24" em alguns runtimes para a
    // meia-noite; se isso vazasse, `pregaoEncerrado` diria true às 00:00 ET.
    expect(minutosDoDiaNaBolsa(new Date("2026-09-25T04:00:00Z"))).toBe(0);
    expect(pregaoEncerrado(new Date("2026-09-25T04:00:00Z"))).toBe(false);
  });
});
