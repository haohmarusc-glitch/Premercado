import { useQuery } from "@tanstack/react-query";
import { Layers, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface Item {
  ticker: string;
  expiry?: string;
  putCallRatio?: number | null;
  atmIvPct?: number | null;
  totalCallVolume?: number;
  totalPutVolume?: number;
  sentiment?: "bullish" | "bearish" | "neutro";
  error?: string;
}

function fmtInt(n: number | null | undefined) { return n == null ? "—" : n.toLocaleString("en-US"); }

export default function OptionsPage() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["options"],
    queryFn: async () => {
      const r = await fetch("/api/options", { credentials: "include" });
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
            <Layers className="h-7 w-7 text-primary" /> OPÇÕES
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Put/Call ratio · volatilidade implícita (ATM) · volume
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold disabled:opacity-50 shrink-0">
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          {isFetching ? "ATUALIZANDO..." : "ATUALIZAR"}
        </button>
      </div>

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando (opções podem demorar)...</div>
      ) : error ? (
        <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">{String(error)}</div>
      ) : (
        <div className="border border-border rounded-lg overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/30">
              <tr className="text-[10px] text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-3 py-2.5">Ticker</th>
                <th className="text-left px-3 py-2.5">Vencimento</th>
                <th className="text-right px-3 py-2.5">Put/Call</th>
                <th className="text-left px-3 py-2.5">Sentimento</th>
                <th className="text-right px-3 py-2.5">IV ATM</th>
                <th className="text-right px-3 py-2.5">Vol Calls</th>
                <th className="text-right px-3 py-2.5">Vol Puts</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={it.ticker} className={cn("border-t border-border/30", idx % 2 === 0 ? "bg-card" : "bg-secondary/10")}>
                  <td className="px-3 py-2.5 font-bold text-primary">{it.ticker}</td>
                  {it.error ? (
                    <td colSpan={6} className="px-3 py-2.5 text-muted-foreground italic text-xs">{it.error}</td>
                  ) : (
                    <>
                      <td className="px-3 py-2.5 text-muted-foreground">{it.expiry ?? "—"}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-bold">{it.putCallRatio ?? "—"}</td>
                      <td className="px-3 py-2.5">
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold uppercase",
                          it.sentiment === "bullish" ? "bg-green-500/10 text-green-400"
                          : it.sentiment === "bearish" ? "bg-red-500/10 text-red-400"
                          : "bg-muted text-muted-foreground")}>
                          {it.sentiment ?? "—"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{it.atmIvPct != null ? `${it.atmIvPct}%` : "—"}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-green-400">{fmtInt(it.totalCallVolume)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-red-400">{fmtInt(it.totalPutVolume)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs font-mono text-muted-foreground">
        <strong>Put/Call &gt; 1</strong> = mais puts que calls (proteção/baixa) · <strong>&lt; 0.7</strong> = otimismo (alta). <strong>IV alta</strong> = mercado esperando grandes movimentos (ex: perto de earnings).
      </p>
    </div>
  );
}
