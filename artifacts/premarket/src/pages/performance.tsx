import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

interface PositionPerf {
  ticker: string;
  quantity: number;
  avgCost: number;
  investedAmount: number;
  currentPrice: number | null;
  currentValue: number | null;
  plAbs: number | null;
  plPct: number | null;
  firstPurchaseDate: string;
}

interface PerformanceData {
  positions: PositionPerf[];
  totalInvested: number;
  totalValue: number;
  totalPL: number;
  totalPLPct: number;
  spyDayPct: number | null;
  spyPrice: number | null;
}

async function fetchPerformance(): Promise<PerformanceData> {
  const r = await fetch("/api/performance", { credentials: "include" });
  if (!r.ok) throw new Error("Failed");
  return r.json();
}

function fmt(n: number | null | undefined, dec = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function PLCell({ value, pct }: { value: number | null; pct: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const pos = value >= 0;
  return (
    <div className={pos ? "text-green-400" : "text-red-400"}>
      <div className="font-bold">{pos ? "+" : ""}${fmt(value)}</div>
      <div className="text-[10px]">{pos ? "+" : ""}{fmt(pct)}%</div>
    </div>
  );
}

export default function PerformancePage() {
  const { data, isLoading } = useQuery({
    queryKey: ["performance"],
    queryFn: fetchPerformance,
    refetchInterval: 60_000,
    staleTime: 55_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!data) return null;

  const totalWeight = data.totalValue;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <TrendingUp className="h-7 w-7 text-primary" /> PERFORMANCE
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Comparativo de carteira vs SPY</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="border border-border rounded-lg bg-card p-4">
          <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Total Investido</div>
          <div className="text-xl font-bold font-mono">${fmt(data.totalInvested)}</div>
        </div>
        <div className="border border-border rounded-lg bg-card p-4">
          <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Valor Atual</div>
          <div className="text-xl font-bold font-mono">${fmt(data.totalValue)}</div>
        </div>
        <div className="border border-border rounded-lg bg-card p-4">
          <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">P&amp;L Total</div>
          <div className={`text-xl font-bold font-mono ${data.totalPL >= 0 ? "text-green-400" : "text-red-400"}`}>
            {data.totalPL >= 0 ? "+" : ""}${fmt(data.totalPL)}
          </div>
          <div className={`text-xs font-mono ${data.totalPLPct >= 0 ? "text-green-400" : "text-red-400"}`}>
            {data.totalPLPct >= 0 ? "+" : ""}{fmt(data.totalPLPct)}%
          </div>
        </div>
        <div className="border border-border rounded-lg bg-card p-4">
          <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">SPY Dia</div>
          {data.spyDayPct != null ? (
            <div className={`text-xl font-bold font-mono flex items-center gap-1 ${data.spyDayPct >= 0 ? "text-green-400" : "text-red-400"}`}>
              {data.spyDayPct >= 0 ? <TrendingUp className="h-5 w-5" /> : <TrendingDown className="h-5 w-5" />}
              {data.spyDayPct >= 0 ? "+" : ""}{fmt(data.spyDayPct)}%
            </div>
          ) : <div className="text-xl font-bold font-mono text-muted-foreground">—</div>}
          {data.spyPrice != null && <div className="text-xs font-mono text-muted-foreground">${fmt(data.spyPrice)}</div>}
        </div>
      </div>

      {/* Positions table */}
      <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
        <table className="w-full font-mono text-sm min-w-[700px]">
          <thead className="bg-secondary/40 border-b border-border">
            <tr>
              {["Ticker", "Qtd", "PM", "Preço Atual", "P&L", "Valor", "Peso"].map((h) => (
                <th key={h} className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase tracking-widest">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.positions.map((pos, idx) => {
              const weight = pos.currentValue != null && totalWeight > 0 ? (pos.currentValue / totalWeight) * 100 : null;
              return (
                <tr key={pos.ticker} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                  <td className="px-4 py-3">
                    <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold">{pos.ticker}</Badge>
                  </td>
                  <td className="px-4 py-3 text-foreground">{pos.quantity.toFixed(4)}</td>
                  <td className="px-4 py-3 text-muted-foreground">${fmt(pos.avgCost)}</td>
                  <td className="px-4 py-3 text-foreground font-bold">
                    {pos.currentPrice != null ? `$${fmt(pos.currentPrice)}` : "—"}
                  </td>
                  <td className="px-4 py-3"><PLCell value={pos.plAbs} pct={pos.plPct} /></td>
                  <td className="px-4 py-3 text-foreground">{pos.currentValue != null ? `$${fmt(pos.currentValue)}` : "—"}</td>
                  <td className="px-4 py-3">
                    {weight != null ? (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden max-w-[60px]">
                          <div className="h-full bg-primary rounded-full" style={{ width: `${weight}%` }} />
                        </div>
                        <span className="text-xs text-muted-foreground">{fmt(weight)}%</span>
                      </div>
                    ) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
