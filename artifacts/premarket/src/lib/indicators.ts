// Indicadores técnicos clássicos de swing trade (tendência/momento,
// volatilidade e confirmação) -- funções puras sobre arrays de preços, sem
// nenhuma dependência de gráfico. `null` marca os índices iniciais onde ainda
// não há dados suficientes pra calcular (o recharts simplesmente pula pontos
// null numa <Line>, o que já dá o comportamento certo de "a linha só começa
// depois que acumular período suficiente").

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period) return out;
  const k = 2 / (period + 1);
  // Semente: SMA simples dos primeiros `period` valores.
  let prev = values.slice(0, period).reduce((s, v) => s + v, 0) / period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

export interface MacdResult {
  macd: (number | null)[];
  signal: (number | null)[];
  histogram: (number | null)[];
}

export function macd(values: number[], fast = 12, slow = 26, signalPeriod = 9): MacdResult {
  const emaFast = ema(values, fast);
  const emaSlow = ema(values, slow);
  const macdLine: (number | null)[] = values.map((_, i) => {
    const f = emaFast[i];
    const s = emaSlow[i];
    return f != null && s != null ? f - s : null;
  });

  // EMA do sinal roda só sobre os valores não-nulos do MACD, senão a EMA(9)
  // levaria `slow` períodos extras de atraso desnecessário antes de começar.
  const firstValidIdx = macdLine.findIndex((v) => v != null);
  const signal: (number | null)[] = new Array(values.length).fill(null);
  if (firstValidIdx !== -1) {
    const compact = macdLine.slice(firstValidIdx) as number[];
    const emaOfCompact = ema(compact, signalPeriod);
    emaOfCompact.forEach((v, i) => { signal[firstValidIdx + i] = v; });
  }

  const histogram: (number | null)[] = values.map((_, i) => {
    const m = macdLine[i];
    const s = signal[i];
    return m != null && s != null ? m - s : null;
  });

  return { macd: macdLine, signal, histogram };
}

export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= period) return out;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = values[i] - values[i - 1];
    if (diff >= 0) avgGain += diff;
    else avgLoss -= diff;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  // Suavização de Wilder pros períodos seguintes.
  for (let i = period + 1; i < values.length; i++) {
    const diff = values[i] - values[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

export interface BollingerBandsResult {
  upper: (number | null)[];
  middle: (number | null)[];
  lower: (number | null)[];
}

export function bollingerBands(values: number[], period = 20, stdDevMult = 2): BollingerBandsResult {
  const middle = sma(values, period);
  const upper: (number | null)[] = new Array(values.length).fill(null);
  const lower: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const window = values.slice(i - period + 1, i + 1);
    const mean = middle[i] as number;
    const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / period;
    const stdDev = Math.sqrt(variance);
    upper[i] = mean + stdDevMult * stdDev;
    lower[i] = mean - stdDevMult * stdDev;
  }
  return { upper, middle, lower };
}

export type IndicatorKey = "sma21" | "sma50" | "bollinger" | "volume" | "macd" | "rsi";

export const INDICATOR_LABELS: Record<IndicatorKey, string> = {
  sma21: "Média Móvel 21",
  sma50: "Média Móvel 50",
  bollinger: "Bandas de Bollinger",
  volume: "Volume",
  macd: "MACD",
  rsi: "IFR (RSI)",
};

// Cores usadas pelas linhas de overlay -- combinam com o resto da paleta
// mono/verde-vermelho já usada nos gráficos (ver session-gradient.tsx).
export const INDICATOR_COLORS = {
  sma21: "#38bdf8", // azul claro
  sma50: "#f97316", // laranja
  bollinger: "#a78bfa", // violeta
  // MACD fica no seu próprio painel, mas o tooltip lista todos os
  // indicadores juntos -- por isso macdLine/macdSignal usam tons próprios
  // (ciano/rosa) em vez de reaproveitar o azul/laranja do SMA21/SMA50,
  // senão ficava impossível diferenciar as bolinhas coloridas no tooltip.
  macdLine: "#22d3ee", // ciano
  macdSignal: "#f472b6", // rosa
} as const;

export interface IndicatorSeries {
  sma21: (number | null)[];
  sma50: (number | null)[];
  bbUpper: (number | null)[];
  bbMiddle: (number | null)[];
  bbLower: (number | null)[];
  rsi: (number | null)[];
  macdLine: (number | null)[];
  macdSignal: (number | null)[];
  macdHistogram: (number | null)[];
}

// Calcula todos os indicadores de uma vez a partir da série de fechamentos --
// usado tanto pelo overlay no painel de preço (SMA/Bollinger) quanto pelos
// paineis auxiliares embaixo (RSI/MACD/Volume).
export function computeIndicatorSeries(closes: number[]): IndicatorSeries {
  const bb = bollingerBands(closes, 20, 2);
  const m = macd(closes, 12, 26, 9);
  return {
    sma21: sma(closes, 21),
    sma50: sma(closes, 50),
    bbUpper: bb.upper,
    bbMiddle: bb.middle,
    bbLower: bb.lower,
    rsi: rsi(closes, 14),
    macdLine: m.macd,
    macdSignal: m.signal,
    macdHistogram: m.histogram,
  };
}

export interface IndicatorFields {
  sma21: number | null;
  sma50: number | null;
  bbUpper: number | null;
  bbMiddle: number | null;
  bbLower: number | null;
  rsi: number | null;
  macdLine: number | null;
  macdSignal: number | null;
  // Histograma do MACD partido em duas séries (positiva/negativa) pra
  // colorir a barra por sinal com dois <Bar> simples, sem precisar de um
  // shape customizado -- só uma delas é não-nula em cada índice.
  macdHistPos: number | null;
  macdHistNeg: number | null;
}

export function attachIndicatorFields<T>(rows: T[], closes: number[]): (T & IndicatorFields)[] {
  const s = computeIndicatorSeries(closes);
  return rows.map((row, i) => {
    const hist = s.macdHistogram[i];
    return {
      ...row,
      sma21: s.sma21[i],
      sma50: s.sma50[i],
      bbUpper: s.bbUpper[i],
      bbMiddle: s.bbMiddle[i],
      bbLower: s.bbLower[i],
      rsi: s.rsi[i],
      macdLine: s.macdLine[i],
      macdSignal: s.macdSignal[i],
      macdHistPos: hist != null && hist >= 0 ? hist : null,
      macdHistNeg: hist != null && hist < 0 ? hist : null,
    };
  });
}
