import { useQuery } from "@tanstack/react-query";
import { Flame, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface ShortBlock {
  shortPctOfFloat?: number | null;
  daysToCover?: number | null;
  sharesShort?: number | null;
  changeVsPriorMonthPct?: number | null;
  squeezeRisk?: "alto" | "moderado" | "baixo";
}
interface Item { ticker: string; price?: number | null; short?: ShortBlock; error?: string; }

function fmt(n: number | null | undefined, d = 2) { return n == null ? "—" : n.toFixed(d); }
function fmtInt(n: number | null | undefined) { return n == null ? "—" : n.toLocaleString("en-US"); }

export default function ShortPage() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["fundamentals-short"],
    queryFn: async () => {
      const r = await fetch("/api/fundamentals", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { items: Item[] };
    },
  });
  const items = data?.items ?? [];

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Flame className="h-7 w-7 text-primary" /> SHORT INTEREST
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            % do float vendido · days-to-cover · risco de squeeze
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold disabled:opacity-50 shrink-0">
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          {isFetching ? "ATUALIZANDO..." : "ATUALIZAR"}
        </button>
      </div>

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando...</div>
      ) : error ? (
        <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">{String(error)}</div>
      ) : (
        <div className="border border-border rounded-lg overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/30">
              <tr className="text-[10px] text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-3 py-2.5">Ticker</th>
                <th className="text-right px-3 py-2.5">Preço</th>
                <th className="text-right px-3 py-2.5">Short % Float</th>
                <th className="text-right px-3 py-2.5">Days to Cover</th>
                <th className="text-right px-3 py-2.5">Shares Short</th>
                <th className="text-right px-3 py-2.5">Δ vs mês ant.</th>
                <th className="text-left px-3 py-2.5">Risco Squeeze</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={it.ticker} className={cn("border-t border-border/30", idx % 2 === 0 ? "bg-card" : "bg-secondary/10")}>
                  <td className="px-3 py-2.5 font-bold text-primary">{it.ticker}</td>
                  {it.error || !it.short ? (
                    <td colSpan={6} className="px-3 py-2.5 text-muted-foreground italic text-xs">{it.error ?? "sem dados"}</td>
                  ) : it.short.shortPctOfFloat == null && it.short.daysToCover == null && it.short.sharesShort == null ? (
                    <td colSpan={6} className="px-3 py-2.5 text-muted-foreground italic text-xs">
                      ${fmt(it.price)} · short interest não disponível (mercado não coberto pela fonte de dados)
                    </td>
                  ) : (
                    <>
                      <td className="px-3 py-2.5 text-right tabular-nums">${fmt(it.price)}</td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums font-bold",
                        (it.short.shortPctOfFloat ?? 0) > 20 ? "text-red-400"
                        : (it.short.shortPctOfFloat ?? 0) > 10 ? "text-yellow-400" : "text-foreground")}>
                        {fmt(it.short.shortPctOfFloat)}%
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{fmt(it.short.daysToCover)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{fmtInt(it.short.sharesShort)}</td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums",
                        it.short.changeVsPriorMonthPct == null ? "text-muted-foreground"
                        : it.short.changeVsPriorMonthPct >= 0 ? "text-red-400" : "text-green-400")}>
                        {it.short.changeVsPriorMonthPct != null ? `${it.short.changeVsPriorMonthPct >= 0 ? "+" : ""}${fmt(it.short.changeVsPriorMonthPct)}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold uppercase",
                          it.short.squeezeRisk === "alto" ? "bg-red-500/10 text-red-400"
                          : it.short.squeezeRisk === "moderado" ? "bg-yellow-500/10 text-yellow-400"
                          : "bg-green-500/10 text-green-400")}>
                          {it.short.squeezeRisk ?? "—"}
                        </span>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs font-mono text-muted-foreground">
        Short % alto + days-to-cover alto = maior risco de <strong>short squeeze</strong> (alta forçada). Aumento vs. mês anterior indica pressão vendedora crescente.
      </p>
    </div>
  );
}