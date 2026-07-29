import { useLocation } from "wouter";
import { useGetTickerQuotes, getGetTickerQuotesQueryKey } from "@workspace/api-client-react";
import type { TickerQuote } from "@workspace/api-client-react";
import { Orbit, RefreshCw } from "lucide-react";

// Diâmetro escala com |variação %| (não temos market cap na cotação do
// Yahoo) -- maior variação, bolha maior. Capado em ±8% pra não deixar um
// outlier gigante o suficiente pra empurrar as outras bolhas pra fora de vista.
const MIN_SIZE = 64;
const MAX_SIZE = 168;
const CAP_PCT = 8;

function bubbleStyle(pct: number): { size: number; bg: string; border: string; shadow: string; text: string } {
  const abs = Math.min(Math.abs(pct), CAP_PCT);
  const intensity = abs / CAP_PCT; // 0..1
  const size = MIN_SIZE + intensity * (MAX_SIZE - MIN_SIZE);
  const up = pct >= 0;
  const rgb = up ? "34,197,94" : "248,113,113";
  return {
    size,
    bg: `rgba(${rgb}, ${0.06 + intensity * 0.22})`,
    border: `rgba(${rgb}, ${0.35 + intensity * 0.5})`,
    shadow: `0 0 ${8 + intensity * 24}px rgba(${rgb}, ${0.15 + intensity * 0.35})`,
    text: up ? "#4ade80" : "#f87171",
  };
}

function Bubble({ quote, onClick }: { quote: TickerQuote; onClick: () => void }) {
  const pct = quote.changePct ?? 0;
  const style = bubbleStyle(pct);
  const fontSize = Math.max(11, Math.min(16, style.size / 8));

  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full flex flex-col items-center justify-center font-mono transition-transform hover:scale-105 shrink-0"
      style={{
        width: style.size,
        height: style.size,
        background: style.bg,
        border: `2px solid ${style.border}`,
        boxShadow: style.shadow,
      }}
      data-testid={`bubble-${quote.symbol}`}
      title={`${quote.symbol} ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}
    >
      <span className="font-bold text-foreground" style={{ fontSize }}>{quote.symbol}</span>
      <span className="font-bold" style={{ fontSize: fontSize * 0.8, color: style.text }}>
        {pct >= 0 ? "+" : ""}{pct.toFixed(1)}%
      </span>
    </button>
  );
}

export default function QuotesBubblesPage() {
  const [, navigate] = useLocation();

  const { data: quotes, isLoading, dataUpdatedAt } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 15_000, staleTime: 10_000 },
  });

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  const withData = (quotes ?? [])
    .filter((q) => !q.error && q.changePct != null)
    .sort((a, b) => Math.abs(b.changePct ?? 0) - Math.abs(a.changePct ?? 0));

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Orbit className="h-7 w-7 text-primary" /> COTAÇÕES EM TEMPO REAL
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Tamanho e cor da bolha = magnitude da variação do dia — clique numa bolha pra abrir o gráfico do ativo.
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
      ) : withData.length === 0 ? (
        <div className="border border-dashed border-border rounded-lg p-6 text-center">
          <p className="text-xs font-mono text-muted-foreground">
            Sem dados de cotação. Adicione tickers em Settings.
          </p>
        </div>
      ) : (
        <div className="border border-border rounded-lg bg-card p-6">
          <div className="flex flex-wrap gap-4 items-center justify-center">
            {withData.map((q) => (
              <Bubble key={q.symbol} quote={q} onClick={() => navigate(`/grafico?ticker=${q.symbol}`)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
