import { useState } from "react";
import { Bitcoin } from "lucide-react";
import { TradingViewChart } from "@/components/tradingview-chart";
import { cn } from "@/lib/utils";

// Top 10 por volume de negociação (stablecoins fora -- gráfico de preço fixo
// em ~$1 não agrega nada). Símbolo no formato que a TradingView resolve pra
// listagem mais líquida sem precisar de prefixo de bolsa (ver tradingview-chart.tsx).
const CRYPTOS = [
  { symbol: "BTCUSD", ticker: "BTC", name: "Bitcoin" },
  { symbol: "ETHUSD", ticker: "ETH", name: "Ethereum" },
  { symbol: "BNBUSD", ticker: "BNB", name: "BNB" },
  { symbol: "SOLUSD", ticker: "SOL", name: "Solana" },
  { symbol: "XRPUSD", ticker: "XRP", name: "XRP" },
  { symbol: "DOGEUSD", ticker: "DOGE", name: "Dogecoin" },
  { symbol: "ADAUSD", ticker: "ADA", name: "Cardano" },
  { symbol: "TRXUSD", ticker: "TRX", name: "TRON" },
  { symbol: "AVAXUSD", ticker: "AVAX", name: "Avalanche" },
  { symbol: "LINKUSD", ticker: "LINK", name: "Chainlink" },
] as const;

const INTERVALS = [
  { key: "1", label: "1m" },
  { key: "5", label: "5m" },
  { key: "15", label: "15m" },
  { key: "60", label: "1H" },
  { key: "D", label: "1D" },
  { key: "W", label: "1S" },
];

export default function CryptoPage() {
  const [selected, setSelected] = useState<(typeof CRYPTOS)[number]>(CRYPTOS[0]);
  const [interval, setInterval] = useState("D");

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Bitcoin className="h-7 w-7 text-primary" /> CRIPTOMOEDAS
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          As 10 mais negociadas — gráfico da TradingView em tempo real
        </p>
      </div>

      {/* Grade de seleção */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
        {CRYPTOS.map((c) => {
          const sel = c.symbol === selected.symbol;
          return (
            <button
              key={c.symbol}
              type="button"
              onClick={() => setSelected(c)}
              className={cn(
                "text-left border rounded-lg px-3 py-2.5 font-mono transition-colors",
                sel
                  ? "border-primary bg-primary/10 ring-1 ring-primary/40"
                  : "border-border bg-card hover:border-primary/40",
              )}
              data-testid={`crypto-tile-${c.ticker}`}
            >
              <div className={cn("font-bold text-sm tracking-widest", sel ? "text-primary" : "text-foreground")}>
                {c.ticker}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">{c.name}</div>
            </button>
          );
        })}
      </div>

      {/* Gráfico em tempo real da moeda selecionada */}
      <div className="border border-border rounded-lg overflow-hidden bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-border bg-secondary/30">
          <div className="flex items-baseline gap-2">
            <span className="font-mono font-bold text-primary text-xl tracking-widest">{selected.ticker}</span>
            <span className="font-mono text-sm text-muted-foreground">{selected.name}</span>
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
                data-testid={`crypto-interval-${iv.key}`}
              >
                {iv.label}
              </button>
            ))}
          </div>
        </div>
        <div className="p-2">
          <TradingViewChart symbol={selected.symbol} height={780} interval={interval} hideSideToolbar={false} />
        </div>
      </div>
    </div>
  );
}
