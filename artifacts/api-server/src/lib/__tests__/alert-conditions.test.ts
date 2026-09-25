/**
 * Ver o cabeçalho de alert-conditions.ts para o desenho e para as duas
 * divergências deliberadas em relação à especificação (RVOL não recalculado
 * aqui; abertura tratada por `indefinido_abertura` em vez de piso de 0,1).
 *
 * Os casos de `avaliarCondicoes` são literalmente os do critério de aceite.
 */
import { describe, it, expect } from "vitest";
import {
  avaliarCondicoes, condicoesDoAlerta, condicoesDoAlertaAntigo, descreverCondicoes,
  motivoParaIgnorarRvol, rotuloDeRvolIndisponivel, rvolEhDeHoje, validarCondicoes,
  type Condicao, type RetratoDoTicker,
} from "../alert-conditions";

/** O caso do chat: "confirmação acima de $365-370 com volume >1.2x". */
const AVGO: Condicao[] = [
  { indicator: "price", op: "above", value: 365 },
  { indicator: "rvol", op: "above", value: 1.2 },
];

function retrato(p: Partial<RetratoDoTicker>): RetratoDoTicker {
  return { ticker: "AVGO", rvolSignal: "normal", ...p };
}

describe("avaliarCondicoes — os casos do critério de aceite", () => {
  it("preço 366 + RVOL 1,3 → dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 366, rvol: 1.3 }));
    expect(r.disparou).toBe(true);
    expect(r.condicoes.every((c) => c.satisfeita)).toBe(true);
    // valueAtFiring registra o valor da PRIMEIRA condição.
    expect(r.valorPrincipal).toBe(366);
  });

  it("preço 366 + RVOL 0,9 → NÃO dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 366, rvol: 0.9 }));
    expect(r.disparou).toBe(false);
    // O detalhe de cada uma tem de sobreviver: é o que a tela mostra.
    expect(r.condicoes[0].satisfeita).toBe(true);
    expect(r.condicoes[1].satisfeita).toBe(false);
    expect(r.condicoes[1].atual).toBe(0.9);
  });

  it("preço 351 + RVOL 1,5 → NÃO dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 351, rvol: 1.5 }));
    expect(r.disparou).toBe(false);
    expect(r.condicoes[0].satisfeita).toBe(false);
    expect(r.condicoes[1].satisfeita).toBe(true);
  });
});

describe("avaliarCondicoes — o E é de verdade", () => {
  it("avalia TODAS as condições, mesmo depois de uma falhar", () => {
    // Não é curto-circuito: a lista de alertas mostra o valor atual de cada
    // condição ("preço 351,05 / 365 ❌ · RVOL 0,89 / 1,2 ❌").
    const r = avaliarCondicoes(AVGO, retrato({ price: 351.05, rvol: 0.89 }));
    expect(r.condicoes).toHaveLength(2);
    expect(r.condicoes.map((c) => c.atual)).toEqual([351.05, 0.89]);
  });

  it("lista vazia não dispara", () => {
    // Um alerta sem condição dispararia SEMPRE, e é o estado em que uma
    // migração malfeita deixaria as linhas antigas.
    expect(avaliarCondicoes([], retrato({ price: 999 })).disparou).toBe(false);
  });

  it("condição sem dado não dispara, e diz por quê", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 366, rvol: null }));
    expect(r.disparou).toBe(false);
    expect(r.condicoes[1].motivo).toBe("sem RVOL do pregão de hoje");
  });

  it("indicador sem RVOL usa o motivo genérico", () => {
    const r = avaliarCondicoes(
      [{ indicator: "rsi14", op: "above", value: 70 }], retrato({ rsi: null }),
    );
    expect(r.condicoes[0].motivo).toBe("sem dado");
  });

  it("condição sem valor de corte não dispara", () => {
    // Nível ausente em indicador que exige nível é alerta mal formado. Tratar
    // como "passa" faria dele um alerta que dispara sempre.
    const r = avaliarCondicoes(
      [{ indicator: "price", op: "above", value: null }], retrato({ price: 366 }),
    );
    expect(r.disparou).toBe(false);
    expect(r.condicoes[0].motivo).toBe("condição sem valor de corte");
  });

  it("o corte é inclusivo (>=), como no checker antigo", () => {
    // Preço exatamente no alvo dispara. Mudar para > silenciosamente faria
    // alertas antigos deixarem de disparar no nível exato.
    const r = avaliarCondicoes(
      [{ indicator: "price", op: "above", value: 365 }], retrato({ price: 365 }),
    );
    expect(r.disparou).toBe(true);
  });
});

