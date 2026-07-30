import { describe, it, expect } from "vitest";
import {
  sma, ema, macd, rsi, bollingerBands, computeIndicatorSeries, attachIndicatorFields,
  computeVwapSeries, computeCumulativeVolume, computeExpectedVolumePace,
} from "../lib/indicators";

describe("sma", () => {
  it("retorna null antes de acumular o período", () => {
    const out = sma([1, 2, 3], 3);
    expect(out[0]).toBeNull();
    expect(out[1]).toBeNull();
  });

  it("calcula a média simples corretamente", () => {
    const out = sma([1, 2, 3, 4, 5], 3);
    expect(out[2]).toBeCloseTo(2, 6); // (1+2+3)/3
    expect(out[3]).toBeCloseTo(3, 6); // (2+3+4)/3
    expect(out[4]).toBeCloseTo(4, 6); // (3+4+5)/3
  });
});

describe("ema", () => {
  it("retorna tudo null quando não há dados suficientes", () => {
    const out = ema([1, 2], 5);
    expect(out).toEqual([null, null]);
  });

  it("usa a SMA como semente e depois aplica o fator de suavização", () => {
    const values = [1, 2, 3, 4, 5];
    const out = ema(values, 3);
    expect(out[0]).toBeNull();
    expect(out[1]).toBeNull();
    expect(out[2]).toBeCloseTo(2, 6); // semente = SMA(1,2,3)
    const k = 2 / 4;
    const expected3 = 4 * k + 2 * (1 - k);
    expect(out[3]).toBeCloseTo(expected3, 6);
  });
});

describe("macd", () => {
  it("retorna null enquanto a EMA lenta não tem dados suficientes", () => {
    const values = Array.from({ length: 20 }, (_, i) => 100 + i);
    const { macd: line } = macd(values, 12, 26, 9);
    expect(line.every((v) => v === null)).toBe(true);
  });

  it("calcula MACD/signal/histogram como diferença de EMAs", () => {
    const values = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 5) * 10 + i * 0.3);
    const { macd: line, signal, histogram } = macd(values, 12, 26, 9);
    const lastIdx = values.length - 1;
    expect(line[lastIdx]).not.toBeNull();
    expect(signal[lastIdx]).not.toBeNull();
    expect(histogram[lastIdx]).toBeCloseTo((line[lastIdx] as number) - (signal[lastIdx] as number), 6);
  });
});

describe("rsi", () => {
  it("retorna null antes de ter `period` variações", () => {
    const out = rsi([1, 2, 3], 14);
    expect(out.every((v) => v === null)).toBe(true);
  });

  it("retorna 100 quando só houve ganhos no período", () => {
    const values = Array.from({ length: 15 }, (_, i) => 100 + i); // sempre sobe
    const out = rsi(values, 14);
    expect(out[14]).toBe(100);
  });

  it("retorna 0 quando só houve perdas no período", () => {
    const values = Array.from({ length: 15 }, (_, i) => 100 - i); // sempre cai
    const out = rsi(values, 14);
    expect(out[14]).toBe(0);
  });

  it("fica entre 0 e 100 pra uma série mista", () => {
    const values = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 110, 108, 111, 113, 112];
    const out = rsi(values, 14);
    expect(out[14]).toBeGreaterThan(0);
    expect(out[14]).toBeLessThan(100);
  });
});

describe("bollingerBands", () => {
  it("retorna null antes de acumular o período", () => {
    const { upper, middle, lower } = bollingerBands([1, 2, 3], 20);
    expect(upper.every((v) => v === null)).toBe(true);
    expect(middle.every((v) => v === null)).toBe(true);
    expect(lower.every((v) => v === null)).toBe(true);
  });

  it("banda do meio é a SMA, e upper/lower ficam simétricas ao redor dela", () => {
    const values = [10, 12, 11, 13, 9, 14, 10, 12, 11, 13, 9, 14, 10, 12, 11, 13, 9, 14, 10, 12];
    const { upper, middle, lower } = bollingerBands(values, 20, 2);
    const m = middle[19] as number;
    const u = upper[19] as number;
    const l = lower[19] as number;
    expect(u - m).toBeCloseTo(m - l, 6);
    expect(u).toBeGreaterThan(m);
    expect(l).toBeLessThan(m);
  });

  it("banda fica mais estreita quando a série é constante (volatilidade zero)", () => {
    const values = new Array(25).fill(50);
    const { upper, middle, lower } = bollingerBands(values, 20, 2);
    expect(upper[24]).toBeCloseTo(50, 6);
    expect(middle[24]).toBeCloseTo(50, 6);
    expect(lower[24]).toBeCloseTo(50, 6);
  });
});

