import { 
  useListReports, 
  getListReportsQueryKey 
} from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export default function History() {
  const { data: reports, isLoading } = useListReports({
    query: {
      queryKey: getListReportsQueryKey()
    }
  });

  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">HISTORY</h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Archive of all pre-market intelligence reports</p>
      </div>

      <div className="space-y-4">
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))
        ) : !reports?.length ? (
          <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono">
            No historical reports found.
          </div>
        ) : (
          reports.map(report => {
            const isExpanded = expandedId === report.id;
            return (
              <Card key={report.id} className="bg-card border-border shadow-none rounded-sm overflow-hidden">
                <div 
                  className="p-4 cursor-pointer hover:bg-secondary/50 transition-colors flex items-center justify-between"
                  onClick={() => setExpandedId(isExpanded ? null : report.id)}
                >
                  <div className="flex items-center gap-4">
                    {isExpanded ? <ChevronDown className="w-5 h-5 text-muted-foreground" /> : <ChevronRight className="w-5 h-5 text-muted-foreground" />}
                    <div>
                      <div className="font-mono font-bold text-foreground">{report.date}</div>
                      <div className="text-xs text-muted-foreground font-mono mt-1">Generated: {formatDateTime(report.createdAt)}</div>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {report.tickers.map(t => (
                      <Badge key={t} variant="secondary" className="font-mono bg-secondary border-border">{t}</Badge>
                    ))}
                  </div>
                </div>
                {isExpanded && (
                  <div className="border-t border-border p-6 bg-background">
                    <MarkdownContent content={report.content} />
                  </div>
                )}
              </Card>
            );
          })
        )}
      </div>
    </div>
  );
}
