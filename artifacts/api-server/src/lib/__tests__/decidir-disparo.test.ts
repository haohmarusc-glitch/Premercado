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
  esteveParaDisparar,
  type AlertaParaDecisao, type Condicao, type RetratoDoTicker,
} from "@workspace/alertas";

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
  // `dadosAte` é obrigatório aqui: sem a identidade da sessão não há como
  // exigir "mesma sessão em tudo" nem deduplicar pela sessão, então o alerta de
  // fechamento FALHA FECHADO. Em produção ela sempre vem de get_technicals; se
  // só a cotação estiver disponível, a confirmação espera o próximo ciclo.
  const naSessao = () => retrato({ dadosAte: HOJE, rvolData: HOJE });

  it("com o pregão em curso, não avalia", () => {
    const d = decidirDisparo(alerta({ confirmAtClose: true }), naSessao(), ctx());
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("16:00 ET");
  });

  it("depois do fechamento, avalia", () => {
    const d = decidirDisparo(
      alerta({ confirmAtClose: true }), naSessao(),
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
    // final. O guarda de sessão não pode barrá-lo por "antigo".
    const d = decidirDisparo(
      alerta({ confirmAtClose: true }),
      retrato({ dadosAte: HOJE, rvolData: HOJE, rvolAte: "16:00", rvol: 1.35 }),
      ctx({ pregaoEncerrado: true }),
    );
    expect(d.disparar).toBe(true);
    expect(d.sessaoConfirmada).toBe(HOJE);
    expect(d.processadoDepois).toBe(false);
  });
});

