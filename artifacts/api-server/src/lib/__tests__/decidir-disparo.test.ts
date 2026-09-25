/**
 * `decidirDisparo` é a única parte do checker que dá para provar.
 *
 * O resto dele precisa de Postgres, de dois subprocessos Python e de SMTP para
 * existir. A decisão de mandar ou não mandar o e-mail é pura de propósito, e é
 * onde ficam os erros que custam caro: um alerta que dispara em ruído de
 * abertura, um que reenvia a cada cinco minutos, um que fica mudo para sempre.
 */
import { describe, it, expect } from "vitest";
import {
  decidirDisparo, assuntoDoAlertaComposto, avaliarCondicoes, COOLDOWN_MS,
  type AlertaParaDecisao, type Condicao, type RetratoDoTicker,
} from "../alert-conditions";

const HOJE = "2026-09-25";
const AGORA = new Date("2026-09-25T18:00:00Z"); // 14:00 ET, pregão em curso

const AVGO: Condicao[] = [
  { indicator: "price", op: "above", value: 365 },
  { indicator: "rvol", op: "above", value: 1.2 },
];

function alerta(over: Partial<AlertaParaDecisao> = {}): AlertaParaDecisao {
  return {
    conditions: AVGO, indicator: "price", condition: "above",
    thresholdPrice: 365, lastTriggeredAt: null, confirmAtClose: false, ...over,
  };
}

function retrato(over: Partial<RetratoDoTicker> = {}): RetratoDoTicker {
  return {
    ticker: "AVGO", price: 366.2, rvol: 1.35, rvolSignal: "alto",
    rvolData: HOJE, rvolAte: "13:55", ...over,
  };
}

const ctx = (over: Partial<Parameters<typeof decidirDisparo>[2]> = {}) => ({
  agora: AGORA, dataDeHojeNaBolsa: HOJE, pregaoEncerrado: false, ...over,
});

describe("decidirDisparo", () => {
  it("as duas condições batendo: dispara", () => {
    const d = decidirDisparo(alerta(), retrato(), ctx());
    expect(d.disparar).toBe(true);
    expect(d.motivo).toBeNull();
    expect(d.avaliacao.valorPrincipal).toBe(366.2);
  });

  it("uma condição falhando: não dispara, e o motivo é da condição", () => {
    const d = decidirDisparo(alerta(), retrato({ rvol: 0.89 }), ctx());
    expect(d.disparar).toBe(false);
    expect(d.motivo).toBe("condição não satisfeita");
  });

  it("RVOL na abertura: o motivo diz o que é, não 'condição não satisfeita'", () => {
    // O motivo vai para o log. "Condição não satisfeita" num alerta que o
    // usuário jura que devia ter disparado não ajuda ninguém a entender por quê.
    const d = decidirDisparo(
      alerta(), retrato({ rvol: 5.81, rvolSignal: "indefinido_abertura" }), ctx(),
    );
    expect(d.motivo).toContain("menos de 30 minutos");
  });

  it("RVOL de ontem: o motivo diz a data", () => {
    const d = decidirDisparo(alerta(), retrato({ rvolData: "2026-09-24" }), ctx());
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("2026-09-24");
  });
});