describe("computeIndicatorSeries", () => {
  it("retorna todas as séries com o mesmo tamanho da entrada", () => {
    const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 4) * 8 + i * 0.2);
    const out = computeIndicatorSeries(closes);
    for (const key of ["sma21", "sma50", "bbUpper", "bbMiddle", "bbLower", "rsi", "macdLine", "macdSignal", "macdHistogram"] as const) {
      expect(out[key]).toHaveLength(closes.length);
    }
    expect(out.sma21[closes.length - 1]).not.toBeNull();
    expect(out.rsi[closes.length - 1]).not.toBeNull();
  });
});

describe("attachIndicatorFields", () => {
  it("preserva os campos originais e anexa os indicadores por índice", () => {
    const rows = [{ t: 1, v: 100 }, { t: 2, v: 101 }, { t: 3, v: 99 }];
    const out = attachIndicatorFields(rows, [100, 101, 99]);
    expect(out).toHaveLength(3);
    expect(out[0].t).toBe(1);
    expect(out[0].v).toBe(100);
    expect(out[0].sma21).toBeNull();
  });

  it("só preenche macdHistPos ou macdHistNeg por índice, nunca os dois", () => {
    const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 5) * 10 + i * 0.3);
    const rows = closes.map((c, i) => ({ t: i }));
    const out = attachIndicatorFields(rows, closes);
    for (const row of out) {
      expect(row.macdHistPos != null && row.macdHistNeg != null).toBe(false);
    }
  });
});

describe("computeVwapSeries", () => {
  it("com volume uniforme, VWAP é a média simples do preço típico acumulado", () => {
    // 3 barras, mesmo volume -- VWAP em cada ponto = média dos typical price até ali.
    const candles = [
      { h: 102, l: 98, c: 100, v: 1000, session: "regular" },
      { h: 104, l: 100, c: 102, v: 1000, session: "regular" },
      { h: 106, l: 102, c: 104, v: 1000, session: "regular" },
    ];
    const out = computeVwapSeries(candles);
    expect(out[0]).toBeCloseTo(100, 6); // (102+98+100)/3
    expect(out[1]).toBeCloseTo(101, 6); // média dos 2 primeiros typical price
    expect(out[2]).toBeCloseTo(102, 6); // média dos 3
  });

  it("ignora barras de pré/pós-mercado -- só acumula a partir do pregão regular", () => {
    const candles = [
      { h: 200, l: 200, c: 200, v: 5000, session: "pre" }, // deveria distorcer se contasse
      { h: 102, l: 98, c: 100, v: 1000, session: "regular" },
      { h: 104, l: 100, c: 102, v: 1000, session: "regular" },
    ];
    const out = computeVwapSeries(candles);
    expect(out[0]).toBeNull(); // pré-mercado não gera VWAP
    expect(out[1]).toBeCloseTo(100, 6);
    expect(out[2]).toBeCloseTo(101, 6);
  });

  it("pondera pelo volume -- barra com volume maior pesa mais no acumulado", () => {
    const candles = [
      { h: 100, l: 100, c: 100, v: 1, session: "regular" },
      { h: 200, l: 200, c: 200, v: 999, session: "regular" },
    ];
    const out = computeVwapSeries(candles);
    // cumPV = 100*1 + 200*999 = 199900; cumV = 1000 -> vwap = 199.9
    expect(out[1]).toBeCloseTo(199.9, 6);
  });
});

describe("computeCumulativeVolume", () => {
  it("acumula o volume só do pregão regular", () => {
    const candles = [
      { v: 500, session: "pre" },
      { v: 1000, session: "regular" },
      { v: 2000, session: "regular" },
      { v: 300, session: "post" },
    ];
    const out = computeCumulativeVolume(candles);
    expect(out[0]).toBeNull();
    expect(out[1]).toBe(1000);
    expect(out[2]).toBe(3000);
    expect(out[3]).toBeNull();
  });
});

describe("computeExpectedVolumePace", () => {
  it("retorna tudo null sem média de 20 dias disponível", () => {
    const candles = [{ session: "regular" }, { session: "regular" }];
    expect(computeExpectedVolumePace(candles, null)).toEqual([null, null]);
    expect(computeExpectedVolumePace(candles, 0)).toEqual([null, null]);
  });

  it("extrapola linearmente pela fração do pregão nominal decorrida", () => {
    const candles = Array.from({ length: 4 }, () => ({ session: "regular" as const }));
    const out = computeExpectedVolumePace(candles, 780_000, 78); // 78 barras nominais
    expect(out[0]).toBeCloseTo(780_000 * (1 / 78), 6);
    expect(out[3]).toBeCloseTo(780_000 * (4 / 78), 6);
  });

  it("nunca passa de 100% do volume médio diário, mesmo com mais barras que o nominal", () => {
    const candles = Array.from({ length: 100 }, () => ({ session: "regular" as const }));
    const out = computeExpectedVolumePace(candles, 780_000, 78);
    expect(out[99]).toBeCloseTo(780_000, 6);
  });
});
