import { 
  useGetLatestReport, 
  getGetLatestReportQueryKey,
  useGetObservationsSummary,
  getGetObservationsSummaryQueryKey,
  useGetTickerQuotes,
  getGetTickerQuotesQueryKey,
} from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, TrendingUp, TrendingDown, Minus, RefreshCw } from "lucide-react";

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtVol(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toString();
}

function QuoteCard({ symbol, price, change, changePct, open, previousClose, dayHigh, dayLow, volume, error }: {
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
}) {
  const positive = change != null && change >= 0;
  const negative = change != null && change < 0;

  return (
    <div className="border border-border rounded-lg p-4 bg-card font-mono">
      <div className="flex items-start justify-between mb-3">
        <div>
          <span className="text-primary font-bold text-lg tracking-widest">{symbol}</span>
          {error && <p className="text-xs text-red-400 mt-0.5 font-sans">{error}</p>}
        </div>
        <div className="text-right">
          <div className="text-xl font-bold text-foreground">
            {price != null ? `$${fmt(price)}` : "—"}
          </div>
          {changePct != null && (
            <div className={`flex items-center gap-1 justify-end text-sm font-bold ${positive ? "text-green-400" : negative ? "text-red-400" : "text-muted-foreground"}`}>
              {positive ? <TrendingUp className="h-3.5 w-3.5" /> : negative ? <TrendingDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
              {positive ? "+" : ""}{fmt(changePct)}%
              <span className="text-xs font-normal opacity-70">({positive ? "+" : ""}{fmt(change)})</span>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mt-3 pt-3 border-t border-border/50">
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Open</div>
          <div className="text-xs text-foreground">{price != null ? `$${fmt(open)}` : "—"}</div>
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
    </div>
  );
}

export default function Dashboard() {
  const { data: report, isLoading: loadingReport } = useGetLatestReport({
    query: {
      queryKey: getGetLatestReportQueryKey(),
      retry: false
    }
  });

  const { data: summary, isLoading: loadingSummary } = useGetObservationsSummary({
    query: {
      queryKey: getGetObservationsSummaryQueryKey()
    }
  });

  const { data: quotes, isLoading: loadingQuotes, dataUpdatedAt } = useGetTickerQuotes({
    query: {
      queryKey: getGetTickerQuotesQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    }
  });

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">DASHBOARD</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">Latest intelligence & sentiment summary</p>
        </div>
      </div>

      {/* Real-time Quotes */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">Cotações</h2>
          {updatedTime && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground">
              <RefreshCw className="h-3 w-3" />
              atualizado às {updatedTime}
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
              <QuoteCard key={q.symbol} {...q} />
            ))}
          </div>
        ) : (
          <div className="border border-dashed border-border rounded-lg p-6 text-center">
            <p className="text-xs font-mono text-muted-foreground">Sem dados de cotação disponíveis.</p>
          </div>
        )}
      </div>

      {/* Sentiment Summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {loadingSummary ? (
          <>
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </>
        ) : summary?.map(s => (
          <Card key={s.ticker} className="bg-card border-border shadow-none rounded-sm">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-2xl font-mono text-primary">{s.ticker}</CardTitle>
                {s.lastSentiment === "bullish" && <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20 font-mono"><TrendingUp className="w-3 h-3 mr-1"/> BULLISH</Badge>}
                {s.lastSentiment === "bearish" && <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20 font-mono"><TrendingDown className="w-3 h-3 mr-1"/> BEARISH</Badge>}
                {s.lastSentiment === "neutral" && <Badge variant="outline" className="bg-slate-500/10 text-slate-400 border-slate-500/20 font-mono"><Minus className="w-3 h-3 mr-1"/> NEUTRAL</Badge>}
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 mt-4 text-center">
                <div className="bg-secondary/50 p-2 rounded-sm border border-border">
                  <div className="text-xs text-muted-foreground font-mono mb-1">BULL</div>
                  <div className="text-lg font-mono font-bold text-green-500">{s.bullish}</div>
                </div>
                <div className="bg-secondary/50 p-2 rounded-sm border border-border">
                  <div className="text-xs text-muted-foreground font-mono mb-1">BEAR</div>
                  <div className="text-lg font-mono font-bold text-red-500">{s.bearish}</div>
                </div>
                <div className="bg-secondary/50 p-2 rounded-sm border border-border">
                  <div className="text-xs text-muted-foreground font-mono mb-1">NEUTRAL</div>
                  <div className="text-lg font-mono font-bold text-slate-400">{s.neutral}</div>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Latest Report */}
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
                {report.tickers.map(t => (
                  <Badge key={t} variant="secondary" className="font-mono bg-secondary border-border">{t}</Badge>
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
            <p className="text-xs text-muted-foreground font-mono mt-2">Run the agent to generate a report.</p>
          </div>
        )}
      </div>
    </div>
  );
}