describe("um fechamento, um e-mail — dedupe por SESSÃO", () => {
  // O defeito medido na auditoria de 30/09: a janela entre 16:00 ET e a
  // meia-noite tem OITO horas, e o cooldown de 4h cabe duas vezes nela. O
  // alerta disparava às 16:00 e de novo às 20:00, com o mesmo fechamento.
  //
  // O dedupe por dia de bolsa que consertou isso foi substituído pelo dedupe
  // por SESSÃO, que é exato: ele errava os dois lados da confirmação atrasada
  // (deixava a sexta confirmada na segunda bloquear o fechamento da própria
  // segunda, e não bloqueava nada se o relógio virasse entre dois disparos).
  const FECHOU = new Date("2026-09-30T20:00:00Z");   // 16:00 ET (EDT), quarta
  const SESSAO = "2026-09-30";
  const noFechamento = (over: Partial<AlertaParaDecisao> = {}) =>
    alerta({ confirmAtClose: true, ...over });
  const noDia = (dia = SESSAO) => retrato({ rvolData: dia, dadosAte: dia });
  const ctxFechado = (agora: Date, ja = false) => ({
    agora, dataDeHojeNaBolsa: SESSAO, pregaoEncerrado: true,
    pregaoSeguinteJaAbriu: false, jaConfirmouEstaSessao: ja,
  });

  it("dispara no fechamento, e diz qual sessão confirmou", () => {
    const d = decidirDisparo(noFechamento(), noDia(), ctxFechado(FECHOU));
    expect(d.disparar).toBe(true);
    expect(d.sessaoConfirmada).toBe(SESSAO);
    expect(d.processadoDepois).toBe(false);
  });

  it("NÃO dispara de novo pela mesma sessão", () => {
    const d = decidirDisparo(
      noFechamento({ lastTriggeredAt: FECHOU }), noDia(),
      ctxFechado(new Date("2026-10-01T00:00:00Z"), true),
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toBe(`fechamento de ${SESSAO} já confirmado`);
  });

  it("o cooldown de 4h NÃO se aplica a alerta de fechamento", () => {
    // Quem deduplica é a sessão. O cooldown atrapalharia: uma sexta confirmada
    // na segunda está a dias do último disparo, mas uma sessão NOVA logo depois
    // de um disparo precisa passar.
    const d = decidirDisparo(
      noFechamento({ lastTriggeredAt: FECHOU }),
      retrato({ rvolData: "2026-10-01", dadosAte: "2026-10-01" }),
      { agora: new Date("2026-10-01T20:30:00Z"), dataDeHojeNaBolsa: "2026-10-01",
        pregaoEncerrado: true, pregaoSeguinteJaAbriu: false,
        jaConfirmouEstaSessao: false },
    );
    expect(d.disparar).toBe(true);
    expect(d.sessaoConfirmada).toBe("2026-10-01");
  });

  it("o alerta intradiário continua no cooldown de 4h", () => {
    // A contrapartida: trocar o cooldown pelo dedupe diário em TODOS limitaria
    // cada alerta a um e-mail por dia, em silêncio.
    const d = decidirDisparo(
      alerta({ confirmAtClose: false, lastTriggeredAt: FECHOU }),
      noDia(),
      { agora: new Date("2026-09-30T21:00:00Z"), dataDeHojeNaBolsa: SESSAO,
        pregaoEncerrado: true },
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toBe("em cooldown");
  });
});

describe("confirmação ATRASADA do fechamento", () => {
  // O limite que o teste em produção expôs: o guarda exigia `rvolData === hoje`,
  // então o fechamento de uma sexta só era confirmável entre 16:00 e 23:59 ET
  // daquela sexta. Oito horas de indisponibilidade perdiam a confirmação para
  // sempre -- janela frágil para o caminho que existe para ser o CONFIÁVEL.
  const SEXTA = "2026-10-02";
  const daSexta = () => retrato({ rvolData: SEXTA, dadosAte: SEXTA, rvolSignal: "alto" });
  const noFechamento = (over: Partial<AlertaParaDecisao> = {}) =>
    alerta({ confirmAtClose: true, ...over });

  it("sábado de manhã ainda confirma a sexta, e marca o atraso", () => {
    const d = decidirDisparo(noFechamento(), daSexta(), {
      agora: new Date("2026-10-03T14:00:00Z"),      // sábado 10:00 ET
      dataDeHojeNaBolsa: "2026-10-03",
      pregaoEncerrado: false,                        // sábado não "fechou" nada
      pregaoSeguinteJaAbriu: false,
      jaConfirmouEstaSessao: false,
    });
    expect(d.disparar).toBe(true);
    expect(d.sessaoConfirmada).toBe(SEXTA);
    expect(d.processadoDepois).toBe(true);
  });

  it("segunda ANTES da abertura ainda confirma a sexta", () => {
    const d = decidirDisparo(noFechamento(), daSexta(), {
      agora: new Date("2026-10-05T12:00:00Z"),      // segunda 08:00 ET
      dataDeHojeNaBolsa: "2026-10-05",
      pregaoEncerrado: false, pregaoSeguinteJaAbriu: false,
      jaConfirmouEstaSessao: false,
    });
    expect(d.disparar).toBe(true);
    expect(d.processadoDepois).toBe(true);
  });

  it("segunda DEPOIS da abertura não confirma mais", () => {
    // O fim da validade. Sem isto, um fechamento de sexta poderia disparar na
    // quarta com dado de sexta.
    const d = decidirDisparo(noFechamento(), daSexta(), {
      agora: new Date("2026-10-05T14:00:00Z"),      // segunda 10:00 ET
      dataDeHojeNaBolsa: "2026-10-05",
      pregaoEncerrado: false, pregaoSeguinteJaAbriu: true,
      jaConfirmouEstaSessao: false,
    });
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("o pregão seguinte já abriu");
  });

  it("preço e RVOL de sessões DIFERENTES não confirmam nada", () => {
    // A integridade que a janela não pode custar: casar o volume de sexta com
    // um preço de pré-mercado de segunda seria dois pregões num veredito.
    const d = decidirDisparo(
      noFechamento(),
      retrato({ dadosAte: "2026-10-05", rvolData: SEXTA, rvolSignal: "alto" }),
      { agora: new Date("2026-10-05T12:00:00Z"), dataDeHojeNaBolsa: "2026-10-05",
        pregaoEncerrado: false, pregaoSeguinteJaAbriu: false,
        jaConfirmouEstaSessao: false },
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("sessões diferentes");
  });

  it("sem condição de RVOL, só o preço, a mesma sessão basta", () => {
    const d = decidirDisparo(
      noFechamento({ conditions: [{ indicator: "price", op: "above", value: 365 }] }),
      retrato({ dadosAte: SEXTA, rvolData: null, rvol: null }),
      { agora: new Date("2026-10-03T14:00:00Z"), dataDeHojeNaBolsa: "2026-10-03",
        pregaoEncerrado: false, pregaoSeguinteJaAbriu: false,
        jaConfirmouEstaSessao: false },
    );
    expect(d.disparar).toBe(true);
    expect(d.processadoDepois).toBe(true);
  });

  it("sem dadosAte não confirma -- não há identidade de sessão", () => {
    const d = decidirDisparo(noFechamento(), retrato({ dadosAte: null }), {
      agora: new Date("2026-10-03T14:00:00Z"), dataDeHojeNaBolsa: "2026-10-03",
      pregaoEncerrado: false, pregaoSeguinteJaAbriu: false,
    });
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("sem data da sessão");
  });

  it("dado À FRENTE do relógio é inconsistência, não atraso", () => {
    const d = decidirDisparo(noFechamento(), daSexta(), {
      agora: new Date("2026-10-01T20:00:00Z"), dataDeHojeNaBolsa: "2026-10-01",
      pregaoEncerrado: true, pregaoSeguinteJaAbriu: false,
    });
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain("à frente de hoje");
  });

  it("o alerta INTRADIÁRIO não ganha a exceção", () => {
    // A proteção que separa os dois caminhos: dado de outra sessão continua
    // barrado para quem avalia ao longo do dia.
    const d = decidirDisparo(
      alerta({ confirmAtClose: false }),
      daSexta(),
      { agora: new Date("2026-10-05T12:00:00Z"), dataDeHojeNaBolsa: "2026-10-05",
        pregaoEncerrado: false, pregaoSeguinteJaAbriu: false },
    );
    expect(d.disparar).toBe(false);
    expect(d.motivo).toContain(`RVOL é do pregão de ${SEXTA}`);
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

describe("o log da recusa tem de ser LEGÍVEL", () => {
  // A linha "Alerta composto não disparou" era emitida em `logger.debug`, e
  // `LOG_LEVEL` é `info`: a explicação existia, estava correta, e nunca
  // aparecia em produção. Eu cheguei a passar ao usuário um comando de grep
  // que não podia achar nada.
  //
  // `esteveParaDisparar` é o corte, e por isso tem nome: quem pergunta "por que
  // não disparou?" pergunta de um alerta que QUASE disparou.

  it("alguma condição satisfeita = perto, vai para info", () => {
    // O caso da AVGO: preço passou, RVOL não. É exatamente o que o usuário quer
    // ler quando o alerta não chega.
    const r = avaliarCondicoes(AVGO, retrato({ price: 366, rvol: 0.89 }));
    expect(r.disparou).toBe(false);
    expect(esteveParaDisparar(r)).toBe(true);
  });

  it("em cooldown com tudo satisfeito também é perto", () => {
    // As condições são avaliadas ANTES da recusa por cooldown, então o cooldown
    // cai naturalmente no nível visível -- sem precisar de caso especial.
    const d = decidirDisparo(
      alerta({ lastTriggeredAt: AGORA }), retrato(), ctx(),
    );
    expect(d.motivo).toBe("em cooldown");
    expect(esteveParaDisparar(d.avaliacao)).toBe(true);
  });

  it("nenhuma condição satisfeita = longe, fica em debug", () => {
    // 288 linhas por dia por alerta que ninguém leria.
    const r = avaliarCondicoes(AVGO, retrato({ price: 300, rvol: 0.5 }));
    expect(esteveParaDisparar(r)).toBe(false);
  });

  it("RVOL indefinido com o preço passando é perto", () => {
    // O caso da abertura: o usuário vê o preço no alvo e não recebe e-mail.
    // Se esta linha ficasse em debug, a pergunta dele não teria resposta.
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 5.81, rvolSignal: "indefinido_abertura",
    }));
    expect(esteveParaDisparar(r)).toBe(true);
  });

  it("lista vazia não é perto", () => {
    expect(esteveParaDisparar(avaliarCondicoes([], retrato()))).toBe(false);
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
