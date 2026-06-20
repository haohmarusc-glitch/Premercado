import { Link, useLocation } from "wouter";
import { useRef, useEffect } from "react";
import { Activity, LayoutDashboard, History, Database, Play, Settings, ListChecks, Bell, MessageSquare, Briefcase, Zap } from "lucide-react";
import {
  useGetAgentStatus,
  getGetAgentStatusQueryKey,
  useRunAgent,
  getGetLatestReportQueryKey,
  getListObservationsQueryKey,
  getGetObservationsSummaryQueryKey,
  useListAlerts,
  getListAlertsQueryKey,
  useGetTickerQuotes,
  getGetTickerQuotesQueryKey,
} from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { useQueryClient, useMutation } from "@tanstack/react-query";

function useFiringCount(): number {
  const { data: alerts } = useListAlerts({
    query: { queryKey: getListAlertsQueryKey(), refetchInterval: 60_000, staleTime: 55_000 },
  });
  const { data: quotes } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 60_000, staleTime: 55_000 },
  });

  if (!alerts || !quotes) return 0;

  const quoteMap = new Map(quotes.map((q) => [q.symbol, q.changePct ?? null]));

  return alerts.filter((a) => {
    if (!a.enabled) return false;
    const pct = quoteMap.get(a.symbol);
    if (pct == null) return false;
    return a.condition === "above" ? pct >= a.thresholdPct : pct <= a.thresholdPct;
  }).length;
}

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const queryClient = useQueryClient();
  const firingCount = useFiringCount();

  const { data: status } = useGetAgentStatus({
    query: {
      queryKey: getGetAgentStatusQueryKey(),
      refetchInterval: 30000,
    }
  });

  const isRunning = status?.running;
  const runAgent = useRunAgent();
  const runPortfolio = useMutation({
    mutationFn: () =>
      fetch("/api/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "portfolio" }),
      }).then((r) => r.json()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
    },
  });

  const wasRunningRef = useRef(isRunning);
  useEffect(() => {
    if (wasRunningRef.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
      queryClient.invalidateQueries({ queryKey: getGetLatestReportQueryKey() });
      queryClient.invalidateQueries({ queryKey: getListObservationsQueryKey() });
      queryClient.invalidateQueries({ queryKey: getGetObservationsSummaryQueryKey() });
    }
    wasRunningRef.current = isRunning;
  }, [isRunning, queryClient]);

  const handleRun = () => {
    runAgent.mutate(undefined, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetLatestReportQueryKey() });
        queryClient.invalidateQueries({ queryKey: getListObservationsQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetObservationsSummaryQueryKey() });
      }
    });
  };

  const navLink = (href: string, icon: React.ReactNode, label: string, badge?: number) => {
    const active = location === href;
    return (
      <Link
        href={href}
        className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${
          active
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-secondary"
        }`}
      >
        {icon}
        <span className="flex-1">{label}</span>
        {badge != null && badge > 0 && (
          <span
            className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold font-mono animate-pulse ${
              active
                ? "bg-primary-foreground/20 text-primary-foreground"
                : "bg-red-500 text-white"
            }`}
            data-testid="alerts-firing-badge"
          >
            {badge}
          </span>
        )}
      </Link>
    );
  };

  return (
    <div className="flex min-h-screen w-full bg-background dark text-foreground">
      <aside className="w-64 border-r border-border bg-card flex flex-col">
        <div className="p-6 border-b border-border">
          <div className="flex items-center gap-2 text-primary font-bold text-xl font-mono tracking-tight">
            <Activity className="h-6 w-6" />
            <span>PRÉ-MERCADO</span>
          </div>
          <p className="text-xs text-muted-foreground mt-2 font-mono uppercase">Agent Command Center</p>
        </div>
        
        <nav className="flex-1 p-4 space-y-2">
          {navLink("/", <LayoutDashboard className="h-4 w-4" />, "Dashboard")}
          {navLink("/history", <History className="h-4 w-4" />, "History")}
          {navLink("/observations", <Database className="h-4 w-4" />, "Observations")}
          {navLink("/runs", <ListChecks className="h-4 w-4" />, "Runs")}
          {navLink("/alerts", <Bell className="h-4 w-4" />, "Alerts", firingCount)}
          {navLink("/chat", <MessageSquare className="h-4 w-4" />, "Chat")}
          {navLink("/portfolio", <Briefcase className="h-4 w-4" />, "Carteira")}
          {navLink("/settings", <Settings className="h-4 w-4" />, "Settings")}
        </nav>

        <div className="p-4 border-t border-border">
          <div className="mb-4">
            <div className="flex items-center justify-between text-xs font-mono mb-2">
              <span className="text-muted-foreground">STATUS</span>
              {isRunning ? (
                <span className="text-primary flex items-center gap-1.5">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
                  </span>
                  RUNNING
                </span>
              ) : (
                <span className="text-muted-foreground">IDLE</span>
              )}
            </div>
            {isRunning && status?.currentStep && (
              <div className="text-xs text-foreground font-mono bg-secondary p-2 rounded truncate" title={status.currentStep}>
                &gt; {status.currentStep}
              </div>
            )}
            {!isRunning && status?.lastRunAt && (
              <div className="text-xs text-muted-foreground font-mono">
                Last run: {new Date(status.lastRunAt).toLocaleTimeString()}
              </div>
            )}
            {!isRunning && status?.nextRunAt && (
              <div className="text-xs font-mono mt-1 flex items-center gap-1.5">
                <span className="text-muted-foreground">Next:</span>
                <span className="text-primary" data-testid="text-next-run">
                  {new Date(status.nextRunAt).toLocaleString("pt-BR", {
                    weekday: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                    timeZone: "America/Sao_Paulo",
                  })}
                </span>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <Button
              onClick={handleRun}
              disabled={isRunning || runAgent.isPending || runPortfolio.isPending}
              className="w-full font-mono font-bold"
              variant="default"
            >
              <Play className="h-4 w-4 mr-2" />
              {isRunning ? "AGENT ACTIVE" : "COMPLETO"}
            </Button>
            <Button
              onClick={() => runPortfolio.mutate()}
              disabled={isRunning || runAgent.isPending || runPortfolio.isPending}
              className="w-full font-mono font-bold"
              variant="outline"
            >
              <Zap className="h-4 w-4 mr-2" />
              RÁPIDO (CARTEIRA)
            </Button>
          </div>
        </div>
      </aside>
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className="flex-1 overflow-y-auto p-8">
          <div className="max-w-6xl mx-auto">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
