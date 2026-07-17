import { Link } from "wouter";
import { AlertTriangle, Flag } from "lucide-react";
import { cn } from "@/lib/utils";
import { useListExitPlan, getListExitPlanQueryKey } from "@workspace/api-client-react";
import type { ExitPlanItem } from "@workspace/api-client-react";
import { useTacticalContext, tacticalSignal } from "@/hooks/use-tactical-context";

// ─── ExitPlanSummaryCard ─────────────────────────────────────────────────────
// Resumo compacto do plano de saída no Dashboard -- linka pra /plano-saida
// (janela separada com os itens completos). Só mostra os pendentes vencidos
// ou vencendo em até 3 dias, mesmo corte de useExitPlanDueCount em layout.tsx.

function daysUntil(dateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(dateStr + "T00:00:00");
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function urgentItems(items: ExitPlanItem[]) {
  return items
    .filter((i) => i.status === "pending" && daysUntil(i.targetDate) <= 3)
    .sort((a, b) => daysUntil(a.targetDate) - daysUntil(b.targetDate));
}

export function ExitPlanSummaryCard() {
  const { data, isLoading } = useListExitPlan({
    query: { queryKey: getListExitPlanQueryKey(), staleTime: 4 * 60_000, refetchInterval: 5 * 60_000 },
  });

  if (isLoading || !data || data.length === 0) return null;

  const urgent = urgentItems(data);
  if (urgent.length === 0) return null;

  return <ExitPlanSummaryCardBody urgent={urgent} />;
}

function ExitPlanSummaryCardBody({ urgent }: { urgent: ExitPlanItem[] }) {
  const ctx = useTacticalContext(urgent.map((i) => i.ticker));

  return (
    <Link href="/plano-saida">
      <div className="border border-border rounded-lg overflow-hidden bg-card hover:border-primary/40 transition-colors cursor-pointer">
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border bg-secondary/30">
          <div className="flex items-center gap-2">
            <Flag className="h-4 w-4 text-primary" />
            <span className="font-mono font-bold text-sm tracking-wider uppercase text-muted-foreground">
              Plano de Saída
            </span>
          </div>
          <span className="flex items-center gap-1 text-[11px] font-mono font-bold text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            {urgent.length} no prazo curto
          </span>
        </div>
        <div className="p-4 space-y-2">
          {urgent.slice(0, 4).map((item) => {
            const d = daysUntil(item.targetDate);
            const overdue = d < 0;
            const signal = tacticalSignal(item.ticker, ctx);
            const tech = ctx.technicalsByTicker.get(item.ticker);
            return (
              <div
                key={item.id}
                className={cn(
                  "border rounded-md px-3 py-2 text-xs space-y-1",
                  overdue ? "border-red-500/40 bg-red-500/10 text-red-400" : "border-amber-500/40 bg-amber-500/10 text-amber-400",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-bold">
                    {item.ticker}
                    {tech?.changePct != null && (
                      <span className="ml-1 font-normal">
                        {tech.changePct >= 0 ? "▲" : "▼"} {Math.abs(tech.changePct).toFixed(1)}%
                      </span>
                    )}
                  </span>
                  <span className="text-muted-foreground truncate">{item.action}</span>
                  <span className="font-mono whitespace-nowrap">
                    {overdue ? `vencido ${Math.abs(d)}d` : d === 0 ? "hoje" : `${d}d`}
                  </span>
                </div>
                {signal && <p className="text-[10px] text-muted-foreground/90 font-mono">{signal.label}</p>}
              </div>
            );
          })}
        </div>
      </div>
    </Link>
  );
}