describe("cooldown", () => {
  it("dentro das 4h não dispara, mesmo com tudo batendo", () => {
    const d = decidirDisparo(
      alerta({ lastTriggeredAt: new Date(AGORA.getTime() - 3 * 60 * 60_000) }),
      retrato(), ctx(),
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toBe("em cooldown");
  });

  it("passadas as 4h, dispara de novo", () => {
    const d = decidirDisparo(
      alerta({ lastTriggeredAt: new Date(AGORA.getTime() - COOLDOWN_MS - 1000) }),
      retrato(), ctx(),
    );
    expect(d.disparar).toBe(true);
  });

  it("o cooldown é checado ANTES das condições", () => {
    // Não é só eficiência: um alerta em cooldown cujo RVOL está indefinido
    // sairia no log como "RVOL não conclusivo", que descreve o mercado em vez de
    // descrever a decisão.
    const d = decidirDisparo(
      alerta({ lastTriggeredAt: AGORA }),
      retrato({ rvol: null, rvolSignal: "indisponivel" }), ctx(),
    );
    expect(d.motivo).toBe("em cooldown");
  });

  it("aceita lastTriggeredAt como string (é o que vem do banco)", () => {
    const d = decidirDisparo(
      alerta({ lastTriggeredAt: new Date(AGORA.getTime() - 60_000).toISOString() }),
      retrato(), ctx(),
    );
    expect(d.motivo).toBe("em cooldown");
  });
});

describe("confirmar no fechamento", () => {
  it("com o pregão em curso, não avalia", () => {
    const d = decidirDisparo(alerta({ confirmAtClose: true }), retrato(), ctx());
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("16:00 ET");
  });

  it("depois do fechamento, avalia", () => {
    const d = decidirDisparo(
      alerta({ confirmAtClose: true }), retrato(),
      ctx({ pregaoEncerrado: true }),
    );
    expect(d.disparar).toBe(true);
  });

  it("o alerta intradiário NÃO espera o fechamento", () => {
    // A contrapartida: ligar a checagem de fechamento para todo mundo mataria
    // silenciosamente todos os alertas existentes durante o pregão.
    expect(decidirDisparo(alerta({ confirmAtClose: false }), retrato(), ctx()).disparar)
      .toBe(true);
    expect(decidirDisparo(alerta({ confirmAtClose: null }), retrato(), ctx()).disparar)
      .toBe(true);
  });

  it("no fechamento, o RVOL do dia inteiro vale", () => {
    // É o cenário que a opção existe para servir: 16:00 ET, fração 1,0, RVOL
    // final. O guarda de data não pode barrá-lo por "antigo".
    const d = decidirDisparo(
      alerta({ confirmAtClose: true }),
      retrato({ rvolAte: "16:00", rvol: 1.35 }),
      ctx({ pregaoEncerrado: true }),
    );
    expect(d.disparar).toBe(true);
  });
});

describe("alerta antigo passa pelo mesmo caminho", () => {
  it("sem conditions, decide pelas colunas antigas", () => {
    const d = decidirDisparo(
      { conditions: [], indicator: "price", condition: "above", thresholdPrice: 365 },
      { ticker: "AVGO", price: 366 }, ctx(),
    );
    expect(d.disparar).toBe(true);
  });

  it("alerta antigo de preço SEM limiar nenhum não dispara", () => {
    // A linha existe no banco. Com `conditions` vazio e nenhum threshold, a
    // conversão dá lista vazia -- e lista vazia dispararia sempre se
    // `avaliarCondicoes` não a barrasse.
    const d = decidirDisparo(
      { conditions: [], indicator: "price", condition: "above" },
      { ticker: "AVGO", price: 99999 }, ctx(),
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toBe("alerta sem condição");
  });

  it("alerta antigo de RSI continua funcionando", () => {
    const d = decidirDisparo(
      { conditions: [], indicator: "rsi", condition: "above", thresholdValue: 70 },
      { ticker: "AVGO", rsi: 72 }, ctx(),
    );
    expect(d.disparar).toBe(true);
  });

  it("alerta antigo não é afetado pelo guarda de RVOL", () => {
    // Um alerta de RSI num dia em que o RVOL veio de ontem tem de disparar
    // igual: o guarda é da condição de RVOL, não do alerta.
    const d = decidirDisparo(
      { conditions: [], indicator: "rsi", condition: "above", thresholdValue: 70 },
      { ticker: "AVGO", rsi: 72, rvolData: "2020-01-01", rvolSignal: "indisponivel" },
      ctx(),
    );
    expect(d.disparar).toBe(true);
  });
});

describe("assuntoDoAlertaComposto", () => {
  it("é o formato da especificação", () => {
    const r = avaliarCondicoes(AVGO, retrato());
    expect(assuntoDoAlertaComposto("AVGO", r.condicoes, true))
      .toBe("[Premercado] AVGO — confirmação: preço 366,20 > 365 e RVOL 1,35x > 1,2x");
  });

  it("alerta intradiário não diz 'confirmação'", () => {
    // A palavra prometeria uma confirmação que o dado intradiário não dá -- é a
    // mesma disciplina da regra "você tem PONTOS, não a série".
    const r = avaliarCondicoes(AVGO, retrato());
    expect(assuntoDoAlertaComposto("AVGO", r.condicoes, false))
      .toBe("[Premercado] AVGO — disparou: preço 366,20 > 365 e RVOL 1,35x > 1,2x");
  });

  it("os valores vão no ASSUNTO, não só no corpo", () => {
    // No celular a notificação mostra o assunto e nada mais.
    const r = avaliarCondicoes(AVGO, retrato({ price: 371.5, rvol: 2.04 }));
    const s = assuntoDoAlertaComposto("AVGO", r.condicoes, false);
    expect(s).toContain("371,50");
    expect(s).toContain("2,04x");
  });

  it("MACD e médias saem sem número de corte", () => {
    const r = avaliarCondicoes(
      [{ indicator: "macd", op: "above" }, { indicator: "sma50", op: "above" }],
      { ticker: "NVDA", macdHistogram: 0.5, price: 110, sma50: 100 },
    );
    expect(assuntoDoAlertaComposto("NVDA", r.condicoes))
      .toBe("[Premercado] NVDA — disparou: MACD bullish e preço > MM50");
  });

  it("variação leva %, e a direção abaixo leva <", () => {
    const r = avaliarCondicoes(
      [{ indicator: "changePct", op: "below", value: -5 }],
      { ticker: "MU", changePct: -6.31 },
    );
    expect(assuntoDoAlertaComposto("MU", r.condicoes))
      .toBe("[Premercado] MU — disparou: variação -6,31% < -5%");
  });
});
