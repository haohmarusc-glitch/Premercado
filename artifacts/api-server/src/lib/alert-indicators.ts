// Indicadores suportados por um alerta. 'price' e' o comportamento original
// (preco/variacao %); os demais sao alertas por condicao tecnica.
export const ALERT_INDICATORS = ["price", "rsi", "macd", "sma20", "sma50"] as const;
export type AlertIndicator = (typeof ALERT_INDICATORS)[number];

export function isAlertIndicator(v: string): v is AlertIndicator {
  return (ALERT_INDICATORS as readonly string[]).includes(v);
}
