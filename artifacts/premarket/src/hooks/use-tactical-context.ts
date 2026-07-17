import { useQuery } from "@tanstack/react-query";

// ─── Contexto tático por ticker ──────────────────────────────────────────────
// Junta 3 endpoints que já existem e rodam fora do loop do agente (rápidos,
// sem custo de token): /technicals (RSI/MACD/SMA), /news (manchetes) e
// /market-alerts (contágio setorial, macro, earnings, geopolítico -- o mesmo
// check_market_alerts do agente). Usado pelo Plano de Saída pra mostrar, por
// posição, não só o prazo mas o "porquê agora": preço/RSI atual, manchete
// recente e qualquer alerta de mercado batendo com aquele ticker.

export interface TechnicalSnapshot {
  ticker: string;
  price?: number;
  changePct?: number | null;
  rsi?: number | null;
  rsiSignal?: string;
  macdTrend?: string;
  sma50?: number | null;
  sma200?: number | null;
  pctAboveSma50?: number | null;
  pctAboveSma200?: number | null;
  volumeRatio?: number | null;
  error?: string;
}

export interface NewsHeadline {
  title: string;
  published: string | number;
  summary: string;
  source: string;
}

export interface MarketAlertItem {
  ticker: string;
  category: string;
  severity: "info" | "atencao" | "critico";
  title: string;
  detail: string;
  value?: number | null;
  timestamp: string;
}

async function fetchJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function tickerKey(tickers: string[]): string {
  return Array.from(new Set(tickers.map((t) => t.toUpperCase()))).sort().join(",");
}

export function useTacticalContext(tickers: string[]) {
  const key = tickerKey(tickers);
  const enabled = key.length > 0;

  const technicalsQ = useQuery({
    queryKey: ["exit-plan-technicals", key],
    queryFn: () => fetchJSON<{ items: TechnicalSnapshot[] }>(`/api/technicals?tickers=${encodeURIComponent(key)}`),
    enabled,
    staleTime: 55_000,
    refetchInterval: 60_000,
    retry: 1,
  });

  const newsQ = useQuery({
    queryKey: ["exit-plan-news", key],
    queryFn: () => fetchJSON<{ items: { ticker: string; news?: NewsHeadline[]; error?: string }[] }>(`/api/news?tickers=${encodeURIComponent(key)}`),
    enabled,
    staleTime: 4 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });

  const alertsQ = useQuery({
    queryKey: ["exit-plan-market-alerts", key],
    queryFn: () => fetchJSON<{ total: number; criticalCount: number; alerts: MarketAlertItem[] }>(`/api/market-alerts?tickers=${encodeURIComponent(key)}`),
    enabled,
    staleTime: 4 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });

  const technicalsByTicker = new Map<string, TechnicalSnapshot>();
  for (const item of technicalsQ.data?.items ?? []) technicalsByTicker.set(item.ticker, item);

  const newsByTicker = new Map<string, NewsHeadline[]>();
  for (const entry of newsQ.data?.items ?? []) newsByTicker.set(entry.ticker, entry.news ?? []);

  const alertsByTicker = new Map<string, MarketAlertItem[]>();
  for (const alert of alertsQ.data?.alerts ?? []) {
    if (!alert.ticker) continue;
    const list = alertsByTicker.get(alert.ticker) ?? [];
    list.push(alert);
    alertsByTicker.set(alert.ticker, list);
  }

  return {
    technicalsByTicker,
    newsByTicker,
    alertsByTicker,
    isLoading: enabled && (technicalsQ.isLoading || newsQ.isLoading || alertsQ.isLoading),
  };
}

export type TacticalContext = ReturnType<typeof useTacticalContext>;

export type Tone = "critico" | "atencao" | "info" | "bom";

export interface TacticalSignal {
  label: string;
  tone: Tone;
}

// Resumo de 1 linha combinando técnico + alerta de mercado -- não é um score,
// é só a leitura mais acionável disponível pra aquele ticker agora.
export function tacticalSignal(ticker: string, ctx: TacticalContext): TacticalSignal | null {
  const alerts = ctx.alertsByTicker.get(ticker) ?? [];
  const critical = alerts.find((a) => a.severity === "critico");
  if (critical) return { label: critical.title, tone: "critico" };

  const tech = ctx.technicalsByTicker.get(ticker);
  if (tech?.rsi != null) {
    if (tech.rsi >= 70) return { label: `RSI ${tech.rsi.toFixed(0)} — esticado pra cima, bom momento de venda`, tone: "bom" };
    if (tech.rsi <= 30) return { label: `RSI ${tech.rsi.toFixed(0)} — sobrevendido, ainda sem força de repique`, tone: "atencao" };
  }

  const atencao = alerts.find((a) => a.severity === "atencao");
  if (atencao) return { label: atencao.title, tone: "atencao" };

  if (tech?.changePct != null && Math.abs(tech.changePct) >= 3) {
    return {
      label: `${tech.changePct > 0 ? "Subindo" : "Caindo"} ${Math.abs(tech.changePct).toFixed(1)}% hoje`,
      tone: tech.changePct > 0 ? "bom" : "atencao",
    };
  }

  return null;
}
