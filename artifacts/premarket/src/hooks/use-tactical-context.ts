import { useQuery } from "@tanstack/react-query";
import { useStaggerReady } from "./use-stagger-ready";

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
  rvol?: number | null;
  rvolSignal?: "alto" | "baixo" | "normal" | null;
  vwap?: number | null;
  priceVsVwapPct?: number | null;
  vwapSignal?: "acima" | "abaixo" | "no vwap" | null;
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

  // As 3 chamadas abaixo eram disparadas juntas, ao vivo, no mesmo mount --
  // sozinhas já eram 3 dos "vários subprocessos concorrendo" medidos em
  // produção (04-05/08). Cada uma tem seu próprio atraso pra não competir
  // nem entre si nem com o resto do dashboard (market-alerts geral, trend,
  // alt-data já espaçados em market-alerts-card.tsx/trend-card.tsx/
  // smart-money-card.tsx). 0/200/400ms: a mais barata (RSI/MACD via
  // technicals) primeiro, a mais cara por rede (busca de notícias) por
  // último.
  const readyTechnicals = useStaggerReady(0);
  const readyNews = useStaggerReady(200);
  const readyAlerts = useStaggerReady(400);

  const technicalsQ = useQuery({
    queryKey: ["exit-plan-technicals", key],
    queryFn: () => fetchJSON<{ items: TechnicalSnapshot[] }>(`/api/technicals?tickers=${encodeURIComponent(key)}`),
    enabled: enabled && readyTechnicals,
    staleTime: 55_000,
    refetchInterval: 60_000,
    retry: 1,
  });

  const newsQ = useQuery({
    queryKey: ["exit-plan-news", key],
    queryFn: () => fetchJSON<{ items: { ticker: string; news?: NewsHeadline[]; error?: string }[] }>(`/api/news?tickers=${encodeURIComponent(key)}`),
    enabled: enabled && readyNews,
    staleTime: 4 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });

  const alertsQ = useQuery({
    queryKey: ["exit-plan-market-alerts", key],
    queryFn: () => fetchJSON<{ total: number; criticalCount: number; alerts: MarketAlertItem[] }>(`/api/market-alerts?tickers=${encodeURIComponent(key)}`),
    enabled: enabled && readyAlerts,
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
    // RVOL confirma (ou não) se o RSI esticado tem força real por trás --
    // RSI extremo com volume normal/baixo é bem mais fraco que o mesmo RSI
    // com volume 1.5x+ acima do esperado pra essa hora do pregão.
    const rvolSuffix = tech.rvolSignal === "alto" ? ` · RVOL ${tech.rvol?.toFixed(1)}x (convicção forte)`
      : tech.rvolSignal === "baixo" ? ` · RVOL ${tech.rvol?.toFixed(1)}x (sinal fraco)` : "";
    if (tech.rsi >= 70) return { label: `RSI ${tech.rsi.toFixed(0)} — esticado pra cima, bom momento de venda${rvolSuffix}`, tone: "bom" };
    if (tech.rsi <= 30) return { label: `RSI ${tech.rsi.toFixed(0)} — sobrevendido, ainda sem força de repique${rvolSuffix}`, tone: "atencao" };
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
