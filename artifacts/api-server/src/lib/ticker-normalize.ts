// Usuários costumam colar o ticker direto do Google Finanças, que usa o
// formato "SYMBOL:EXCHANGE" (ex.: "AMBP3:BVMF", "NVDA:NASDAQ") -- mas o
// backend busca cotação via yfinance, que usa o formato do Yahoo Finance
// ("AMBP3.SA" pra B3, sem sufixo pros EUA). Sem essa conversão o símbolo
// nunca é encontrado e a cotação fica sempre "indisponível".
const EXCHANGE_SUFFIX_MAP: Record<string, string> = {
  BVMF: ".SA", // B3 (Brasil)
  NASDAQ: "",
  NYSE: "",
  NYSEARCA: "",
};

export function normalizeTicker(raw: string): string {
  const upper = raw.trim().toUpperCase();
  const [base, exchange] = upper.split(":");
  if (exchange && exchange in EXCHANGE_SUFFIX_MAP) {
    return `${base}${EXCHANGE_SUFFIX_MAP[exchange]}`;
  }
  return upper;
}
