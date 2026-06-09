import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/dashboard";
import History from "@/pages/history";
import Observations from "@/pages/observations";
import SettingsPage from "@/pages/settings";
import RunsPage from "@/pages/runs";
import AlertsPage from "@/pages/alerts";
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
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

type AuthState = "loading" | "authenticated" | "unauthenticated";

// Constantes para internacionalização (preparação para i18n futuro)
const i18n = {
  loading: "Carregando...",
  errorChecking: "Erro ao verificar autenticação",
};

function App() {
  const [authState, setAuthState] = useState<AuthState>("loading");

  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);

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
        // Logar erro para debug, exceto para AbortError (timeout)
        if (err instanceof Error && err.name !== "AbortError") {
          console.error(i18n.errorChecking, err);
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
        <div className="text-muted-foreground text-sm">{i18n.loading}</div>
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