describe("RVOL na abertura", () => {
  it("não satisfaz enquanto o pregão tem menos de 30 minutos", () => {
    // O NBIS saiu com RVOL 5,81 "alto" aos SETE minutos de pregão. Com o piso
    // de 0,1 que a especificação pede, esse 5,81 continuaria passando de 1,2 e
    // o alerta disparia em ruído; marcado como indefinido, não passa.
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 5.81, rvolSignal: "indefinido_abertura",
    }));
    expect(r.disparou).toBe(false);
    expect(r.condicoes[1].motivo).toContain("menos de 30 minutos");
    // O valor continua visível: a tela mostra o número e diz que não vale.
    expect(r.condicoes[1].atual).toBe(5.81);
  });

  it("com o pregão andado, o mesmo RVOL passa", () => {
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 1.35, rvolSignal: "alto",
    }));
    expect(r.disparou).toBe(true);
  });

  it("a guarda vale só para RVOL, não para preço", () => {
    // Preço na abertura é preço. Só o RVOL depende de quanto da sessão passou.
    const r = avaliarCondicoes(
      [{ indicator: "price", op: "above", value: 365 }],
      retrato({ price: 366, rvolSignal: "indefinido_abertura" }),
    );
    expect(r.disparou).toBe(true);
  });
});

describe("RVOL indefinido derruba o alerta INTEIRO", () => {
  // O modo de falha a evitar: o alerta "desiste" da condição de volume e passa
  // a disparar só pelo preço. Um alerta de confirmação sem a confirmação é um
  // alerta de preço com nome errado, e dispara justamente no rompimento falso
  // que ele foi criado para filtrar.

  it("preço acima do alvo + RVOL indefinido = não dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({
      price: 400, rvol: 9.9, rvolSignal: "indefinido_abertura",
    }));
    expect(r.disparou).toBe(false);
    expect(r.condicoes[0].satisfeita).toBe(true);   // o preço passou...
    expect(r.condicoes[1].satisfeita).toBe(false);  // ...e não bastou
  });

  it("RVOL indisponível (pré-mercado, feriado) = não dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({
      price: 400, rvol: null, rvolSignal: "indisponivel",
    }));
    expect(r.disparou).toBe(false);
    expect(r.condicoes[1].motivo).toBe("sem RVOL do pregão de hoje");
  });

  it("alerta que olha SÓ o preço não muda", () => {
    // A contrapartida: apertar o RVOL não pode apertar o que não usa RVOL.
    const soPreco: Condicao[] = [{ indicator: "price", op: "above", value: 365 }];
    const r = avaliarCondicoes(soPreco, retrato({
      price: 366, rvol: null, rvolSignal: "indisponivel",
    }), "2026-09-25");
    expect(r.disparou).toBe(true);
  });
});

