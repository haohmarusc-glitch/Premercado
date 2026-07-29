import { useState, type KeyboardEvent } from "react";
import { useLocation } from "wouter";
import {
  useGetTickerQuotes, getGetTickerQuotesQueryKey,
  useGetSettings, getGetSettingsQueryKey,
  useUpdateSettings,
} from "@workspace/api-client-react";
import type { TickerQuote } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Orbit, RefreshCw, Plus, X } from "lucide-react";

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

// Hash simples determinístico (mesmo ticker sempre flutua "do mesmo jeito"
// entre atualizações, em vez de trocar de personalidade a cada refetch) pra
// variar duração/atraso/amplitude da flutuação sem depender de Math.random().
function hashSeed(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function floatStyle(symbol: string): { duration: number; delay: number; x: number; y: number } {
  const seed = hashSeed(symbol);
  return {
    duration: 4 + (seed % 5), // 4-8s
    delay: (seed % 30) / 10, // 0-2.9s
    x: ((seed >> 3) % 21) - 10, // -10..10px
    y: -8 - ((seed >> 6) % 10), // -8..-17px
  };
}

function Bubble({ quote, onClick }: { quote: TickerQuote; onClick: () => void }) {
  const pct = quote.changePct ?? 0;
  const style = bubbleStyle(pct);
  const fontSize = Math.max(11, Math.min(16, style.size / 8));
  const float = floatStyle(quote.symbol);

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
        animation: `float-bubble ${float.duration}s ease-in-out ${float.delay}s infinite`,
        // @ts-expect-error -- custom properties lidas pelo keyframe em index.css
        "--float-x": `${float.x}px`,
        "--float-y": `${float.y}px`,
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

function AddTickerBar({ tickers }: { tickers: string[] }) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const updateSettings = useUpdateSettings();

  function addTicker() {
    const symbol = input.trim().toUpperCase();
    if (!symbol || tickers.includes(symbol)) { setInput(""); return; }
    updateSettings.mutate(
      { data: { tickers: [...tickers, symbol] } },
      { onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetSettingsQueryKey() }) },
    );
    setInput("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTicker();
    }
  }

  function removeTicker(symbol: string) {
    updateSettings.mutate(
      { data: { tickers: tickers.filter((t) => t !== symbol) } },
      { onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetSettingsQueryKey() }) },
    );
  }

  return (
    <div className="border border-border rounded-lg bg-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          onKeyDown={onKeyDown}
          placeholder="Adicionar ticker (ex: AAPL) e pressionar Enter"
          className="flex-1 bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          data-testid="input-add-bubble-ticker"
        />
        <button
          type="button"
          onClick={addTicker}
          disabled={!input.trim() || updateSettings.isPending}
          className="px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-xs font-bold disabled:opacity-50 flex items-center gap-1.5"
          data-testid="button-add-bubble-ticker"
        >
          <Plus className="h-3.5 w-3.5" /> Adicionar
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tickers.map((t) => (
          <span
            key={t}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground font-mono text-[11px]"
          >
            {t}
            <button
              type="button"
              onClick={() => removeTicker(t)}
              className="hover:text-red-400 transition-colors"
              aria-label={`Remover ${t}`}
              data-testid={`remove-bubble-ticker-${t}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function QuotesBubblesPage() {
  const [, navigate] = useLocation();
  const [showManage, setShowManage] = useState(false);

  const { data: quotes, isLoading, dataUpdatedAt } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 15_000, staleTime: 10_000 },
  });
  const { data: settings } = useGetSettings({ query: { queryKey: getGetSettingsQueryKey() } });

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
        <div className="flex items-center gap-3 shrink-0">
          {updatedTime && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground mt-1">
              <RefreshCw className="h-3 w-3" />
              {updatedTime}
            </span>
          )}
          <button
            type="button"
            onClick={() => setShowManage((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-border font-mono text-xs text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors"
            data-testid="button-toggle-manage-tickers"
          >
            <Plus className="h-3.5 w-3.5" /> {showManage ? "Fechar" : "Gerenciar ativos"}
          </button>
        </div>
      </div>

      {showManage && settings && <AddTickerBar tickers={settings.tickers} />}

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando cotações...</div>
      ) : withData.length === 0 ? (
        <div className="border border-dashed border-border rounded-lg p-6 text-center">
          <p className="text-xs font-mono text-muted-foreground">
            Sem dados de cotação. Adicione tickers acima ou em Settings.
          </p>
        </div>
      ) : (
        <div className="border border-border rounded-lg bg-card p-6 overflow-hidden">
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
