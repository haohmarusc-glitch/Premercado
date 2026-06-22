import { useGetTickerQuotes, getGetTickerQuotesQueryKey } from "@workspace/api-client-react";
import { LineChart, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

function fmt(n: number | null | undefined, d = 2) { return n == null ? "—" : n.toFixed(d); }
function fmtInt(n: number | null | undefined) { return n == null ? "—" : n.toLocaleString("en-US"); }

// Rótulo amigável do estado de mercado vindo do Yahoo
const MARKET_STATE_LABEL: Record<string, string> = {
  PRE: "Pré-mercado",
  PREPRE: "Pré-mercado",
  REGULAR: "Pregão",
  POST: "After-hours",
  POSTPOST: "After-hours",
  CLOSED: "Fechado",
};

// Seleciona o preço/variação extended relevante conforme o estado de mercado
function extendedQuote(q: {
  marketState?: string | null;
  preMarketPrice?: number | null;
  preMarketChangePct?: number | null;
  postMarketPrice?: number | null;
  postMarketChangePct?: number | null;
}): { label: string; price: number; pct: number | null } | null {
  const st = q.marketState ?? "";
  if (st.startsWith("PRE") && q.preMarketPrice != null) {
    return { label: "Pré", price: q.preMarketPrice, pct: q.preMarketChangePct ?? null };
  }
  if (st.startsWith("POST") && q.postMarketPrice != null) {
    return { label: "Pós", price: q.postMarketPrice, pct: q.postMarketChangePct ?? null };
  }
  // Fora dessas janelas, mostra o último extended disponível (após o fechamento)
  if (q.postMarketPrice != null) return { label: "Pós", price: q.postMarketPrice, pct: q.postMarketChangePct ?? null };
  if (q.preMarketPrice != null) return { label: "Pré", price: q.preMarketPrice, pct: q.preMarketChangePct ?? null };
  return null;
}

export default function QuotesPage() {
  const { data: quotes = [], isLoading, isFetching, refetch } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 60_000 },
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <LineChart className="h-7 w-7 text-primary" /> COTAÇÕES
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Preço · variação · abertura · máx/mín · volume — atualiza a cada 60s
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
      ) : (
        <div className="border border-border rounded-lg overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/30">
              <tr className="text-[10px] text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-3 py-2.5">Ticker</th>
                <th className="text-left px-3 py-2.5">Sessão</th>
                <th className="text-right px-3 py-2.5">Pré/Pós-mercado</th>
                <th className="text-right px-3 py-2.5">Preço</th>
                <th className="text-right px-3 py-2.5">Var $</th>
                <th className="text-right px-3 py-2.5">Var %</th>
                <th className="text-right px-3 py-2.5">Abertura</th>
                <th className="text-right px-3 py-2.5">Fech. Ant.</th>
                <th className="text-right px-3 py-2.5">Máx</th>
                <th className="text-right px-3 py-2.5">Mín</th>
                <th className="text-right px-3 py-2.5">Volume</th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q, idx) => (
                <tr key={q.symbol} className={cn("border-t border-border/30", idx % 2 === 0 ? "bg-card" : "bg-secondary/10")}>
                  <td className="px-3 py-2.5 font-bold text-primary">{q.symbol}</td>
                  {q.error ? (
                    <td colSpan={10} className="px-3 py-2.5 text-muted-foreground italic text-xs">{q.error}</td>
                  ) : (
                    <>
                      <td className="px-3 py-2.5 text-left text-xs text-muted-foreground">
                        {q.marketState ? (MARKET_STATE_LABEL[q.marketState] ?? q.marketState) : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {(() => {
                          const ext = extendedQuote(q);
                          if (!ext) return <span className="text-muted-foreground">—</span>;
                          const up = (ext.pct ?? 0) >= 0;
                          return (
                            <span className="inline-flex flex-col items-end leading-tight">
                              <span className="font-semibold">{ext.label} ${ext.price.toFixed(2)}</span>
                              {ext.pct != null && (
                                <span className={cn("text-[10px]", up ? "text-green-400" : "text-red-400")}>
                                  {up ? "▲ +" : "▼ "}{ext.pct.toFixed(2)}%
                                </span>
                              )}
                            </span>
                          );
                        })()}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-semibold">${fmt(q.price)}</td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums", (q.change ?? 0) >= 0 ? "text-green-400" : "text-red-400")}>
                        {q.change != null ? `${q.change >= 0 ? "+" : ""}${fmt(q.change)}` : "—"}
                      </td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums font-bold", (q.changePct ?? 0) >= 0 ? "text-green-400" : "text-red-400")}>
                        {q.changePct != null ? `${q.changePct >= 0 ? "+" : ""}${fmt(q.changePct)}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">${fmt(q.open)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">${fmt(q.previousClose)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">${fmt(q.dayHigh)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">${fmt(q.dayLow)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{fmtInt(q.volume)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
