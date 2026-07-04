import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/dashboard";
import History from "@/pages/history";
import Observations from "@/pages/observations";
import SettingsPage from "@/pages/settings";
import RunsPage from "@/pages/runs";
import AlertsPage from "@/pages/alerts";
import ScreenerPage from "@/pages/screener";
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
import QuotesPage from "@/pages/quotes";
import ShortPage from "@/pages/short";
import AnalystsPage from "@/pages/analysts";
import OptionsPage from "@/pages/options";
import NewsPage from "@/pages/news";
import MacroPage from "@/pages/macro";
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
        <Route path="/screener" component={ScreenerPage} />
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
        <Route path="/cotacoes" component={QuotesPage} />
        <Route path="/short" component={ShortPage} />
        <Route path="/analistas" component={AnalystsPage} />
        <Route path="/opcoes" component={OptionsPage} />
        <Route path="/noticias" component={NewsPage} />
        <Route path="/macro" component={MacroPage} />
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

function App() {
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
