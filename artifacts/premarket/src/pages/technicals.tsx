import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, RefreshCw, TrendingUp, TrendingDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface TechItem {
  ticker: string;
  price?: number;
  changePct?: number | null;
  rsi?: number;
  rsiSignal?: "sobrecomprado" | "sobrevendido" | "neutro";
  macdHistogram?: number;
  macdTrend?: "bullish" | "bearish";
  sma20?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  pctAboveSma50?: number | null;
  pctAboveSma200?: number | null;
  volumeRatio?: number | null;
  error?: string;
}

function fmt(n: number | null | undefined, d = 2) {
  return n == null ? "—" : n.toFixed(d);
}

function rsiColor(rsi?: number) {
  if (rsi == null) return "text-muted-foreground";
  if (rsi > 70) return "text-red-400";
  if (rsi < 30) return "text-green-400";
  return "text-foreground";
}

function pctColor(p?: number | null) {
  if (p == null) return "text-muted-foreground";
  return p >= 0 ? "text-green-400" : "text-red-400";
}

export default function TechnicalsPage() {
  const [tickersInput, setTickersInput] = useState("");

  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["technicals", tickersInput],
    queryFn: async () => {
      const qs = tickersInput.trim() ? `?tickers=${encodeURIComponent(tickersInput.trim())}` : "";
      const r = await fetch(`/api/technicals${qs}`, { credentials: "include" });
      const json = await r.json();
      if (!r.ok) throw new Error(json.error || "Falha ao buscar técnicos");
      return json as { items: TechItem[] };
    },
    refetchInterval: false,
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Activity className="h-7 w-7 text-primary" /> TÉCNICOS
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            RSI · MACD · Médias móveis · Volume — indicadores ao vivo por ativo
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold transition-colors disabled:opacity-50 shrink-0"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          {isFetching ? "ATUALIZANDO..." : "ATUALIZAR"}
        </button>
      </div>

      {/* Optional ticker filter */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          value={tickersInput}
          onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
          placeholder="Tickers (ex: NVDA,ARM,GOOGL) — vazio = carteira monitorada"
          className="flex-1 min-w-[260px] bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando indicadores...</div>
      ) : error ? (
        <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
          {String(error)}
        </div>
      ) : items.length === 0 ? (
        <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
          Nenhum dado técnico disponível.
        </div>
      ) : (
        <div className="border border-border rounded-lg overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/30">
              <tr className="text-[10px] text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-3 py-2.5">Ticker</th>
                <th className="text-right px-3 py-2.5">Preço</th>
                <th className="text-right px-3 py-2.5">Var %</th>
                <th className="text-right px-3 py-2.5">RSI 14</th>
                <th className="text-left px-3 py-2.5">Sinal RSI</th>
                <th className="text-right px-3 py-2.5">MACD</th>
                <th className="text-right px-3 py-2.5">vs MM50</th>
                <th className="text-right px-3 py-2.5">vs MM200</th>
                <th className="text-right px-3 py-2.5">Vol 5d/20d</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={it.ticker} className={cn("border-t border-border/30", idx % 2 === 0 ? "bg-card" : "bg-secondary/10")}>
                  <td className="px-3 py-2.5 font-bold text-primary">{it.ticker}</td>
                  {it.error ? (
                    <td colSpan={8} className="px-3 py-2.5 text-muted-foreground italic text-xs">{it.error}</td>
                  ) : (
                    <>
                      <td className="px-3 py-2.5 text-right tabular-nums text-foreground">${fmt(it.price)}</td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums", pctColor(it.changePct))}>
                        {it.changePct != null ? `${it.changePct >= 0 ? "+" : ""}${fmt(it.changePct)}%` : "—"}
                      </td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums font-bold", rsiColor(it.rsi))}>{fmt(it.rsi, 1)}</td>
                      <td className="px-3 py-2.5">
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold",
                          it.rsiSignal === "sobrecomprado" ? "bg-red-500/10 text-red-400"
                          : it.rsiSignal === "sobrevendido" ? "bg-green-500/10 text-green-400"
                          : "bg-muted text-muted-foreground")}>
                          {it.rsiSignal ?? "—"}
                        </span>
                      </td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums", it.macdTrend === "bullish" ? "text-green-400" : "text-red-400")}>
                        <span className="inline-flex items-center gap-1 justify-end">
                          {it.macdTrend === "bullish" ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                          {fmt(it.macdHistogram, 3)}
                        </span>
                      </td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums", pctColor(it.pctAboveSma50))}>
                        {it.pctAboveSma50 != null ? `${it.pctAboveSma50 >= 0 ? "+" : ""}${fmt(it.pctAboveSma50)}%` : "—"}
                      </td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums", pctColor(it.pctAboveSma200))}>
                        {it.pctAboveSma200 != null ? `${it.pctAboveSma200 >= 0 ? "+" : ""}${fmt(it.pctAboveSma200)}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{fmt(it.volumeRatio)}x</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="border border-border/40 rounded-lg p-4 space-y-1.5 text-xs font-mono text-muted-foreground">
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground mb-2">Como ler</p>
        <p><span className="text-red-400">RSI &gt; 70</span> = sobrecomprado (possível correção) · <span className="text-green-400">RSI &lt; 30</span> = sobrevendido (possível repique)</p>
        <p><span className="text-green-400">MACD bullish</span> = histograma positivo (momentum de alta) · <span className="text-red-400">bearish</span> = negativo</p>
        <p><span className="text-green-400">vs MM50/MM200 positivo</span> = preço acima da média (tendência de alta)</p>
        <p>Vol 5d/20d &gt; 1 = volume recente acima da média (interesse crescente)</p>
      </div>
    </div>
  );
}
