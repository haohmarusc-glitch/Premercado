import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/dashboard";
import History from "@/pages/history";
import Observations from "@/pages/observations";
import SettingsPage from "@/pages/settings";
import RunsPage from "@/pages/runs";
import AlertsPage from "@/pages/alerts";
import ChatPage from "@/pages/chat";
import PortfolioPage from "@/pages/portfolio";
import SectorCoal from "@/pages/sector-coal";
import SectorAI from "@/pages/sector-ai";
import CalculatorPage from "@/pages/calculator";
import WatchlistPage from "@/pages/watchlist";
import JournalPage from "@/pages/journal";
import EarningsPage from "@/pages/earnings";
import PerformancePage from "@/pages/performance";
import BacktestPage from "@/pages/backtest";
import TechnicalsPage from "@/pages/technicals";
import LoginPage from "@/pages/login";
import { Layout } from "@/components/layout";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function Router() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/history" component={History} />
        <Route path="/observations" component={Observations} />
        <Route path="/settings" component={SettingsPage} />
        <Route path="/runs" component={RunsPage} />
        <Route path="/alerts" component={AlertsPage} />
        <Route path="/chat" component={ChatPage} />
        <Route path="/portfolio" component={PortfolioPage} />
        <Route path="/setor/carvao" component={SectorCoal} />
        <Route path="/setor/ia" component={SectorAI} />
        <Route path="/calculadora" component={CalculatorPage} />
        <Route path="/watchlist" component={WatchlistPage} />
        <Route path="/journal" component={JournalPage} />
        <Route path="/earnings" component={EarningsPage} />
        <Route path="/performance" component={PerformancePage} />
        <Route path="/backtest" component={BacktestPage} />
        <Route path="/tecnicos" component={TechnicalsPage} />
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

type AuthState = "loading" | "authenticated" | "unauthenticated";

function App() {
  const { t } = useTranslation();
  const [authState, setAuthState] = useState<AuthState>("loading");

  useEffect(() => {
    // Criar controller com timeout de 5 segundos
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);

    fetch("/api/auth/me", { credentials: "include", signal: controller.signal })
      .then((r) => r.json())
      .then((data: { authenticated: boolean }) => {
        setAuthState(data.authenticated ? "authenticated" : "unauthenticated");
      })
      .catch((err) => {
        if (err instanceof Error && err.name !== "AbortError") {
          console.error(t("common.errorCheckingAuth"), err);
        }
        setAuthState("unauthenticated");
      })
      .finally(() => {
        clearTimeout(timeoutId);
      });
  }, []);

  if (authState === "loading") {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
      </div>
    );
  }

  if (authState === "unauthenticated") {
    return (
      <QueryClientProvider client={queryClient}>
        <LoginPage onSuccess={() => setAuthState("authenticated")} />
        <Toaster />
      </QueryClientProvider>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
