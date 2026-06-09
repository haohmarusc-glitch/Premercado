import {
  useListReports,
  getListReportsQueryKey,
} from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useState } from "react";
import { ChevronDown, ChevronRight, Calendar, Zap } from "lucide-react";

function ModeBadge({ mode }: { mode: string }) {
  if (mode === "premarket") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-primary/10 border border-primary/30 text-primary font-mono text-[10px] uppercase">
        <Zap className="h-2.5 w-2.5" />
        flash
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground font-mono text-[10px] uppercase">
      <Calendar className="h-2.5 w-2.5" />
      diário
    </span>
  );
}

type Tab = "all" | "daily" | "premarket";

export default function History() {
  const { data: reports, isLoading } = useListReports({
    query: { queryKey: getListReportsQueryKey() },
  });

  const [tab, setTab] = useState<Tab>("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const filtered = reports?.filter((r) => {
    if (tab === "all") return true;
    return (r.mode ?? "daily") === tab;
  });

  const dailyCount = reports?.filter((r) => (r.mode ?? "daily") === "daily").length ?? 0;
  const flashCount = reports?.filter((r) => r.mode === "premarket").length ?? 0;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">HISTÓRICO</h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Todos os relatórios — análises diárias e flash scans intradiários
        </p>
      </div>

      <div className="flex items-center justify-between">
        <Tabs value={tab} onValueChange={(v) => { setTab(v as Tab); setExpandedId(null); }}>
          <TabsList className="font-mono">
            <TabsTrigger value="all">
              Todos
              {reports && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded bg-secondary text-muted-foreground text-[10px]">
                  {reports.length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="daily">
              <Calendar className="h-3 w-3 mr-1.5" />
              Diário
              <span className="ml-1.5 px-1.5 py-0.5 rounded bg-secondary text-muted-foreground text-[10px]">
                {dailyCount}
              </span>
            </TabsTrigger>
            <TabsTrigger value="premarket">
              <Zap className="h-3 w-3 mr-1.5" />
              Flash
              <span className="ml-1.5 px-1.5 py-0.5 rounded bg-secondary text-muted-foreground text-[10px]">
                {flashCount}
              </span>
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className="space-y-3">
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))
        ) : !filtered?.length ? (
          <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
            {tab === "premarket"
              ? "Nenhum flash scan registrado. Ative o pré-mercado intradiário em Configurações."
              : "Nenhum relatório encontrado."}
          </div>
        ) : (
          filtered.map((report) => {
            const isExpanded = expandedId === report.id;
            const isFlash = (report.mode ?? "daily") === "premarket";
            return (
              <Card
                key={report.id}
                className={`bg-card shadow-none rounded-sm overflow-hidden transition-colors ${
                  isFlash
                    ? "border-primary/20 hover:border-primary/40"
                    : "border-border hover:border-border/80"
                }`}
              >
                <div
                  className="p-4 cursor-pointer hover:bg-secondary/40 transition-colors flex items-center justify-between gap-4"
                  onClick={() => setExpandedId(isExpanded ? null : report.id)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    {isExpanded
                      ? <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                      : <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />}
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono font-bold text-foreground">{report.date}</span>
                        <ModeBadge mode={report.mode ?? "daily"} />
                      </div>
                      <div className="text-xs text-muted-foreground font-mono mt-0.5">
                        {formatDateTime(report.createdAt)}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1.5 flex-shrink-0 flex-wrap justify-end">
                    {report.tickers.slice(0, 6).map((t) => (
                      <Badge
                        key={t}
                        variant="secondary"
                        className="font-mono bg-secondary border-border text-[10px] px-1.5"
                      >
                        {t}
                      </Badge>
                    ))}
                    {report.tickers.length > 6 && (
                      <Badge variant="secondary" className="font-mono bg-secondary border-border text-[10px] px-1.5">
                        +{report.tickers.length - 6}
                      </Badge>
                    )}
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
