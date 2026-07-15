import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";

// ─── MarketAlertsCard ────────────────────────────────────────────────────────
// Consome GET /api/market-alerts (get_market_alerts_snapshot.py): mesmo
// check_market_alerts que o agente chama durante uma run completa, exposto
// direto via HTTP -- não passa pelo loop do LLM, então atualiza rápido e sem
// custo de token. Inclui os sinais de risco macro (petróleo, Taiwan,
// Irã/Ormuz, Coreia do Norte, independência do Fed, rating soberano).

interface MarketAlert {
  ticker: string;
  category: string;
  severity: "info" | "atencao" | "critico";
  title: string;
  detail: string;
  value?: number | null;
  timestamp: string;
}

interface MarketAlertsResponse {
  total: number;
  criticalCount: number;
  alerts: MarketAlert[];
}

async function fetchMarketAlerts(): Promise<MarketAlertsResponse> {
  const res = await fetch("/api/market-alerts", { credentials: "include" });
  if (!res.ok) throw new Error(`market-alerts ${res.status}`);
  return (await res.json()) as MarketAlertsResponse;
}

function useMarketAlerts() {
  return useQuery({
    queryKey: ["market-alerts"],
    queryFn: fetchMarketAlerts,
    staleTime: 4 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });
}

const SEVERITY_STYLE: Record<MarketAlert["severity"], string> = {
  critico: "border-red-500/40 bg-red-500/10 text-red-400",
  atencao: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  info: "border-border bg-secondary/30 text-muted-foreground",
};

const SEVERITY_LABEL: Record<MarketAlert["severity"], string> = {
  critico: "CRÍTICO",
  atencao: "ATENÇÃO",
  info: "INFO",
};

export function MarketAlertsCard() {
  const { data, isLoading, isError } = useMarketAlerts();

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border bg-secondary/30">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-primary" />
          <span className="font-mono font-bold text-sm tracking-wider uppercase text-muted-foreground">
            Alertas de Mercado
          </span>
        </div>
        {data && data.criticalCount > 0 && (
          <span className="flex items-center gap-1 text-[11px] font-mono font-bold text-red-400" data-testid="market-alerts-critical-count">
            <AlertTriangle className="h-3.5 w-3.5" />
            {data.criticalCount} crítico{data.criticalCount > 1 ? "s" : ""}
          </span>
        )}
      </div>

      <div className="p-4">
        {isLoading ? (
          <p className="text-xs font-mono text-muted-foreground animate-pulse">Carregando alertas...</p>
        ) : isError ? (
          <p className="text-xs font-mono text-muted-foreground italic">Falha ao carregar alertas de mercado.</p>
        ) : !data || data.alerts.length === 0 ? (
          <p className="text-xs font-mono text-muted-foreground">Nenhum alerta ativo no momento.</p>
        ) : (
          <div className="space-y-2">
            {data.alerts.map((a, i) => (
              <div
                key={i}
                className={cn("border rounded-md px-3 py-2", SEVERITY_STYLE[a.severity])}
                data-testid={`market-alert-${i}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] font-bold tracking-widest">
                    [{SEVERITY_LABEL[a.severity]}] {a.ticker}
                  </span>
                </div>
                <p className="text-xs font-mono font-semibold text-foreground mt-0.5">{a.title}</p>
                <p className="text-[11px] font-mono text-muted-foreground mt-0.5 leading-snug">{a.detail}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
