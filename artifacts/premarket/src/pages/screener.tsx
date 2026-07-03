import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { TrendingUp, TrendingDown, Minus, RefreshCw, Radar } from "lucide-react";
import { fetchTrendBasket, type TrendItem } from "@/components/trend-card";

// ─── Swing Screener ────────────────────────────────────────────────────────
// Roda o motor de confluência técnico + notícias (get_trend.py) pra cesta
// inteira de uma vez e ranqueia por score, pra dar uma varredura rápida de
// setups em vez de checar ticker por ticker no dashboard. Mesma filosofia do
// TrendCard: calculadora, não decisor.

const SINAL_FILTERS = ["todos", "compra", "venda", "aguardar"] as const;
type SinalFilter = (typeof SINAL_FILTERS)[number];

function trendVisual(trend?: string) {
  if (trend === "alta forte" || trend === "alta") return { color: "#22c55e", Icon: TrendingUp };
  if (trend === "baixa forte" || trend === "baixa") return { color: "#ef4444", Icon: TrendingDown };
  return { color: "#9ca3af", Icon: Minus };
}

function sinalStyle(sinal?: string) {
  if (sinal === "compra") return { color: "#22c55e", bg: "#22c55e22", border: "#22c55e" };
  if (sinal === "venda") return { color: "#ef4444", bg: "#ef444422", border: "#ef4444" };
  return { color: "#9ca3af", bg: "transparent", border: "#3f3f46" };
}

function useScreener() {
  return useQuery({
    queryKey: ["trend-basket"],
    queryFn: fetchTrendBasket,
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });
}

// Extraída pra ser testada sem montar o componente: descarta itens sem score
// ou com erro, filtra por sinal e ordena por score desc (setups mais fortes
// primeiro, tanto compra quanto venda).
export function rankScreenerItems(items: TrendItem[], sinalFilter: SinalFilter): (TrendItem & { score: number })[] {
  const scored = items.filter((it): it is TrendItem & { score: number } => it.score != null && !it.error);
  const filtered = sinalFilter === "todos" ? scored : scored.filter((it) => it.sinal === sinalFilter);
  return [...filtered].sort((a, b) => b.score - a.score);
}

export default function ScreenerPage() {
  const { data, isLoading, isError, dataUpdatedAt, refetch, isFetching } = useScreener();
  const [sinalFilter, setSinalFilter] = useState<SinalFilter>("todos");

  const ranked = useMemo(() => rankScreenerItems(data ?? [], sinalFilter), [data, sinalFilter]);

  const errored = (data ?? []).filter((it) => it.error);

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Radar className="h-7 w-7 text-primary" />
            SWING SCREENER
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Confluência técnico + notícias de toda a cesta, ranqueada por score — calculadora, não recomendação.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {updatedTime && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground">
              <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} />
              {updatedTime}
            </span>
          )}
          <button
            type="button"
            onClick={() => refetch()}
            className="px-2.5 py-1 rounded text-[11px] font-mono border border-border text-muted-foreground hover:text-foreground hover:border-border/80 transition-colors"
          >
            Atualizar
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {SINAL_FILTERS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSinalFilter(s)}
            className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold transition-colors border uppercase ${
              sinalFilter === s
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="p-12 text-center border border-dashed border-border rounded-lg">
          <span className="font-mono text-sm text-muted-foreground animate-pulse">Varrendo a cesta...</span>
        </div>
      ) : isError ? (
        <div className="p-12 text-center border border-dashed border-border rounded-lg">
          <span className="font-mono text-sm text-muted-foreground">Erro ao carregar o screener.</span>
        </div>
      ) : ranked.length === 0 ? (
        <div className="p-12 text-center border border-dashed border-border rounded-lg">
          <span className="font-mono text-sm text-muted-foreground">
            Nenhum ticker com esse sinal no momento.
          </span>
        </div>
      ) : (
        <div className="border border-border rounded-lg overflow-hidden bg-card">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-border bg-secondary/30 text-[10px] uppercase tracking-widest text-muted-foreground">
                <th className="text-left px-4 py-2.5">Ticker</th>
                <th className="text-right px-4 py-2.5">Preço</th>
                <th className="text-left px-4 py-2.5">Tendência</th>
                <th className="text-right px-4 py-2.5">Score</th>
                <th className="text-center px-4 py-2.5">Sinal</th>
                <th className="text-right px-4 py-2.5">RSI</th>
                <th className="text-left px-4 py-2.5">Confluência</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((it, i) => {
                const { color, Icon } = trendVisual(it.trend);
                const sinalC = sinalStyle(it.sinal);
                const diverge = (it.confluence ?? "").includes("DIVERGÊNCIA");
                return (
                  <tr
                    key={it.ticker}
                    className={`border-b border-border/40 last:border-0 hover:bg-secondary/20 transition-colors ${i % 2 === 1 ? "bg-secondary/5" : ""}`}
                    data-testid={`screener-row-${it.ticker}`}
                  >
                    <td className="px-4 py-2.5">
                      <Link href="/" className="font-bold text-primary hover:underline">
                        {it.ticker}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-right text-foreground">
                      {it.price != null ? `$${it.price.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="flex items-center gap-1.5" style={{ color }}>
                        <Icon className="h-3.5 w-3.5" />
                        {it.trend ?? "—"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right font-bold" style={{ color }}>
                      {it.score}
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <span
                        className="inline-block text-[11px] font-bold px-2 py-0.5 rounded uppercase tracking-wider"
                        style={{ background: sinalC.bg, color: sinalC.color, border: `1px solid ${sinalC.border}` }}
                      >
                        {it.sinal ?? "—"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right text-muted-foreground">
                      {it.components?.rsi ?? "—"}
                    </td>
                    <td className={`px-4 py-2.5 text-xs max-w-xs truncate ${diverge ? "text-yellow-500" : "text-muted-foreground"}`} title={it.confluence}>
                      {it.confluence ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {errored.length > 0 && (
        <p className="text-xs font-mono text-muted-foreground">
          Sem dados para: {errored.map((e) => e.ticker).join(", ")}.
        </p>
      )}
    </div>
  );
}
