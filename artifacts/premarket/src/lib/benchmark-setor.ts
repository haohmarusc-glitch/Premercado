/**
 * Benchmark setorial sugerido por ticker — usado pela tela Análise Rápida
 * para preencher o campo de vol/beta sozinha.
 *
 * É SUGESTÃO, não regra: o campo continua editável, porque papel com dois
 * drivers merece ser medido das duas formas. DELL contra XLK dá um beta;
 * contra SMH dá outro, e o segundo é mais informativo se o que move a ação
 * for demanda de servidor de IA.
 *
 * O critério de escolha é "o que faz esse papel subir e descer no mesmo dia
 * que outros" — não o setor formal da empresa. Por isso GOOGL/META estão em
 * XLC (comunicação) e não em XLK, e por isso as fabricantes de energia para
 * data center (CEG/VST) ficam em XLU mesmo sendo tese de IA: quem dita o
 * dia delas é o setor elétrico.
 */

export const BENCHMARK_PADRAO = "SMH";

const MAPA: Record<string, string> = {};

function registrar(benchmark: string, tickers: string[]): void {
  for (const t of tickers) MAPA[t] = benchmark;
}

// Semicondutores e IA
registrar("SMH", [
  "NVDA", "INTC", "AMD", "AVGO", "MRVL", "ARM", "QCOM", "TSM", "ASML",
  "AMAT", "LRCX", "KLAC", "MU", "SNDK", "WDC", "STX", "AOSL", "SMCI",
  "TXN", "ADI", "NXPI", "ON", "MCHP", "SOXX",
]);

// Energia para data center — o dia deles é ditado pelo setor elétrico
registrar("XLU", ["CEG", "VST", "NRG", "TLN", "NEE", "DUK", "SO"]);

// Equipamento elétrico/industrial ligado à infraestrutura de IA
registrar("XLI", ["VRT", "ETN", "GEV", "PWR", "CAT", "GE", "HON", "EMR"]);

// Internet chinesa
registrar("KWEB", ["BIDU", "BABA", "PDD", "NTES", "JD", "TCEHY", "TCOM", "BILI", "LI", "NIO", "XPEV"]);

// Big tech / software
registrar("XLK", ["MSFT", "AAPL", "ORCL", "CRM", "ADBE", "NOW", "PLTR", "SNOW", "PANW", "DELL", "HPE", "ANET", "IBM", "CSCO"]);

// Comunicação e mídia
registrar("XLC", ["GOOGL", "GOOG", "META", "NFLX", "DIS", "TTWO", "EA", "SPOT"]);

// Consumo discricionário
registrar("XLY", ["AMZN", "TSLA", "HD", "NKE", "SBUX", "MCD", "LOW", "BKNG", "MELI"]);

// Consumo básico
registrar("XLP", ["EL", "PG", "KO", "PEP", "COST", "CL", "KMB", "GIS", "WMT"]);

// Construção civil
registrar("ITB", ["TOL", "DHI", "LEN", "PHM", "NVR", "KBH", "MTH"]);

// Financeiro
registrar("XLF", ["JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "AXP", "BLK"]);

// Saúde
registrar("XLV", ["LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "TMO", "ISRG", "NVO"]);

// Energia (petróleo e gás)
registrar("XLE", ["XOM", "CVX", "OXY", "COP", "SLB", "PSX", "MPC"]);

/**
 * ETFs e índices: o beta de um ETF contra si mesmo seria 1,00 e não diria
 * nada, então a referência vira o mercado amplo. Índices (^GSPC, ^IXIC)
 * caem na mesma regra pelo prefixo "^".
 */
const ETFS_E_INDICES = new Set([
  "SMH", "SOXX", "KWEB", "FXI", "ITB", "XHB", "XLK", "XLC", "XLY", "XLP",
  "XLF", "XLV", "XLE", "XLI", "XLU", "SPY", "QQQ", "VOO", "IVV", "VTI",
  "DIA", "ARKK", "EWY", "EWZ", "EWJ", "IWM", "ACWI", "VXX", "UVXY",
  "SGOV", "BIL", "TLT", "AGG", "BND", "GLD", "SLV", "USO",
]);

/**
 * Benchmark sugerido para o ticker. Devolve SMH (o padrão do sistema)
 * quando não há mapeamento — a tela mostra a sugestão como preenchimento,
 * nunca como imposição.
 */
export function benchmarkSugerido(ticker: string): string {
  const t = (ticker || "").trim().toUpperCase();
  if (!t) return BENCHMARK_PADRAO;
  if (t.startsWith("^") || ETFS_E_INDICES.has(t)) return "SPY";
  return MAPA[t] ?? BENCHMARK_PADRAO;
}

/** True quando a sugestão veio do mapa, não do fallback — a tela usa isso
 * para dizer "sugerido para NVDA" em vez de fingir certeza sobre um ticker
 * que não conhece. */
export function temSugestaoConhecida(ticker: string): boolean {
  const t = (ticker || "").trim().toUpperCase();
  if (!t) return false;
  return t.startsWith("^") || ETFS_E_INDICES.has(t) || t in MAPA;
}
