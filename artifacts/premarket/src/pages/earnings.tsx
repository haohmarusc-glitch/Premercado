import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Calendar, Plus, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface EarningsItem {
  ticker: string;
  name: string;
  earningsDate: string | null;
  epsEstimate: number | null;
  sector: string | null;
}

const DEFAULT_TICKERS = ["NVDA", "MU", "INTC", "ARM", "GOOGL", "TSLA", "SMCI"];

function daysUntil(dateStr: string): number {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const target = new Date(dateStr);
  return Math.round((target.getTime() - now.getTime()) / 86400000);
}

function groupEarnings(items: EarningsItem[]): Record<string, EarningsItem[]> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dayOfWeek = today.getDay();
  const endOfWeek = new Date(today);
  endOfWeek.setDate(today.getDate() + (6 - dayOfWeek));
  const endOfNextWeek = new Date(endOfWeek);
  endOfNextWeek.setDate(endOfWeek.getDate() + 7);
  const endOfMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0);

  const groups: Record<string, EarningsItem[]> = {
    "ESTA SEMANA": [],
    "PRÓXIMA SEMANA": [],
    "ESTE MÊS": [],
    "MAIS TARDE": [],
    "SEM DATA": [],
  };

  for (const item of items) {
    if (!item.earningsDate) {
      groups["SEM DATA"].push(item);
      continue;
    }
    const d = new Date(item.earningsDate);
    if (d <= endOfWeek) groups["ESTA SEMANA"].push(item);
    else if (d <= endOfNextWeek) groups["PRÓXIMA SEMANA"].push(item);
    else if (d <= endOfMonth) groups["ESTE MÊS"].push(item);
    else groups["MAIS TARDE"].push(item);
  }
  return groups;
}

function DaysChip({ days }: { days: number }) {
  const cls = days < 7 ? "text-red-400 border-red-400/30 bg-red-400/10"
    : days < 30 ? "text-yellow-400 border-yellow-400/30 bg-yellow-400/10"
    : "text-green-400 border-green-400/30 bg-green-400/10";
  return (
    <span className={`inline-block font-mono text-[10px] border rounded px-1.5 py-0.5 ${cls}`}>
      {days >= 0 ? `em ${days}d` : `${Math.abs(days)}d atrás`}
    </span>
  );
}

export default function EarningsPage() {
  const [customTickers, setCustomTickers] = useState<string[]>([]);
  const [input, setInput] = useState("");

  const tickers = [...new Set([...DEFAULT_TICKERS, ...customTickers])];

  const { data, isLoading, refetch } = useQuery<EarningsItem[]>({
    queryKey: ["earnings", tickers.join(",")],
    queryFn: async () => {
      const r = await fetch(`/api/earnings?tickers=${tickers.join(",")}`, { credentials: "include" });
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    staleTime: 5 * 60 * 1000,
  });

  function addTicker() {
    const t = input.trim().toUpperCase();
    if (t && !tickers.includes(t)) {
      setCustomTickers((prev) => [...prev, t]);
      void refetch();
    }
    setInput("");
  }

  function removeTicker(t: string) {
    setCustomTickers((prev) => prev.filter((x) => x !== t));
  }

  const groups = data ? groupEarnings(data) : {};

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Calendar className="h-7 w-7 text-primary" /> CALENDÁRIO DE EARNINGS
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Próximas divulgações de resultados</p>
      </div>

      {/* Add ticker */}
      <div className="flex gap-2 flex-wrap items-center">
        <input
          type="text"
          placeholder="Adicionar ticker..."
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && addTicker()}
          className="w-36 bg-background border border-border rounded px-3 py-1.5 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button
          onClick={addTicker}
          disabled={!input.trim()}
          className="flex items-center gap-1 px-3 py-1.5 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> Add
        </button>
        {customTickers.map((t) => (
          <span key={t} className="flex items-center gap-1 px-2 py-1 bg-secondary border border-border rounded font-mono text-xs text-foreground">
            {t}
            <button onClick={() => removeTicker(t)}><X className="h-3 w-3 text-muted-foreground hover:text-red-400" /></button>
          </span>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
      ) : (
        <div className="space-y-6">
          {Object.entries(groups).map(([group, items]) => {
            if (!items.length) return null;
            return (
              <div key={group}>
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-xs font-mono text-muted-foreground uppercase tracking-widest">{group}</span>
                  <div className="flex-1 border-t border-border/40" />
                  <span className="text-[10px] font-mono text-muted-foreground">{items.length}</span>
                </div>
                <div className="border border-border rounded-lg overflow-hidden">
                  <table className="w-full font-mono text-sm">
                    <tbody>
                      {items.map((item, idx) => {
                        const days = item.earningsDate ? daysUntil(item.earningsDate) : null;
                        return (
                          <tr key={item.ticker} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                            <td className="px-4 py-3 w-24">
                              <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold">{item.ticker}</Badge>
                            </td>
                            <td className="px-4 py-3 text-foreground text-sm">{item.name}</td>
                            <td className="px-4 py-3 text-muted-foreground text-xs">{item.earningsDate ?? "—"}</td>
                            <td className="px-4 py-3">
                              {days != null ? <DaysChip days={days} /> : null}
                            </td>
                            <td className="px-4 py-3 text-muted-foreground text-xs">{item.sector ?? "—"}</td>
                            <td className="px-4 py-3 text-right text-xs text-muted-foreground">
                              {item.epsEstimate != null ? `EPS est. $${item.epsEstimate.toFixed(2)}` : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
