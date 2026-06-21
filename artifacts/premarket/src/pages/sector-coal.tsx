import { useListObservations, getListObservationsQueryKey, useGetAgentStatus, getGetAgentStatusQueryKey } from "@workspace/api-client-react";
import { CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useState, useMemo, useRef, useEffect } from "react";
import { TrendingUp, TrendingDown, Minus, X, Zap } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";

const COAL_TICKERS = ["HCC", "AMR", "ARCH", "CEIX", "BTU"];

function SentimentBadge({ sentiment }: { sentiment: string }) {
  if (sentiment === "bullish")
    return (
      <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20 font-mono text-[10px]">
        <TrendingUp className="w-3 h-3 mr-1" /> BULLISH
      </Badge>
    );
  if (sentiment === "bearish")
    return (
      <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20 font-mono text-[10px]">
        <TrendingDown className="w-3 h-3 mr-1" /> BEARISH
      </Badge>
    );
  return (
    <Badge variant="outline" className="bg-slate-500/10 text-slate-400 border-slate-500/20 font-mono text-[10px]">
      <Minus className="w-3 h-3 mr-1" /> NEUTRAL
    </Badge>
  );
}

export default function SectorCoal() {
  const [tickerFilter, setTickerFilter] = useState<string | undefined>(undefined);
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data: status } = useGetAgentStatus({
    query: { queryKey: getGetAgentStatusQueryKey(), refetchInterval: 5000 },
  });
  const isRunning = status?.running;

  const runCoal = useMutation({
    mutationFn: () =>
      fetch("/api/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "coal" }),
      }).then((r) => r.json()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
      toast({ title: "⚡ Análise de Carvão iniciada", description: "HCC · AMR · ARCH · CEIX · BTU em análise." });
    },
  });

  const coalTickersParam = COAL_TICKERS.join(",");
  const obsQueryKey = getListObservationsQueryKey({ tickers: coalTickersParam, limit: 500 });
  const { data: observations, isLoading, refetch } = useListObservations(
    { tickers: coalTickersParam, limit: 500 },
    { query: { queryKey: obsQueryKey, refetchInterval: isRunning ? 5000 : false } },
  );

  const wasRunningRef = useRef(isRunning);
  useEffect(() => {
    if (wasRunningRef.current && !isRunning) {
      void refetch();
    }
    wasRunningRef.current = isRunning;
  }, [isRunning, refetch]);

  const sectorObs = useMemo(() => observations ?? [], [observations]);

  const filtered = useMemo(
    () => (tickerFilter ? sectorObs.filter((o) => o.ticker === tickerFilter) : sectorObs),
    [sectorObs, tickerFilter],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, typeof filtered>();
    for (const obs of filtered) {
      const d = obs.date ?? obs.createdAt.split("T")[0];
      if (!map.has(d)) map.set(d, []);
      map.get(d)!.push(obs);
    }
    return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [filtered]);

  const countByTicker = useMemo(
    () =>
      sectorObs.reduce<Record<string, number>>((acc, o) => {
        acc[o.ticker] = (acc[o.ticker] ?? 0) + 1;
        return acc;
      }, {}),
    [sectorObs],
  );

  const bull = sectorObs.filter((o) => o.sentiment === "bullish").length;
  const bear = sectorObs.filter((o) => o.sentiment === "bearish").length;
  const neu  = sectorObs.filter((o) => o.sentiment === "neutral").length;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Header */}
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">CARVÃO</h1>
          <p className="text-muted-foreground font-mono text-sm mt-1">
            HCC · AMR · ARCH · CEIX · BTU — análise profunda do setor
          </p>
          {sectorObs.length > 0 && (
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
              <span className="text-xs font-mono text-muted-foreground">· {sectorObs.length} total</span>
            </div>
          )}
        </div>
        <button
          onClick={() => runCoal.mutate()}
          disabled={isRunning || runCoal.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
        >
          <Zap className="h-3.5 w-3.5" />
          {isRunning ? "ANALISANDO..." : "ANALISAR CARVÃO"}
        </button>
      </div>

      {/* Ticker filter */}
      <div className="flex flex-wrap gap-1.5 items-center">
        {tickerFilter && (
          <button
            type="button"
            onClick={() => setTickerFilter(undefined)}
            className="flex items-center gap-1 px-2 py-0.5 rounded font-mono text-xs text-muted-foreground hover:text-foreground border border-dashed border-border/60 hover:border-border transition-colors"
          >
            <X className="h-3 w-3" /> limpar
          </button>
        )}
        {COAL_TICKERS.map((t) => {
          const count = countByTicker[t] ?? 0;
          if (count === 0) return null;
          return (
            <button
              key={t}
              type="button"
              onClick={() => setTickerFilter(tickerFilter === t ? undefined : t)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded font-mono text-xs font-bold transition-colors border ${
                tickerFilter === t
                  ? "bg-primary/20 border-primary text-primary"
                  : "bg-secondary border-border/60 text-muted-foreground hover:text-primary hover:border-primary/40"
              }`}
            >
              {t}
              <span className={`text-[10px] px-1 rounded-sm ${tickerFilter === t ? "bg-primary/20 text-primary" : "bg-border/60 text-muted-foreground"}`}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Feed */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
        </div>
      ) : grouped.length === 0 ? (
        <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
          Nenhuma observação para o setor de carvão. Clique em <strong>ANALISAR CARVÃO</strong> para gerar.
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(([date, obs]) => (
            <div key={date}>
              <div className="flex items-center gap-3 mb-3">
                <span className="text-xs font-mono text-muted-foreground uppercase tracking-widest">{date}</span>
                <div className="flex-1 border-t border-border/40" />
                <span className="text-[10px] font-mono text-muted-foreground">{obs.length} obs.</span>
              </div>
              <div className="space-y-2">
                {obs.map((o) => (
                  <div key={o.id} className="border border-border rounded-lg bg-card hover:border-border/80 transition-colors">
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold text-xs">
                            {o.ticker}
                          </Badge>
                          <span className="text-xs text-muted-foreground font-mono">{formatDateTime(o.createdAt)}</span>
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
