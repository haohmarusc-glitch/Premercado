import { 
  useGetLatestReport, 
  getGetLatestReportQueryKey,
  useGetObservationsSummary,
  getGetObservationsSummaryQueryKey
} from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, TrendingUp, TrendingDown, Minus } from "lucide-react";

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

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">DASHBOARD</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">Latest intelligence & sentiment summary</p>
        </div>
      </div>

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
