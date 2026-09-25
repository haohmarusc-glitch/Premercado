/**
 * Ver o cabeçalho de alert-conditions.ts para o desenho e para as duas
 * divergências deliberadas em relação à especificação (RVOL não recalculado
 * aqui; abertura tratada por `indefinido_abertura` em vez de piso de 0,1).
 *
 * Os casos de `avaliarCondicoes` são literalmente os do critério de aceite.
 */
import { describe, it, expect } from "vitest";
import {
  avaliarCondicoes, condicoesDoAlertaAntigo, descreverCondicoes,
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
    expect(r.condicoes[1].motivo).toBe("sem dado");
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
