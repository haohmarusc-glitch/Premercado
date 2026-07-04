export interface TechnicalAlertInput {
  indicator: string; // 'rsi' | 'macd' | 'sma20' | 'sma50'
  condition: string; // 'above' | 'below'
  thresholdValue: number | null;
}

export interface Technicals {
  ticker: string;
  price?: number | null;
  rsi?: number | null;
  macdHistogram?: number | null;
  sma20?: number | null;
  sma50?: number | null;
  error?: string;
}

// Retorna o valor atual do indicador (pra registrar em valueAtFiring) quando
// a condicao do alerta e' satisfeita agora, ou null se nao disparar / faltar dado.
export function evalTechnical(alert: TechnicalAlertInput, t: Technicals): number | null {
  const up = alert.condition === "above";
  if (alert.indicator === "rsi") {
    if (t.rsi == null || alert.thresholdValue == null) return null;
    const hit = up ? t.rsi >= alert.thresholdValue : t.rsi <= alert.thresholdValue;
    return hit ? t.rsi : null;
  }
  if (alert.indicator === "macd") {
    if (t.macdHistogram == null) return null;
    const hit = up ? t.macdHistogram > 0 : t.macdHistogram < 0;
    return hit ? t.macdHistogram : null;
  }
  if (alert.indicator === "sma20" || alert.indicator === "sma50") {
    const sma = alert.indicator === "sma20" ? t.sma20 : t.sma50;
    if (sma == null || t.price == null) return null;
    const hit = up ? t.price > sma : t.price < sma;
    return hit ? t.price : null;
  }
  return null;
}
