import {
  useListObservations,
  getListObservationsQueryKey,
} from "@workspace/api-client-react";
import { CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useState, useMemo } from "react";
import { TrendingUp, TrendingDown, Minus, X } from "lucide-react";

// Sector groups matching sector_contagion.py
const SECTOR_GROUPS = [
  { key: "memory",      label: "Memória",       tickers: ["MU", "SNDK", "WDC"] },
  { key: "interconnect",label: "Interconexão",  tickers: ["SMCI", "ALAB", "CRDO", "ANET"] },
  { key: "power",       label: "Energia",       tickers: ["VRT"] },
  { key: "foundry",     label: "Fundição",      tickers: ["TSM", "ASML"] },
  { key: "coal",        label: "Carvão",        tickers: ["HCC", "AMR", "ARCH", "CEIX", "BTU"] },
  { key: "other",       label: "Outros",        tickers: ["NVDA", "INTC", "GOOGL", "ARM", "TSLA"] },
];

function sentimentTickers(ticker: string): string {
  return SECTOR_GROUPS.find((g) => g.tickers.includes(ticker))?.label ?? "Outros";
}

function SentimentBadge({ sentiment }: { sentiment: string }) {
  if (sentiment === "bullish") {
    return (
      <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20 font-mono text-[10px]">
        <TrendingUp className="w-3 h-3 mr-1" /> BULLISH
      </Badge>
    );
  }
  if (sentiment === "bearish") {
    return (
      <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20 font-mono text-[10px]">
        <TrendingDown className="w-3 h-3 mr-1" /> BEARISH
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="bg-slate-500/10 text-slate-400 border-slate-500/20 font-mono text-[10px]">
      <Minus className="w-3 h-3 mr-1" /> NEUTRAL
    </Badge>
  );
}

export default function Observations() {
  const [sectorFilter, setSectorFilter] = useState<string>("all");
  const [tickerFilter, setTickerFilter] = useState<string | undefined>(undefined);

  // Fetch all observations client-side for flexible filtering
  const { data: observations, isLoading } = useListObservations(
    { limit: 300 },
    { query: { queryKey: getListObservationsQueryKey({ limit: 300 }) } },
  );

  const activeSector = SECTOR_GROUPS.find((g) => g.key === sectorFilter);

  // Tickers present in observations (may differ from settings)
  const presentTickers = useMemo(
    () => [...new Set(observations?.map((o) => o.ticker) ?? [])].sort(),
    [observations],
  );

  // Build "Outros" dynamically: any ticker not covered by the first 4 fixed groups
  const fixedTickers = new Set(SECTOR_GROUPS.flatMap((g) => g.tickers));
  const dynamicOthers = presentTickers.filter((t) => !fixedTickers.has(t));
  const sectorsWithDynamic = SECTOR_GROUPS.map((g) =>
    g.key === "other" ? { ...g, tickers: [...g.tickers, ...dynamicOthers] } : g,
  );

  // Tickers available inside the active sector
  const tickersInView: string[] =
    sectorFilter === "all"
      ? presentTickers
      : (sectorsWithDynamic.find((g) => g.key === sectorFilter)?.tickers ?? []).filter((t) =>
          presentTickers.includes(t),
        );

  // Count observations per ticker
  const countByTicker = useMemo(
    () =>
      (observations ?? []).reduce<Record<string, number>>((acc, o) => {
        acc[o.ticker] = (acc[o.ticker] ?? 0) + 1;
        return acc;
      }, {}),
    [observations],
  );

  // Filtered list — uses sectorsWithDynamic so "Outros" includes dynamic tickers
  const filtered = useMemo(() => {
    const sectorTickers = sectorsWithDynamic.find((g) => g.key === sectorFilter)?.tickers ?? [];
    return (observations ?? []).filter((obs) => {
      if (tickerFilter) return obs.ticker === tickerFilter;
      if (sectorFilter === "all") return true;
      return sectorTickers.includes(obs.ticker);
    });
  }, [observations, tickerFilter, sectorFilter, sectorsWithDynamic]);

  // Group by date
  const grouped = useMemo(() => {
    const map = new Map<string, typeof filtered>();
    for (const obs of filtered) {
      const d = obs.date ?? obs.createdAt.split("T")[0];
      if (!map.has(d)) map.set(d, []);
      map.get(d)!.push(obs);
    }
    return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [filtered]);

  function handleSectorClick(key: string) {
    setSectorFilter(key);
    setTickerFilter(undefined);
  }

  function handleTickerClick(t: string) {
    setTickerFilter(tickerFilter === t ? undefined : t);
  }

  const sectorCounts = useMemo(() => {
    const result: Record<string, number> = { all: observations?.length ?? 0 };
    for (const g of sectorsWithDynamic) {
      result[g.key] = (observations ?? []).filter((o) => g.tickers.includes(o.ticker)).length;
    }
    return result;
  }, [observations, sectorsWithDynamic]);

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">OBSERVAÇÕES</h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Memória do agente — insights por ativo e setor
        </p>
        {observations && observations.length > 0 && (() => {
          const bull = observations.filter((o) => o.sentiment === "bullish").length;
          const bear = observations.filter((o) => o.sentiment === "bearish").length;
          const neu = observations.filter((o) => o.sentiment === "neutral").length;
          return (
            <div className="flex gap-3 mt-3">
              <span className="flex items-center gap-1 text-xs font-mono text-green-500">
                <TrendingUp className="h-3 w-3" /> {bull} BULL
              </span>
              <span className="flex items-center gap-1 text-xs font-mono text-red-500">
                <TrendingDown className="h-3 w-3" /> {bear} BEAR
              </span>
              <span className="flex items-center gap-1 text-xs font-mono text-slate-400">
                <Minus className="h-3 w-3" /> {neu} NEU
              </span>
              <span className="text-xs font-mono text-muted-foreground">· {observations.length} total</span>
            </div>
          );
        })()}
      </div>

      {/* ── Sector filter ── */}
      <div className="space-y-3">
        <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">Setor</p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => handleSectorClick("all")}
            className={`px-3 py-1.5 rounded-md font-mono text-xs font-bold transition-colors border ${
              sectorFilter === "all"
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
            }`}
          >
            Todos
            <span className="ml-1.5 opacity-70">({sectorCounts.all})</span>
          </button>
          {sectorsWithDynamic.map((g) => {
            const count = sectorCounts[g.key] ?? 0;
            if (count === 0) return null;
            return (
              <button
                key={g.key}
                type="button"
                onClick={() => handleSectorClick(g.key)}
                className={`px-3 py-1.5 rounded-md font-mono text-xs font-bold transition-colors border ${
                  sectorFilter === g.key
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
                }`}
              >
                {g.label}
                <span className="ml-1.5 opacity-70">({count})</span>
              </button>
            );
          })}
        </div>

        {/* ── Ticker filter within sector ── */}
        {tickersInView.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border/40 items-center">
            {tickerFilter && (
              <button
                type="button"
                onClick={() => setTickerFilter(undefined)}
                className="flex items-center gap-1 px-2 py-0.5 rounded font-mono text-xs text-muted-foreground hover:text-foreground border border-dashed border-border/60 hover:border-border transition-colors"
              >
                <X className="h-3 w-3" /> limpar
              </button>
            )}
            {tickersInView.map((t) => {
              const count = countByTicker[t] ?? 0;
              if (count === 0) return null;
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => handleTickerClick(t)}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded font-mono text-xs font-bold transition-colors border ${
                    tickerFilter === t
                      ? "bg-primary/20 border-primary text-primary"
                      : "bg-secondary border-border/60 text-muted-foreground hover:text-primary hover:border-primary/40"
                  }`}
                >
                  {t}
                  <span
                    className={`text-[10px] px-1 rounded-sm ${
                      tickerFilter === t ? "bg-primary/20 text-primary" : "bg-border/60 text-muted-foreground"
                    }`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Feed ── */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : grouped.length === 0 ? (
        <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
          Nenhuma observação encontrada.
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(([date, obs]) => (
            <div key={date}>
              {/* Date separator */}
              <div className="flex items-center gap-3 mb-3">
                <span className="text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  {date}
                </span>
                <div className="flex-1 border-t border-border/40" />
                <span className="text-[10px] font-mono text-muted-foreground">{obs.length} obs.</span>
              </div>

              <div className="space-y-2">
                {obs.map((o) => (
                  <div
                    key={o.id}
                    className="border border-border rounded-lg bg-card hover:border-border/80 transition-colors"
                  >
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Badge
                            variant="outline"
                            className="font-mono bg-secondary/50 border-border text-primary font-bold text-xs"
                          >
                            {o.ticker}
                          </Badge>
                          <span className="text-[10px] font-mono text-muted-foreground bg-secondary border border-border/60 px-1.5 py-0.5 rounded">
                            {sentimentTickers(o.ticker)}
                          </span>
                          <span className="text-xs text-muted-foreground font-mono">
                            {formatDateTime(o.createdAt)}
                          </span>
                          {o.priceAtObservation != null && (
                            <span className="text-xs font-mono text-muted-foreground border border-border px-1.5 py-0.5 rounded-sm">
                              ${o.priceAtObservation.toFixed(2)}
                            </span>
                          )}
                        </div>
                        <SentimentBadge sentiment={o.sentiment} />
                      </div>
                      <div className="font-mono text-sm leading-relaxed text-foreground border-l-2 border-primary/50 pl-3 py-0.5">
                        {o.summary}
                      </div>
                    </CardContent>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
