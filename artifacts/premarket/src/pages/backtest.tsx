import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface Trade {
  date: string;
  price: number;
  pnl: number;
  win: boolean;
}

interface BacktestResult {
  ticker: string;
  strategy: string;
  start: string;
  end: string;
  initialCapital: number;
  finalValue: number;
  totalReturn: number;
  buyAndHoldReturn: number;
  totalTrades: number;
  winRate: number;
  trades: Trade[];
  error?: string;
}

const today = new Date().toISOString().split("T")[0];
const oneYearAgo = new Date(Date.now() - 365 * 86400000).toISOString().split("T")[0];

export default function BacktestPage() {
  const [ticker, setTicker] = useState("NVDA");
  const [start, setStart] = useState(oneYearAgo);
  const [end, setEnd] = useState(today);
  const [strategy, setStrategy] = useState("rsi");
  const [result, setResult] = useState<BacktestResult | null>(null);

  const run = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), start, end, strategy }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as BacktestResult;
    },
    onSuccess: (data) => setResult(data),
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <FlaskConical className="h-7 w-7 text-primary" /> BACKTESTING
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Simular estratégias em dados históricos</p>
      </div>

      {/* Form */}
      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <p className="text-xs font-mono text-muted-foreground uppercase tracking-widest">Parâmetros</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Ticker</label>
            <input
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Data Início</label>
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Data Fim</label>
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Estratégia</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="rsi">RSI (30/70)</option>
              <option value="ma_cross">MA Cross (20/50)</option>
            </select>
          </div>
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending || !ticker.trim()}
          className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
        >
          {run.isPending ? (
            <>
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
              Executando...
            </>
          ) : (
            <><FlaskConical className="h-4 w-4" /> Executar</>
          )}
        </button>
        {run.isError && (
          <p className="text-sm text-red-400 font-mono">{String(run.error)}</p>
        )}
      </div>

      {/* Results */}
      {result && (
        result.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {result.error}
          </div>
        ) : (
          <div className="space-y-4">
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: "Retorno Total", value: `${result.totalReturn >= 0 ? "+" : ""}${result.totalReturn.toFixed(2)}%`, color: result.totalReturn >= 0 ? "text-green-400" : "text-red-400" },
                { label: "Buy & Hold", value: `${result.buyAndHoldReturn >= 0 ? "+" : ""}${result.buyAndHoldReturn.toFixed(2)}%`, color: result.buyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400" },
                { label: "Win Rate", value: `${result.winRate}%`, color: result.winRate > 50 ? "text-green-400" : "text-yellow-400" },
                { label: "Total Trades", value: String(result.totalTrades), color: "text-foreground" },
              ].map(({ label, value, color }) => (
                <div key={label} className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                  <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                </div>
              ))}
            </div>

            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>{result.ticker} · {result.strategy.toUpperCase()}</span>
              <span>{result.start} → {result.end}</span>
              <span>Capital inicial: $10,000</span>
              <span className={`font-bold ${result.finalValue >= 10000 ? "text-green-400" : "text-red-400"}`}>Final: ${result.finalValue.toLocaleString()}</span>
            </div>

            {/* Trades table */}
            {result.trades.length > 0 && (
              <div className="border border-border rounded-lg overflow-hidden">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  Últimas {result.trades.length} Operações
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Data</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Preço Saída</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">P&L%</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Resultado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((trade, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 text-muted-foreground">{trade.date}</td>
                        <td className="px-4 py-2.5 text-foreground">${trade.price.toFixed(2)}</td>
                        <td className={`px-4 py-2.5 font-bold ${trade.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge variant="outline" className={trade.win ? "text-green-500 border-green-500/30 bg-green-500/10 text-[10px] font-mono" : "text-red-500 border-red-500/30 bg-red-500/10 text-[10px] font-mono"}>
                            {trade.win ? "WIN" : "LOSS"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}
