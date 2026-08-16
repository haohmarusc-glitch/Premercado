import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, useAuth } from "@/lib/auth";
import LoginPage from "@/pages/login";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/dashboard";
import History from "@/pages/history";
import Observations from "@/pages/observations";
import SettingsPage from "@/pages/settings";
import RunsPage from "@/pages/runs";
import AiSpendPage from "@/pages/ai-spend";
import CenariosPage from "@/pages/cenarios";
import EntryExitStudyPage from "@/pages/entry-exit-study";
import RadarPage from "@/pages/radar";
import VereditoPage from "@/pages/veredito";
import AdminUsersPage from "@/pages/admin-users";
import AlertsPage from "@/pages/alerts";
import ScreenerPage from "@/pages/screener";
import ChatPage from "@/pages/chat";
import PortfolioPage from "@/pages/portfolio";
import SectorCoal from "@/pages/sector-coal";
import SectorAI from "@/pages/sector-ai";
import CalculatorPage from "@/pages/calculator";
import WatchlistPage from "@/pages/watchlist";
import JournalPage from "@/pages/journal";
import ExitPlanPage from "@/pages/exit-plan";
import EarningsPage from "@/pages/earnings";
import EarningsReactionPage from "@/pages/earnings-reaction";
import AnaliseRapidaPage from "@/pages/analise-rapida";
import PrevisaoVolatilidadePage from "@/pages/previsao-volatilidade";
import PerformancePage from "@/pages/performance";
import BacktestPage from "@/pages/backtest";
import TechnicalsPage from "@/pages/technicals";
import QuotesPage from "@/pages/quotes";
import GraficoPage from "@/pages/grafico";
import QuotesBubblesPage from "@/pages/quotes-bubbles";
import ShortPage from "@/pages/short";
import AnalystsPage from "@/pages/analysts";
import OptionsPage from "@/pages/options";
import NewsPage from "@/pages/news";
import MacroPage from "@/pages/macro";
import CryptoPage from "@/pages/cripto";
import { Layout } from "@/components/layout";
import { ViewModeProvider } from "@/lib/view-mode";

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
        <Route path="/dashboard" component={Dashboard} />
        <Route path="/history" component={History} />
        <Route path="/observations" component={Observations} />
        <Route path="/settings" component={SettingsPage} />
        <Route path="/runs" component={RunsPage} />
        <Route path="/ai-spend" component={AiSpendPage} />
        <Route path="/cenarios" component={CenariosPage} />
        <Route path="/estudo-entrada-saida" component={EntryExitStudyPage} />
        <Route path="/radar-ia" component={RadarPage} />
        <Route path="/veredito" component={VereditoPage} />
        <Route path="/users" component={AdminUsersPage} />
        <Route path="/alerts" component={AlertsPage} />
        <Route path="/screener" component={ScreenerPage} />
        <Route path="/chat" component={ChatPage} />
        <Route path="/portfolio" component={PortfolioPage} />
        <Route path="/setor/carvao" component={SectorCoal} />
        <Route path="/setor/ia" component={SectorAI} />
        <Route path="/calculadora" component={CalculatorPage} />
        <Route path="/watchlist" component={WatchlistPage} />
        <Route path="/journal" component={JournalPage} />
        <Route path="/plano-saida" component={ExitPlanPage} />
        <Route path="/earnings" component={EarningsPage} />
        <Route path="/earnings-reaction" component={EarningsReactionPage} />
        <Route path="/analise-rapida" component={AnaliseRapidaPage} />
        <Route path="/previsao-vol" component={PrevisaoVolatilidadePage} />
        <Route path="/performance" component={PerformancePage} />
        <Route path="/backtest" component={BacktestPage} />
        <Route path="/tecnicos" component={TechnicalsPage} />
        <Route path="/cotacoes" component={QuotesPage} />
        <Route path="/grafico" component={GraficoPage} />
        <Route path="/bolhas" component={QuotesBubblesPage} />
        <Route path="/short" component={ShortPage} />
        <Route path="/analistas" component={AnalystsPage} />
        <Route path="/opcoes" component={OptionsPage} />
        <Route path="/noticias" component={NewsPage} />
        <Route path="/macro" component={MacroPage} />
        <Route path="/cripto" component={CryptoPage} />
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

function AuthGate() {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) return <LoginPage />;

  return <Router />;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ViewModeProvider>
          <TooltipProvider>
            <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
              <AuthGate />
            </WouterRouter>
            <Toaster />
          </TooltipProvider>
        </ViewModeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