describe("RVOL de outro pregão", () => {
  // O frame "1d" do provedor volta com o ÚLTIMO dia negociado. Num feriado o
  // rvol chega completo, plausível e referente a ontem -- nada no número
  // denuncia isso.

  it("data diferente de hoje não satisfaz, e diz qual data veio", () => {
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 1.35, rvolSignal: "alto", rvolData: "2026-09-24",
    }), "2026-09-25");
    expect(r.disparou).toBe(false);
    expect(r.condicoes[1].motivo).toBe("RVOL é do pregão de 2026-09-24, não de hoje");
  });

  it("mesma data dispara", () => {
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 1.35, rvolSignal: "alto", rvolData: "2026-09-25",
    }), "2026-09-25");
    expect(r.disparou).toBe(true);
  });

  it("sem rvolData a resposta é NÃO, não 'provavelmente hoje'", () => {
    // Payload de antes deste campo existir. Tratá-lo como válido faria o alerta
    // composto disparar com rvol de data desconhecida exatamente nos deploys em
    // que o Python e o Node estão fora de passo.
    expect(rvolEhDeHoje({ ticker: "AVGO", rvol: 1.35 }, "2026-09-25")).toBe(false);
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 1.35, rvolSignal: "alto",
    }), "2026-09-25");
    expect(r.disparou).toBe(false);
  });

  it("o fechamento do dia continua valendo — é o que a confirmação usa", () => {
    // Depois das 16h ET o rvol do dia está COMPLETO e é o número mais
    // confiável que existe; recusá-lo por ser "antigo" inviabilizaria a opção
    // "confirmar no fechamento". O guarda é de DATA, não de idade em minutos.
    const r = avaliarCondicoes(AVGO, retrato({
      price: 366, rvol: 1.35, rvolSignal: "alto",
      rvolData: "2026-09-25", rvolAte: "16:00",
    }), "2026-09-25");
    expect(r.disparou).toBe(true);
  });
});

describe("motivoParaIgnorarRvol / rotuloDeRvolIndisponivel", () => {
  it("a ordem dos motivos é fixa: abertura antes de ausência", () => {
    // Nos primeiros 30 minutos o rvol EXISTE. Se "sem dado" viesse primeiro, a
    // tela diria "sem RVOL" num momento em que o número está na própria linha.
    expect(motivoParaIgnorarRvol({
      ticker: "AVGO", rvol: 5.81, rvolSignal: "indefinido_abertura",
    })).toContain("menos de 30 minutos");
  });

  it("sem a data de hoje, o guarda de pregão não opina", () => {
    expect(motivoParaIgnorarRvol({
      ticker: "AVGO", rvol: 1.35, rvolSignal: "alto", rvolData: "2026-09-24",
    })).toBeNull();
  });

  it("o rótulo da tela distingue abertura de ausência", () => {
    expect(rotuloDeRvolIndisponivel({ ticker: "A", rvolSignal: "indefinido_abertura" }))
      .toBe("RVOL indisponível (abertura)");
    expect(rotuloDeRvolIndisponivel({ ticker: "A", rvolSignal: "indisponivel" }))
      .toBe("RVOL indisponível");
  });
});

describe("os indicadores que já existiam", () => {
  it("SMA compara o PREÇO com a média, não a média com um valor", () => {
    // Semântica do avaliador antigo. Trocá-la inverteria o sentido dos alertas
    // de cruzamento que já estão cadastrados.
    const acima = avaliarCondicoes(
      [{ indicator: "sma20", op: "above" }], retrato({ price: 110, sma20: 100 }),
    );
    expect(acima.disparou).toBe(true);
    expect(acima.condicoes[0].atual).toBe(110);
    const abaixo = avaliarCondicoes(
      [{ indicator: "sma20", op: "below" }], retrato({ price: 90, sma20: 100 }),
    );
    expect(abaixo.disparou).toBe(true);
  });

  it("MACD é sinal do histograma, sem nível", () => {
    expect(avaliarCondicoes(
      [{ indicator: "macd", op: "above" }], retrato({ macdHistogram: 0.5 }),
    ).disparou).toBe(true);
    expect(avaliarCondicoes(
      [{ indicator: "macd", op: "above" }], retrato({ macdHistogram: -0.5 }),
    ).disparou).toBe(false);
  });

  it("RSI e variação usam nível", () => {
    expect(avaliarCondicoes(
      [{ indicator: "rsi14", op: "above", value: 70 }], retrato({ rsi: 72 }),
    ).disparou).toBe(true);
    expect(avaliarCondicoes(
      [{ indicator: "changePct", op: "below", value: -5 }], retrato({ changePct: -6 }),
    ).disparou).toBe(true);
  });
});

