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
import {
  diaDaSemanaNaBolsa, notaDeConfirmacaoAtrasada, pregaoSeguinteJaAbriu,
} from "@workspace/alertas";

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

describe("pregaoSeguinteJaAbriu — o fim da confirmação atrasada", () => {
  it("fim de semana não abre, então a janela da sexta atravessa", () => {
    // O caso que motivou a feature: sábado e domingo inteiros ainda confirmam
    // o fechamento de sexta.
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-03T14:00:00Z"))).toBe(false); // sáb 10:00 ET
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-03T23:00:00Z"))).toBe(false); // sáb 19:00 ET
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-04T18:00:00Z"))).toBe(false); // dom 14:00 ET
  });

  it("segunda antes de 09:30 ET ainda não abriu", () => {
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-05T12:00:00Z"))).toBe(false); // 08:00 ET
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-05T13:29:00Z"))).toBe(false); // 09:29 ET
  });

  it("às 09:30 ET de um dia de semana, abriu", () => {
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-05T13:30:00Z"))).toBe(true);  // 09:30 ET
    expect(pregaoSeguinteJaAbriu(new Date("2026-10-05T20:00:00Z"))).toBe(true);  // 16:00 ET
  });

  it("no horário padrão o corte acompanha o fuso", () => {
    // Em EST (UTC-5), 09:30 ET = 14:30Z. Offset fixo de -4 abriria a janela uma
    // hora cedo e encerraria a confirmação atrasada antes do tempo.
    expect(pregaoSeguinteJaAbriu(new Date("2026-01-05T14:29:00Z"))).toBe(false);
    expect(pregaoSeguinteJaAbriu(new Date("2026-01-05T14:30:00Z"))).toBe(true);
  });
});

describe("diaDaSemanaNaBolsa", () => {
  it("é o dia em NOVA YORK, não em UTC", () => {
    // Sábado 02:00Z é sexta 22:00 em Nova York. Em UTC o dia já virou; na
    // bolsa, não -- e quem decide a janela é a bolsa.
    expect(diaDaSemanaNaBolsa(new Date("2026-10-03T02:00:00Z"))).toBe(5); // sexta
    expect(diaDaSemanaNaBolsa(new Date("2026-10-03T14:00:00Z"))).toBe(6); // sábado
  });
});

describe("notaDeConfirmacaoAtrasada", () => {
  it("é a frase que o e-mail mostra", () => {
    expect(notaDeConfirmacaoAtrasada("2026-10-02", "2026-10-05"))
      .toBe("Fechamento de sexta (2026-10-02) confirmado no processamento de segunda");
  });

  it("mesma sessão não ganha nota", () => {
    // Confirmação no próprio dia não é atrasada, e a nota só poluiria.
    expect(notaDeConfirmacaoAtrasada("2026-10-02", "2026-10-02")).toBeNull();
  });

  it("a data é lida como dia de bolsa, sem escorregar de fuso", () => {
    // `new Date("2026-10-05")` é meia-noite UTC, que em Nova York ainda é o dia
    // 4 (domingo). Daí o T12:00:00Z na implementação: 05/10/2026 é SEGUNDA.
    expect(notaDeConfirmacaoAtrasada("2026-10-03", "2026-10-05"))
      .toContain("Fechamento de sábado");
    expect(notaDeConfirmacaoAtrasada("2026-10-03", "2026-10-05"))
      .toContain("processamento de segunda");
  });
});
