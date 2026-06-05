import { Link, useLocation } from "wouter";
import { Activity, LayoutDashboard, History, Database, Play, Settings, ListChecks, Bell } from "lucide-react";
import { 
  useGetAgentStatus, 
  getGetAgentStatusQueryKey,
  useRunAgent,
  getGetLatestReportQueryKey,
  getListObservationsQueryKey,
  getGetObservationsSummaryQueryKey
} from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { useQueryClient } from "@tanstack/react-query";

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const queryClient = useQueryClient();

  const { data: status } = useGetAgentStatus({
    query: {
      queryKey: getGetAgentStatusQueryKey(),
      refetchInterval: 30000,
    }
  });

  const isRunning = status?.running;

  const runAgent = useRunAgent();

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
          <Link href="/" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <LayoutDashboard className="h-4 w-4" />
            Dashboard
          </Link>
          <Link href="/history" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/history" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <History className="h-4 w-4" />
            History
          </Link>
          <Link href="/observations" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/observations" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <Database className="h-4 w-4" />
            Observations
          </Link>
          <Link href="/runs" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/runs" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <ListChecks className="h-4 w-4" />
            Runs
          </Link>
          <Link href="/alerts" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/alerts" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <Bell className="h-4 w-4" />
            Alerts
          </Link>
          <Link href="/settings" className={`flex items-center gap-3 px-3 py-2 rounded-md font-mono text-sm transition-colors ${location === "/settings" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
            <Settings className="h-4 w-4" />
            Settings
          </Link>
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
          <Button 
            onClick={handleRun} 
            disabled={isRunning || runAgent.isPending}
            className="w-full font-mono font-bold"
            variant="default"
          >
            <Play className="h-4 w-4 mr-2" />
            {isRunning ? "AGENT ACTIVE" : "RUN AGENT"}
          </Button>
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