describe("condicoesDoAlertaAntigo — alertas existentes não mudam de comportamento", () => {
  it("preço com valor absoluto", () => {
    expect(condicoesDoAlertaAntigo({
      indicator: "price", condition: "above", thresholdPrice: 365,
    })).toEqual([{ indicator: "price", op: "above", value: 365 }]);
  });

  it("preço com percentual vira changePct", () => {
    expect(condicoesDoAlertaAntigo({
      indicator: "price", condition: "below", thresholdPct: -5,
    })).toEqual([{ indicator: "changePct", op: "below", value: -5 }]);
  });

  it("preço absoluto tem precedência sobre percentual, como no checker antigo", () => {
    // Os dois preenchidos é estado possível no banco. A ordem antiga era
    // thresholdPrice primeiro; invertê-la mudaria o disparo de alertas reais.
    expect(condicoesDoAlertaAntigo({
      indicator: "price", condition: "above", thresholdPrice: 365, thresholdPct: 3,
    })).toEqual([{ indicator: "price", op: "above", value: 365 }]);
  });

  it("rsi, macd e as médias", () => {
    expect(condicoesDoAlertaAntigo({
      indicator: "rsi", condition: "above", thresholdValue: 70,
    })).toEqual([{ indicator: "rsi14", op: "above", value: 70 }]);
    expect(condicoesDoAlertaAntigo({ indicator: "macd", condition: "above" }))
      .toEqual([{ indicator: "macd", op: "above", value: null }]);
    expect(condicoesDoAlertaAntigo({ indicator: "sma50", condition: "below" }))
      .toEqual([{ indicator: "sma50", op: "below", value: null }]);
  });

  it("alerta de preço sem nenhum limiar vira lista VAZIA, não dispara-sempre", () => {
    // Linha possível no banco (preço sem threshold nenhum). Uma condição de
    // preço sem valor dispararia com qualquer cotação.
    expect(condicoesDoAlertaAntigo({ indicator: "price", condition: "above" })).toEqual([]);
    expect(avaliarCondicoes([], retrato({ price: 999 })).disparou).toBe(false);
  });
});

describe("condicoesDoAlerta — sem backfill, a conversão é na leitura", () => {
  it("alerta novo usa as próprias conditions", () => {
    expect(condicoesDoAlerta({
      conditions: AVGO, indicator: "price", condition: "above", thresholdPrice: 999,
    })).toEqual(AVGO);
  });

  it("alerta antigo (conditions vazio) cai na conversão", () => {
    // É o estado de TODA linha existente no banco: a migração adiciona a coluna
    // com default [] e não reescreve nada.
    expect(condicoesDoAlerta({
      conditions: [], indicator: "price", condition: "above", thresholdPrice: 365,
    })).toEqual([{ indicator: "price", op: "above", value: 365 }]);
  });

  it("coluna ausente (payload de antes da migração) também cai na conversão", () => {
    expect(condicoesDoAlerta({
      indicator: "rsi", condition: "above", thresholdValue: 70,
    })).toEqual([{ indicator: "rsi14", op: "above", value: 70 }]);
  });

  it("conditions que não é array não derruba nada", () => {
    // jsonb aceita qualquer JSON: uma linha com `{}` ou `null` ali é possível.
    for (const lixo of [null, {}, "[]", 7]) {
      expect(condicoesDoAlerta({
        conditions: lixo, indicator: "price", condition: "below", thresholdPct: -5,
      })).toEqual([{ indicator: "changePct", op: "below", value: -5 }]);
    }
  });

  it("alerta antigo de preço sem limiar continua inerte, não dispara-sempre", () => {
    const c = condicoesDoAlerta({ conditions: [], indicator: "price", condition: "above" });
    expect(c).toEqual([]);
    expect(avaliarCondicoes(c, retrato({ price: 99999 })).disparou).toBe(false);
  });
});

