import { useState, useEffect, useMemo } from "react";
import {
  useGetLatestReport,
  getGetLatestReportQueryKey,
  useGetObservationsSummary,
  getGetObservationsSummaryQueryKey,
  useGetTickerQuotes,
  getGetTickerQuotesQueryKey,
  useGetTickerChart,
  getGetTickerChartQueryKey,
  useGetAlertFiringsSummary,
  getGetAlertFiringsSummaryQueryKey,
  useListReports,
  getListReportsQueryKey,
} from "@workspace/api-client-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, TrendingUp, TrendingDown, Minus, RefreshCw, Bell, BellRing, Zap, ChevronDown, ChevronRight } from "lucide-react";
import { Link } from "wouter";

// ─── helpers ────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtVol(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toString();
}

function fmtLabel(ts: number, period: string) {
  const d = new Date(ts);
  if (period === "1d") return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (period === "5d") return d.toLocaleDateString("en-US", { weekday: "short", hour: "2-digit", hour12: false });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const PERIODS = [
  { key: "1d",  label: "1D" },
  { key: "5d",  label: "5D" },
  { key: "1mo", label: "1M" },
  { key: "3mo", label: "3M" },
  { key: "6mo", label: "6M" },
  { key: "1y",  label: "1Y" },
];

// ─── Sector groups (mirrors sector_contagion.py + observations.tsx) ─────────

const SECTOR_GROUPS = [
  { key: "memory",       label: "Memória",      tickers: ["MU", "SNDK", "WDC"] },
  { key: "interconnect", label: "Interconexão", tickers: ["SMCI", "ALAB", "CRDO", "ANET"] },
  { key: "power",        label: "Energia",      tickers: ["VRT"] },
  { key: "foundry",      label: "Fundição",     tickers: ["TSM", "ASML"] },
  { key: "other",        label: "Outros",       tickers: ["NVDA", "INTC", "GOOGL", "ARM", "TSLA"] },
];

// ─── Compact sentiment card ───────────────────────────────────────────────────

interface SentimentItem {
  ticker: string;
  bullish: number;
  bearish: number;
  neutral: number;
  lastSentiment: string;
  lastDate: string;
}

function SentimentDot({ sentiment }: { sentiment: string }) {
  const cls =
    sentiment === "bullish" ? "bg-green-500" :
    sentiment === "bearish" ? "bg-red-500" : "bg-slate-500";
  return <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cls}`} />;
}

function SentimentMiniCard({ s }: { s: SentimentItem }) {
  const total = s.bullish + s.bearish + s.neutral || 1;
  const bullPct  = Math.round((s.bullish  / total) * 100);
  const bearPct  = Math.round((s.bearish  / total) * 100);
  const neutralPct = Math.round((s.neutral / total) * 100);

  return (
    <div className="border border-border rounded-lg p-3 bg-card font-mono hover:border-border/80 transition-colors">
      <div className="flex items-center justify-between mb-2.5">
        <span className="font-bold text-primary text-sm tracking-widest">{s.ticker}</span>
        <SentimentDot sentiment={s.lastSentiment} />
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-green-500 w-7 font-bold">BULL</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-green-500 rounded-full" style={{ width: `${bullPct}%` }} />
          </div>
          <span className="text-[10px] text-green-500 w-4 text-right font-bold">{s.bullish}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-red-500 w-7 font-bold">BEAR</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-red-500 rounded-full" style={{ width: `${bearPct}%` }} />
          </div>
          <span className="text-[10px] text-red-500 w-4 text-right font-bold">{s.bearish}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-400 w-7">NEU</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-slate-500 rounded-full" style={{ width: `${neutralPct}%` }} />
          </div>
          <span className="text-[10px] text-slate-400 w-4 text-right">{s.neutral}</span>
        </div>
      </div>
      <div className="text-[9px] text-muted-foreground mt-2 border-t border-border/40 pt-1.5 truncate">
        {s.lastDate}
      </div>
    </div>
  );
}

// ─── QuoteCard ───────────────────────────────────────────────────────────────

interface QuoteCardProps {
  symbol: string;
  price?: number | null;
  change?: number | null;
  changePct?: number | null;
  open?: number | null;
  previousClose?: number | null;
  dayHigh?: number | null;
  dayLow?: number | null;
  volume?: number | null;
  error?: string | null;
  selected: boolean;
  onSelect: () => void;
}

function QuoteCard({
  symbol, price, change, changePct, open, previousClose,
  dayHigh, dayLow, volume, error, selected, onSelect,
}: QuoteCardProps) {
  const positive = change != null && change >= 0;
  const negative = change != null && change < 0;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left border rounded-lg p-4 font-mono transition-all duration-200 ${
        selected
          ? "border-primary bg-primary/10 ring-1 ring-primary/40"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}
      data-testid={`quote-card-${symbol}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <span className={`font-bold text-lg tracking-widest ${selected ? "text-primary" : "text-primary"}`}>
            {symbol}
          </span>
          {selected && (
            <span className="ml-2 text-[10px] text-primary/70 uppercase font-mono">● selecionado</span>
          )}
          {error && <p className="text-xs text-red-400 mt-0.5 font-sans">{error}</p>}
        </div>
        <div className="text-right">
          <div className="text-xl font-bold text-foreground">
            {price != null ? `$${fmt(price)}` : "—"}
          </div>
          {changePct != null && (
            <div
              className={`flex items-center gap-1 justify-end text-sm font-bold ${
                positive ? "text-green-400" : negative ? "text-red-400" : "text-muted-foreground"
              }`}
            >
              {positive ? <TrendingUp className="h-3.5 w-3.5" /> : negative ? <TrendingDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
              {positive ? "+" : ""}{fmt(changePct)}%
              <span className="text-xs font-normal opacity-70">
                ({positive ? "+" : ""}{fmt(change)})
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 pt-3 border-t border-border/50">
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Open</div>
          <div className="text-xs text-foreground">{open != null ? `$${fmt(open)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Prev</div>
          <div className="text-xs text-foreground">{previousClose != null ? `$${fmt(previousClose)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Hi / Lo</div>
          <div className="text-xs text-green-400">{dayHigh != null ? `$${fmt(dayHigh)}` : "—"}</div>
          <div className="text-xs text-red-400">{dayLow != null ? `$${fmt(dayLow)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Volume</div>
          <div className="text-xs text-foreground">{fmtVol(volume)}</div>
        </div>
      </div>
    </button>
  );
}

// ─── PriceChart ──────────────────────────────────────────────────────────────

function PriceChart({ symbol, period }: { symbol: string; period: string }) {
  const { data, isLoading } = useGetTickerChart(
    { symbol, period },
    {
      query: {
        queryKey: getGetTickerChartQueryKey({ symbol, period }),
        staleTime: 55_000,
      },
    },
  );

  const candles = data?.candles ?? [];
  const chartData = candles.map((c) => ({ t: c.t, price: c.c, label: fmtLabel(c.t, period) }));

  const prices = chartData.map((d) => d.price).filter(Boolean) as number[];
  const minP = prices.length ? Math.min(...prices) : 0;
  const maxP = prices.length ? Math.max(...prices) : 0;
  const pad = (maxP - minP) * 0.05 || 1;
  const domain: [number, number] = [minP - pad, maxP + pad];

  const first = prices[0];
  const last = prices[prices.length - 1];
  const up = last != null && first != null && last >= first;
  const color = up ? "#22c55e" : "#ef4444";

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs font-mono text-muted-foreground animate-pulse">Carregando gráfico...</span>
      </div>
    );
  }

  if (!chartData.length) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs font-mono text-muted-foreground">Sem dados para este período.</span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`grad-${symbol}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
          minTickGap={60}
        />
        <YAxis
          domain={domain}
          tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          width={60}
          tickFormatter={(v: number) => `$${fmt(v)}`}
        />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: "6px",
            fontFamily: "monospace",
            fontSize: "12px",
          }}
          labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
          itemStyle={{ color }}
          formatter={(val: number) => [`$${fmt(val)}`, "Price"]}
        />
        <Area
          type="monotone"
          dataKey="price"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#grad-${symbol})`}
          dot={false}
          activeDot={{ r: 3, fill: color }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [period, setPeriod] = useState("1d");
  const [expandedFlashId, setExpandedFlashId] = useState<number | null>(null);
  const [sectorTab, setSectorTab] = useState<string>("all");

  const { data: report, isLoading: loadingReport } = useGetLatestReport({
    query: { queryKey: getGetLatestReportQueryKey(), retry: false },
  });

  const today = new Date().toISOString().split("T")[0];
  const { data: allReports } = useListReports({
    query: {
      queryKey: getListReportsQueryKey(),
      staleTime: 30_000,
      refetchInterval: 60_000,
    },
  });
  const flashToday = allReports
    ?.filter((r) => r.mode === "premarket" && r.date === today)
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()) ?? [];

  const { data: summary, isLoading: loadingSummary } = useGetObservationsSummary({
    query: { queryKey: getGetObservationsSummaryQueryKey() },
  });

  const filteredSummary = useMemo(() => {
    if (!summary) return [];
    if (sectorTab === "all") return summary;
    const group = SECTOR_GROUPS.find((g) => g.key === sectorTab);
    return group ? summary.filter((s) => group.tickers.includes(s.ticker)) : summary;
  }, [summary, sectorTab]);

  const sectorAggregate = useMemo(() => {
    if (!filteredSummary.length) return null;
    return filteredSummary.reduce(
      (acc, s) => ({ bull: acc.bull + s.bullish, bear: acc.bear + s.bearish, neutral: acc.neutral + s.neutral }),
      { bull: 0, bear: 0, neutral: 0 },
    );
  }, [filteredSummary]);

  const { data: alertsSummary } = useGetAlertFiringsSummary({
    query: {
      queryKey: getGetAlertFiringsSummaryQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    },
  });

  const { data: quotes, isLoading: loadingQuotes, dataUpdatedAt } = useGetTickerQuotes({
    query: {
      queryKey: getGetTickerQuotesQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    },
  });

  // Auto-select first ticker when quotes load
  useEffect(() => {
    if (!selectedSymbol && quotes && quotes.length > 0) {
      setSelectedSymbol(quotes[0].symbol);
    }
  }, [quotes, selectedSymbol]);

  const activeSymbol = selectedSymbol ?? (quotes?.[0]?.symbol ?? null);

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : null;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">DASHBOARD</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Latest intelligence & sentiment summary
          </p>
        </div>
      </div>

      {/* ── Quote cards (selectable) ── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
            Cotações — clique para ver o gráfico
          </h2>
          {updatedTime && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground">
              <RefreshCw className="h-3 w-3" />
              {updatedTime}
            </span>
          )}
        </div>

        {loadingQuotes ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : quotes && quotes.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {quotes.map((q) => (
              <QuoteCard
                key={q.symbol}
                {...q}
                selected={q.symbol === activeSymbol}
                onSelect={() => setSelectedSymbol(q.symbol)}
              />
            ))}
          </div>
        ) : (
          <div className="border border-dashed border-border rounded-lg p-6 text-center">
            <p className="text-xs font-mono text-muted-foreground">
              Sem dados de cotação. Adicione tickers em Settings.
            </p>
          </div>
        )}
      </div>

      {/* ── Alerts summary ── */}
      {alertsSummary && alertsSummary.total > 0 && (
        <Link href="/alerts">
          <div className="border border-border rounded-lg bg-card hover:border-primary/40 transition-colors cursor-pointer">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50">
              <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground uppercase tracking-widest">
                <Bell className="h-3.5 w-3.5" />
                Alertas de Preço
              </div>
              {alertsSummary.firingToday > 0 && (
                <div className="flex items-center gap-1.5 text-[11px] font-mono text-red-400 animate-pulse">
                  <BellRing className="h-3.5 w-3.5" />
                  {alertsSummary.firingToday} disparado{alertsSummary.firingToday > 1 ? "s" : ""} hoje
                </div>
              )}
            </div>
            <div className="grid grid-cols-3 divide-x divide-border/50">
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Total</div>
                <div className="text-xl font-bold font-mono">{alertsSummary.total}</div>
              </div>
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Ativos</div>
                <div className={`text-xl font-bold font-mono ${alertsSummary.active > 0 ? "text-primary" : "text-muted-foreground"}`}>
                  {alertsSummary.active}
                </div>
              </div>
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1 flex items-center justify-center gap-1">
                  <Zap className="h-2.5 w-2.5" />
                  Hoje
                </div>
                <div className={`text-xl font-bold font-mono ${alertsSummary.firingToday > 0 ? "text-red-400" : "text-muted-foreground"}`}>
                  {alertsSummary.firingToday}
                </div>
              </div>
            </div>
          </div>
        </Link>
      )}

      {/* ── Chart ── */}
      {activeSymbol && (
        <div className="border border-border rounded-lg overflow-hidden bg-card">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-secondary/30">
            <span className="font-mono font-bold text-primary text-sm tracking-widest">
              {activeSymbol} — Histórico de preço
            </span>

            {/* Period selector */}
            <div className="flex items-center gap-1">
              {PERIODS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => setPeriod(p.key)}
                  className={`px-2.5 py-1 rounded text-[11px] font-mono font-bold transition-colors ${
                    period === p.key
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                  }`}
                  data-testid={`period-btn-${p.key}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Chart body */}
          <div className="p-4">
            <PriceChart symbol={activeSymbol} period={period} />
          </div>
        </div>
      )}

      {/* ── Sentiment summary ── */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
            Sentimento por Ativo
          </h2>
          {summary && (
            <span className="text-[10px] font-mono text-muted-foreground border border-border px-1.5 py-0.5 rounded">
              {filteredSummary.length}/{summary.length}
            </span>
          )}
        </div>

        {/* Sector filter */}
        {!loadingSummary && summary && summary.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            <button
              type="button"
              onClick={() => setSectorTab("all")}
              className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold transition-colors border ${
                sectorTab === "all"
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
              }`}
            >
              Todos
              <span className="ml-1 opacity-70">({summary.length})</span>
            </button>
            {SECTOR_GROUPS.map((g) => {
              const count = summary.filter((s) => g.tickers.includes(s.ticker)).length;
              if (count === 0) return null;
              return (
                <button
                  key={g.key}
                  type="button"
                  onClick={() => setSectorTab(g.key)}
                  className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold transition-colors border ${
                    sectorTab === g.key
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
                  }`}
                >
                  {g.label}
                  <span className="ml-1 opacity-70">({count})</span>
                </button>
              );
            })}
          </div>
        )}

        {/* Sector aggregate row */}
        {sectorTab !== "all" && sectorAggregate && filteredSummary.length > 0 && (
          <div className="flex items-center gap-4 mb-3 px-3 py-2 border border-border/40 rounded-lg bg-secondary/20 font-mono text-xs">
            <span className="text-muted-foreground uppercase tracking-widest text-[10px]">Setor</span>
            <span className="text-green-500 font-bold">BULL {sectorAggregate.bull}</span>
            <span className="text-red-500 font-bold">BEAR {sectorAggregate.bear}</span>
            <span className="text-slate-400">NEU {sectorAggregate.neutral}</span>
            <span className={`ml-auto font-bold text-[11px] ${
              sectorAggregate.bull > sectorAggregate.bear ? "text-green-500" :
              sectorAggregate.bear > sectorAggregate.bull ? "text-red-500" : "text-slate-400"
            }`}>
              {sectorAggregate.bull > sectorAggregate.bear ? "↑ BULLISH" :
               sectorAggregate.bear > sectorAggregate.bull ? "↓ BEARISH" : "→ NEUTRO"}
            </span>
          </div>
        )}

        {loadingSummary ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        ) : filteredSummary.length > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {filteredSummary.map((s) => (
              <SentimentMiniCard key={s.ticker} s={s} />
            ))}
          </div>
        ) : (
          <div className="p-8 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
            Sem dados de sentimento.
          </div>
        )}
      </div>

      {/* ── Flash Scans de Hoje ── */}
      {flashToday.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Zap className="h-3.5 w-3.5 text-primary" />
            <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
              Flash Scans de Hoje
            </h2>
            <span className="px-1.5 py-0.5 rounded bg-primary/10 border border-primary/30 text-primary font-mono text-[10px]">
              {flashToday.length}
            </span>
          </div>

          <div className="space-y-2">
            {flashToday.map((scan) => {
              const isOpen = expandedFlashId === scan.id;
              const time = new Date(scan.createdAt).toLocaleTimeString("pt-BR", {
                hour: "2-digit",
                minute: "2-digit",
                timeZone: "America/Sao_Paulo",
              });
              return (
                <div
                  key={scan.id}
                  className="border border-primary/20 rounded-lg overflow-hidden bg-card"
                >
                  <button
                    type="button"
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-primary/5 transition-colors text-left"
                    onClick={() => setExpandedFlashId(isOpen ? null : scan.id)}
                  >
                    <div className="flex items-center gap-2">
                      {isOpen
                        ? <ChevronDown className="h-3.5 w-3.5 text-primary flex-shrink-0" />
                        : <ChevronRight className="h-3.5 w-3.5 text-primary flex-shrink-0" />}
                      <span className="font-mono text-primary font-bold text-sm">⚡ {time} BRT</span>
                    </div>
                    <span className="text-xs font-mono text-muted-foreground">
                      Flash Scan
                    </span>
                  </button>
                  {isOpen && (
                    <div className="border-t border-primary/10 px-5 py-4 bg-background">
                      <MarkdownContent content={scan.content} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Latest report ── */}
      <div>
        <h2 className="text-sm font-mono text-muted-foreground mb-4">LATEST PRE-MARKET REPORT</h2>
        {loadingReport ? (
          <div className="space-y-4">
            <Skeleton className="h-8 w-1/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : report ? (
          <Card className="bg-card border-border shadow-none rounded-sm">
            <CardHeader className="border-b border-border bg-secondary/30 pb-4">
              <div className="flex items-center justify-between">
                <CardTitle className="font-mono text-lg">{report.date}</CardTitle>
                <div className="text-xs text-muted-foreground font-mono">
                  {formatDateTime(report.createdAt)}
                </div>
              </div>
              <div className="flex gap-2 mt-2">
                {report.tickers.map((t) => (
                  <Badge key={t} variant="secondary" className="font-mono bg-secondary border-border">
                    {t}
                  </Badge>
                ))}
              </div>
            </CardHeader>
            <CardContent className="pt-6">
              <MarkdownContent content={report.content} />
            </CardContent>
          </Card>
        ) : (
          <div className="p-12 text-center border border-dashed border-border rounded-sm bg-secondary/20">
            <AlertTriangle className="h-8 w-8 text-muted-foreground mx-auto mb-4" />
            <p className="text-muted-foreground font-mono">No reports available for today.</p>
            <p className="text-xs text-muted-foreground font-mono mt-2">
              Run the agent to generate a report.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
