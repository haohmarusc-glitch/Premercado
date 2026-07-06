import { useEffect, useState } from "react";
import { useGetTickerQuotes, getGetTickerQuotesQueryKey } from "@workspace/api-client-react";
import { TrendingUp, TrendingDown, Minus, RefreshCw, CandlestickChart } from "lucide-react";
import { TradingViewChart } from "@/components/tradingview-chart";
import { cn } from "@/lib/utils";

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

const INTERVALS = [
  { key: "1", label: "1m" },
  { key: "5", label: "5m" },
  { key: "15", label: "15m" },
  { key: "60", label: "1H" },
  { key: "D", label: "1D" },
  { key: "W", label: "1S" },
];

export default function GraficoPage() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [interval, setInterval] = useState("D");

  const { data: quotes, isLoading, dataUpdatedAt } = useGetTickerQuotes({
    query: {
      queryKey: getGetTickerQuotesQueryKey(),
      refetchInterval: 15_000,
      staleTime: 10_000,
    },
  });

  useEffect(() => {
    if (!selectedSymbol && quotes && quotes.length > 0) {
      setSelectedSymbol(quotes[0].symbol);
    }
  }, [quotes, selectedSymbol]);

  const activeSymbol = selectedSymbol ?? quotes?.[0]?.symbol ?? null;
  const active = quotes?.find((q) => q.symbol === activeSymbol);

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  const positive = active?.change != null && active.change >= 0;
  const negative = active?.change != null && active.change < 0;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <CandlestickChart className="h-7 w-7 text-primary" /> GRÁFICO
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            TradingView em tempo real — atualiza a cada 15s
          </p>
        </div>
        {updatedTime && (
          <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground shrink-0 mt-1">
            <RefreshCw className="h-3 w-3" />
            {updatedTime}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando cotações...</div>
      ) : !quotes || quotes.length === 0 ? (
        <div className="border border-dashed border-border rounded-lg p-6 text-center">
          <p className="text-xs font-mono text-muted-foreground">
            Sem dados de cotação. Adicione tickers em Settings.
          </p>
        </div>
      ) : (
        <>
          {/* ── Ticker strip (tempo real) ── */}
          <div className="flex gap-2 overflow-x-auto pb-1">
            {quotes.map((q) => {
              const up = q.change != null && q.change >= 0;
              const sel = q.symbol === activeSymbol;
              return (
                <button
                  key={q.symbol}
                  type="button"
                  onClick={() => setSelectedSymbol(q.symbol)}
                  className={cn(
                    "shrink-0 min-w-[130px] text-left border rounded-lg px-3 py-2 font-mono transition-colors",
                    sel
                      ? "border-primary bg-primary/10 ring-1 ring-primary/40"
                      : "border-border bg-card hover:border-primary/40",
                  )}
                  data-testid={`grafico-ticker-${q.symbol}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={cn("font-bold text-sm tracking-widest", sel ? "text-primary" : "text-foreground")}>
                      {q.symbol}
                    </span>
                    {q.changePct != null && (
                      <span className={cn("text-[11px] font-bold", up ? "text-green-400" : "text-red-400")}>
                        {up ? "+" : ""}{fmt(q.changePct)}%
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {q.price != null ? `$${fmt(q.price)}` : "—"}
                  </div>
                </button>
              );
            })}
          </div>

          {activeSymbol && (
            <div className="border border-border rounded-lg overflow-hidden bg-card">
              {/* Header: preço em destaque + intervalos */}
              <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-border bg-secondary/30">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono font-bold text-primary text-xl tracking-widest">{activeSymbol}</span>
                  {active?.price != null && (
                    <span className="font-mono text-2xl font-bold text-foreground">${fmt(active.price)}</span>
                  )}
                  {active?.changePct != null && (
                    <span
                      className={cn(
                        "flex items-center gap-1 font-mono text-sm font-bold",
                        positive ? "text-green-400" : negative ? "text-red-400" : "text-muted-foreground",
                      )}
                    >
                      {positive ? <TrendingUp className="h-4 w-4" /> : negative ? <TrendingDown className="h-4 w-4" /> : <Minus className="h-4 w-4" />}
                      {positive ? "+" : ""}{fmt(active.changePct)}%
                      <span className="text-xs font-normal opacity-70">({positive ? "+" : ""}{fmt(active.change)})</span>
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1">
                  {INTERVALS.map((iv) => (
                    <button
                      key={iv.key}
                      type="button"
                      onClick={() => setInterval(iv.key)}
                      className={cn(
                        "px-2.5 py-1 rounded text-[11px] font-mono font-bold transition-colors",
                        interval === iv.key
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary",
                      )}
                      data-testid={`grafico-interval-${iv.key}`}
                    >
                      {iv.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Chart body — tela grande, toolbar completa da TradingView */}
              <div className="p-2">
                <TradingViewChart symbol={activeSymbol} height={780} interval={interval} hideSideToolbar={false} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