describe("validarCondicoes", () => {
  it("aceita o alerta da AVGO", () => {
    expect(validarCondicoes(AVGO)).toBeNull();
  });

  it("recusa lista vazia", () => {
    // Alerta sem condição é inerte e SILENCIOSO: o usuário espera um e-mail que
    // nunca vem. Melhor recusar na criação.
    expect(validarCondicoes([])).toBe("informe ao menos uma condição");
    expect(validarCondicoes(null)).toBe("informe ao menos uma condição");
  });

  it("recusa indicador desconhecido e operador desconhecido", () => {
    expect(validarCondicoes([{ indicator: "vwap", op: "above", value: 1 }]))
      .toContain("indicator deve ser um de");
    expect(validarCondicoes([{ indicator: "price", op: "crosses", value: 1 }]))
      .toContain("op deve ser");
  });

  it("exige nível de quem usa nível, e não exige de quem não usa", () => {
    expect(validarCondicoes([{ indicator: "rvol", op: "above" }]))
      .toContain("exige um valor numérico");
    expect(validarCondicoes([{ indicator: "price", op: "above", value: "365" }]))
      .toContain("exige um valor numérico");
    expect(validarCondicoes([{ indicator: "price", op: "above", value: NaN }]))
      .toContain("exige um valor numérico");
    // MACD e as médias descrevem a condição inteira com above/below.
    expect(validarCondicoes([{ indicator: "macd", op: "above" }])).toBeNull();
    expect(validarCondicoes([{ indicator: "sma50", op: "below" }])).toBeNull();
  });

  it("recusa a mesma condição repetida, e explica como fazer faixa", () => {
    expect(validarCondicoes([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "price", op: "above", value: 350 },
    ])).toContain("use above e below");
  });

  it("faixa (above + below no mesmo indicador) é válida", () => {
    expect(validarCondicoes([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "price", op: "below", value: 380 },
    ])).toBeNull();
  });

  it("diz QUAL condição está errada", () => {
    expect(validarCondicoes([
      { indicator: "price", op: "above", value: 365 },
      { indicator: "rvol", op: "above" },
    ])).toContain("condição 2");
  });
});

describe("descreverCondicoes", () => {
  it("monta a linha que a tela e o e-mail mostram", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 366.2, rvol: 1.35 }));
    expect(descreverCondicoes(r.condicoes))
      .toBe("preço 366.20 / 365 ✅ · RVOL 1.35 / 1.2 ✅");
  });

  it("mostra o que falhou, com o valor atual", () => {
    const r = avaliarCondicoes(AVGO, retrato({ price: 351.05, rvol: 0.89 }));
    expect(descreverCondicoes(r.condicoes))
      .toBe("preço 351.05 / 365 ❌ · RVOL 0.89 / 1.2 ❌");
  });

  it("MACD aparece como bullish/bearish, não como número de corte", () => {
    const r = avaliarCondicoes(
      [{ indicator: "macd", op: "above" }], retrato({ macdHistogram: 0.5 }),
    );
    expect(descreverCondicoes(r.condicoes)).toBe("MACD 0.50 / bullish ✅");
  });

  it("dado ausente vira travessão, não zero", () => {
    const r = avaliarCondicoes(
      [{ indicator: "rvol", op: "above", value: 1.2 }], retrato({ rvol: null }),
    );
    expect(descreverCondicoes(r.condicoes)).toBe("RVOL — / 1.2 ❌");
  });
});
