import { Link, useLocation } from "wouter";
import { useRef, useEffect, useState } from "react";
import { Activity, LayoutDashboard, History, Database, Play, Settings, ListChecks, Bell, MessageSquare, Briefcase, Zap, Calculator, Sun, Moon, Eye, BookOpen, Calendar, TrendingUp, FlaskConical, LineChart, Flame, Users, Layers, Newspaper, Globe } from "lucide-react";
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
import { useToast } from "@/hooks/use-toast";

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
    // Contador baseado em variação — alertas por preço (thresholdPct nulo) ficam de fora
    if (a.thresholdPct == null) return false;
    const pct = quoteMap.get(a.symbol);
    if (pct == null) return false;
    return a.condition === "above" ? pct >= a.thresholdPct : pct <= a.thresholdPct;
  }).length;
}

export function Layout({ children }: { children: React.ReactNode }) {
  const [location, navigate] = useLocation();
  const queryClient = useQueryClient();
  const firingCount = useFiringCount();
  const [theme, setTheme] = useState<"dark" | "light">(() => (localStorage.getItem("theme") as "dark" | "light") ?? "dark");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  const { data: status } = useGetAgentStatus({
    query: {
      queryKey: getGetAgentStatusQueryKey(),
      refetchInterval: 30000,
    }
  });

  const isRunning = status?.running;
  const { toast } = useToast();
  const runAgent = useRunAgent();
  const runFastMode = (mode: "portfolio" | "premarket" | "manual" | "coal" | "ai", maxTurns?: number) =>
    fetch("/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, ...(maxTurns !== undefined ? { maxTurns } : {}) }),
    }).then((r) => r.json());

  const runAI = useMutation({
    mutationFn: () => { navigate("/setor/ia"); return runFastMode("ai"); },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
    },
  });

  const runCoal = useMutation({
    mutationFn: () => { navigate("/setor/carvao"); return runFastMode("coal"); },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
    },
  });

  const runPortfolio = useMutation({
    mutationFn: () => { navigate("/observations"); return runFastMode("portfolio"); },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
    },
  });

  const runPremarket = useMutation({
    mutationFn: () => { navigate("/"); return runFastMode("premarket"); },
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
      toast({ title: "✅ Turno finalizado", description: "Análise concluída. Dados atualizados." });
    }
    wasRunningRef.current = isRunning;
  }, [isRunning, queryClient, toast]);

  const handleRun = () => {
    navigate("/");
    runAgent.mutate(undefined, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetLatestReportQueryKey() });
        queryClient.invalidateQueries({ queryKey: getListObservationsQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetObservationsSummaryQueryKey() });
      }
    });
  };

  const navSection = (label: string) => (
    <p key={`section-${label}`} className="px-3 pt-4 pb-1 text-[10px] font-mono font-bold uppercase tracking-widest text-muted-foreground/60">
      {label}
    </p>
  );

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
    <div className="flex min-h-screen w-full bg-background text-foreground">
      <aside className="w-80 border-r border-border bg-card flex flex-col">
        <div className="p-6 border-b border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-primary font-bold text-xl font-mono tracking-tight">
              <Activity className="h-6 w-6" />
              <span>PRÉ-MERCADO</span>
            </div>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
              title={theme === "dark" ? "Modo claro" : "Modo escuro"}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
          <p className="text-xs text-muted-foreground mt-2 font-mono uppercase">Agent Command Center</p>
        </div>
        
        <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
          {navLink("/", <LayoutDashboard className="h-4 w-4" />, "Dashboard")}
          {navLink("/history", <History className="h-4 w-4" />, "History")}
          {navLink("/observations", <Database className="h-4 w-4" />, "Observations")}
          {navLink("/runs", <ListChecks className="h-4 w-4" />, "Runs")}
          {navLink("/alerts", <Bell className="h-4 w-4" />, "Alerts", firingCount)}
          {navLink("/chat", <MessageSquare className="h-4 w-4" />, "Chat")}

          {navSection("Carteira")}
          {navLink("/portfolio", <Briefcase className="h-4 w-4" />, "Carteira")}
          {navLink("/performance", <TrendingUp className="h-4 w-4" />, "Performance")}
          {navLink("/watchlist", <Eye className="h-4 w-4" />, "Watchlist")}
          {navLink("/journal", <BookOpen className="h-4 w-4" />, "Diário")}
          {navLink("/earnings", <Calendar className="h-4 w-4" />, "Earnings")}
          {navLink("/backtest", <FlaskConical className="h-4 w-4" />, "Backtest")}
          {navLink("/calculadora", <Calculator className="h-4 w-4" />, "Calculadora")}

          {navSection("Dados de Mercado")}
          {navLink("/macro", <Globe className="h-4 w-4" />, "Macro")}
          {navLink("/cotacoes", <LineChart className="h-4 w-4" />, "Cotações")}
          {navLink("/tecnicos", <Activity className="h-4 w-4" />, "Técnicos")}
          {navLink("/short", <Flame className="h-4 w-4" />, "Short")}
          {navLink("/analistas", <Users className="h-4 w-4" />, "Analistas")}
          {navLink("/opcoes", <Layers className="h-4 w-4" />, "Opções")}
          {navLink("/noticias", <Newspaper className="h-4 w-4" />, "Notícias")}

          {navSection("Sistema")}
          {navLink("/settings", <Settings className="h-4 w-4" />, "Settings")}
        </nav>

        <div className="p-4 border-t border-border overflow-y-auto">
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
            {[
              {
                icon: <Play className="h-4 w-4 shrink-0" />,
                label: "COMPLETO",
                desc: "Todos os tickers · notícias · técnicos · short · analistas · alertas",
                onClick: handleRun,
                variant: "default" as const,
              },
              {
                icon: <Zap className="h-4 w-4 shrink-0" />,
                label: "CARTEIRA",
                desc: "Só seus ativos · técnicos · short · analistas · salva sentimento",
                onClick: () => runPortfolio.mutate(),
                variant: "outline" as const,
              },
              {
                icon: <Zap className="h-4 w-4 shrink-0" />,
                label: "PRÉ-MERCADO",
                desc: "Flash intradiário · contágio de setor · cotações · opções",
                onClick: () => runPremarket.mutate(),
                variant: "outline" as const,
              },
              {
                icon: <Zap className="h-4 w-4 shrink-0" />,
                label: "CARVÃO",
                desc: "HCC · AMR · ARCH · CEIX · BTU — análise completa do setor",
                onClick: () => runCoal.mutate(),
                variant: "outline" as const,
              },
              {
                icon: <Zap className="h-4 w-4 shrink-0" />,
                label: "IA",
                desc: "NVDA · ARM · GOOGL · META · MSFT · AMD · PLTR · SMCI",
                onClick: () => runAI.mutate(),
                variant: "outline" as const,
              },
            ].map(({ icon, label, desc, onClick, variant }) => (
              <button
                key={label}
                onClick={onClick}
                disabled={isRunning || runAgent.isPending || runPortfolio.isPending || runPremarket.isPending || runCoal.isPending || runAI.isPending}
                className={`w-full text-left rounded-md border px-3 py-2.5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed
                  ${variant === "default"
                    ? "bg-primary text-primary-foreground border-primary hover:bg-primary/90"
                    : "bg-transparent text-foreground border-border hover:bg-secondary"
                  }`}
              >
                <div className="flex items-center gap-2 font-mono font-bold text-xs">
                  {icon}
                  {isRunning && label === "COMPLETO" ? "AGENT ACTIVE" : label}
                </div>
                <p className={`text-[10px] font-mono mt-1 leading-tight
                  ${variant === "default" ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                  {desc}
                </p>
              </button>
            ))}
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
