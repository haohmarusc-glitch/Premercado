import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FlaskConical, Layers } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Badge } from "@/components/ui/badge";

interface Trade {
  entryDate: string;
  exitDate: string;
  entryPrice: number;
  exitPrice: number;
  pnl: number;
  win: boolean;
  closedOpen: boolean;
  exitReason?: "signal" | "stop_loss" | "take_profit" | "period_end";
}

interface EquityPoint {
  date: string;
  equity: number;
  buyHoldEquity: number;
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
  cagr: number;
  sharpe: number;
  maxDrawdown: number;
  totalTrades: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
  trades: Trade[];
  equityCurve: EquityPoint[];
  error?: string;
}

const EXIT_REASON_LABEL: Record<string, string> = {
  signal: "sinal",
  stop_loss: "stop loss",
  take_profit: "take profit",
  period_end: "fim período",
};

interface SensitivityRun {
  param?: string;
  value?: number;
  totalReturn: number;
  buyAndHoldReturn: number;
  cagr: number;
  sharpe: number;
  maxDrawdown: number;
  totalTrades: number;
  winRate: number;
  error?: string;
}

interface SensitivityResult {
  ticker: string;
  strategy: string;
  start: string;
  end: string;
  baseline: SensitivityRun;
  variations: SensitivityRun[];
  error?: string;
}

const SENSITIVITY_PARAM_LABEL: Record<string, string> = {
  rsiOversold: "RSI Sobrevendido",
  rsiOverbought: "RSI Sobrecomprado",
  scoreThreshold: "Threshold do Score",
  stopLossPct: "Stop Loss",
  takeProfitPct: "Take Profit",
};

function formatSensitivityValue(param: string, value: number): string {
  if (param === "stopLossPct" || param === "takeProfitPct") return `${(value * 100).toFixed(0)}%`;
  return String(value);
}

interface SectorAggregate {
  sector: string;
  label: string;
  tickerCount: number;
  avgTotalReturn: number;
  avgBuyAndHoldReturn: number;
  avgWinRate: number;
  totalTrades: number;
  beatBuyAndHoldCount: number;
}

interface BasketResult {
  strategy: string;
  start: string;
  end: string;
  tickersRequested: number;
  tickersOk: number;
  aggregate?: {
    avgTotalReturn: number;
    avgBuyAndHoldReturn: number;
    avgWinRate: number;
    totalTrades: number;
    beatBuyAndHoldCount: number;
  };
  bySector?: SectorAggregate[];
  results: BacktestResult[];
  failed: { ticker: string; error: string }[];
  error?: string;
}

const today = new Date().toISOString().split("T")[0];
const oneYearAgo = new Date(Date.now() - 365 * 86400000).toISOString().split("T")[0];
const sixMonthsAgo = new Date(Date.now() - 182 * 86400000).toISOString().split("T")[0];

export default function BacktestPage() {
  const [mode, setMode] = useState<"ticker" | "basket">("ticker");
  const [ticker, setTicker] = useState("NVDA");
  const [start, setStart] = useState(oneYearAgo);
  const [end, setEnd] = useState(today);
  const [strategy, setStrategy] = useState("rsi");
  const [positionFraction, setPositionFraction] = useState("1.0");
  const [commissionPct, setCommissionPct] = useState("0.001");
  const [slippagePct, setSlippagePct] = useState("0.0005");
  const [stopLossPct, setStopLossPct] = useState("");
  const [takeProfitPct, setTakeProfitPct] = useState("");
  const [rsiOversold, setRsiOversold] = useState("30");
  const [rsiOverbought, setRsiOverbought] = useState("70");
  const [scoreThreshold, setScoreThreshold] = useState("60");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [basketResult, setBasketResult] = useState<BasketResult | null>(null);
  const [sensitivityResult, setSensitivityResult] = useState<SensitivityResult | null>(null);

  function switchToBasket() {
    setMode("basket");
    setStrategy("confluencia");
    setStart(sixMonthsAgo);
  }
  function switchToTicker() {
    setMode("ticker");
    setStrategy("rsi");
    setStart(oneYearAgo);
  }

  function riskParams() {
    return {
      positionFraction: parseFloat(positionFraction),
      commissionPct: parseFloat(commissionPct),
      slippagePct: parseFloat(slippagePct),
      stopLossPct: stopLossPct ? parseFloat(stopLossPct) / 100 : undefined,
      takeProfitPct: takeProfitPct ? parseFloat(takeProfitPct) / 100 : undefined,
      rsiOversold: parseFloat(rsiOversold),
      rsiOverbought: parseFloat(rsiOverbought),
      scoreThreshold: parseFloat(scoreThreshold),
    };
  }

  const run = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as BacktestResult;
    },
    onSuccess: (data) => setResult(data),
  });

  const runBasket = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest/basket", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as BasketResult;
    },
    onSuccess: (data) => setBasketResult(data),
  });

  const runSensitivity = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest/sensitivity", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as SensitivityResult;
    },
    onSuccess: (data) => setSensitivityResult(data),
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
        <div className="flex items-center justify-between">
          <p className="text-xs font-mono text-muted-foreground uppercase tracking-widest">Parâmetros</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={switchToTicker}
              className={`px-3 py-1.5 rounded border font-mono text-xs transition-colors ${
                mode === "ticker" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              Ticker único
            </button>
            <button
              type="button"
              onClick={switchToBasket}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded border font-mono text-xs transition-colors ${
                mode === "basket" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              <Layers className="h-3 w-3" /> Cesta inteira
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {mode === "ticker" && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Ticker</label>
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
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
              <option value="confluencia">Confluência (técnico, sem notícias)</option>
            </select>
          </div>
        </div>
        {strategy === "confluencia" && (
          <p className="text-[11px] font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2">
            Reproduz o score técnico do sinal (SMA20×50, preço×SMA200, estrutura, MACD, RSI) sem a camada de notícias
            — não dá pra reconstruir com fidelidade o que era manchete em cada dia do passado. Compra/venda nos
            thresholds do score configurados abaixo (padrão ±60).
          </p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[
            { label: "Fração Posição (0.1–1.0)", val: positionFraction, set: setPositionFraction, step: "0.1" },
            { label: "Comissão (ex: 0.001 = 0.1%)", val: commissionPct, set: setCommissionPct, step: "0.0001" },
            { label: "Slippage (ex: 0.0005)", val: slippagePct, set: setSlippagePct, step: "0.0001" },
          ].map(({ label, val, set, step }) => (
            <div key={label} className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">{label}</label>
              <input
                type="number" step={step} value={val}
                onChange={(e) => set(e.target.value)}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 border-t border-border/40 pt-4">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Stop Loss % (opcional)</label>
            <input
              type="number" step="0.5" min="0" placeholder="ex: 8"
              value={stopLossPct}
              onChange={(e) => setStopLossPct(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Take Profit % (opcional)</label>
            <input
              type="number" step="0.5" min="0" placeholder="ex: 15"
              value={takeProfitPct}
              onChange={(e) => setTakeProfitPct(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          {strategy === "rsi" && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">RSI Sobrevendido</label>
                <input
                  type="number" step="1" min="1" max="49" value={rsiOversold}
                  onChange={(e) => setRsiOversold(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">RSI Sobrecomprado</label>
                <input
                  type="number" step="1" min="51" max="99" value={rsiOverbought}
                  onChange={(e) => setRsiOverbought(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </>
          )}
          {strategy === "confluencia" && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Threshold do Score</label>
              <input
                type="number" step="5" min="5" max="100" value={scoreThreshold}
                onChange={(e) => setScoreThreshold(e.target.value)}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => mode === "basket" ? runBasket.mutate() : run.mutate()}
            disabled={mode === "basket" ? runBasket.isPending : (run.isPending || !ticker.trim())}
            className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {(mode === "basket" ? runBasket.isPending : run.isPending) ? (
              <>
                <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
                Executando...
              </>
            ) : (
              <><FlaskConical className="h-4 w-4" /> Executar</>
            )}
          </button>
          {mode === "ticker" && (
            <button
              onClick={() => runSensitivity.mutate()}
              disabled={runSensitivity.isPending || !ticker.trim()}
              className="px-6 py-2 border border-border rounded font-mono text-sm font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
            >
              {runSensitivity.isPending ? (
                <>
                  <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-border border-t-foreground rounded-full" />
                  Testando...
                </>
              ) : (
                "Análise de Sensibilidade"
              )}
            </button>
          )}
        </div>
        {mode === "ticker" && run.isError && (
          <p className="text-sm text-red-400 font-mono">{String(run.error)}</p>
        )}
        {mode === "basket" && runBasket.isError && (
          <p className="text-sm text-red-400 font-mono">{String(runBasket.error)}</p>
        )}
        {mode === "ticker" && runSensitivity.isError && (
          <p className="text-sm text-red-400 font-mono">{String(runSensitivity.error)}</p>
        )}
      </div>

      {/* Basket results */}
      {mode === "basket" && basketResult && (
        basketResult.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {basketResult.error}
          </div>
        ) : (
          <div className="space-y-4">
            {basketResult.aggregate && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: "Retorno Médio", value: `${basketResult.aggregate.avgTotalReturn >= 0 ? "+" : ""}${basketResult.aggregate.avgTotalReturn.toFixed(2)}%`, color: basketResult.aggregate.avgTotalReturn >= 0 ? "text-green-400" : "text-red-400" },
                  { label: "Buy & Hold Médio", value: `${basketResult.aggregate.avgBuyAndHoldReturn >= 0 ? "+" : ""}${basketResult.aggregate.avgBuyAndHoldReturn.toFixed(2)}%`, color: basketResult.aggregate.avgBuyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400" },
                  { label: "Win Rate Médio", value: `${basketResult.aggregate.avgWinRate}%`, color: basketResult.aggregate.avgWinRate > 50 ? "text-green-400" : "text-yellow-400" },
                  { label: "Bateu Buy&Hold", value: `${basketResult.aggregate.beatBuyAndHoldCount}/${basketResult.tickersOk}`, color: "text-foreground" },
                ].map(({ label, value, color }) => (
                  <div key={label} className="border border-border rounded-lg bg-card p-4">
                    <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                    <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                  </div>
                ))}
              </div>
            )}

            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>{basketResult.strategy.toUpperCase()}</span>
              <span>{basketResult.start} → {basketResult.end}</span>
              <span>{basketResult.tickersOk}/{basketResult.tickersRequested} tickers com dados suficientes</span>
            </div>

            <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
              <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                Por ticker (ordenado por retorno)
              </div>
              <table className="w-full font-mono text-sm">
                <thead className="bg-secondary/20">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Ticker</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Buy&Hold</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  {basketResult.results.map((r, idx) => (
                    <tr key={r.ticker} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                      <td className="px-4 py-2.5 font-bold text-primary">{r.ticker}</td>
                      <td className={`px-4 py-2.5 text-right font-bold ${r.totalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {r.totalReturn >= 0 ? "+" : ""}{r.totalReturn.toFixed(2)}%
                      </td>
                      <td className={`px-4 py-2.5 text-right ${r.buyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {r.buyAndHoldReturn >= 0 ? "+" : ""}{r.buyAndHoldReturn.toFixed(2)}%
                      </td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground">{r.totalTrades}</td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground">{r.totalTrades > 0 ? `${r.winRate}%` : "—"}</td>
                      <td className="px-4 py-2.5 text-right text-red-400">{r.maxDrawdown.toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {basketResult.bySector && basketResult.bySector.length > 0 && (
              <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  Por setor (ordenado por retorno médio)
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Setor</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Tickers</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno Médio</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Buy&Hold Médio</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Bateu B&H</th>
                    </tr>
                  </thead>
                  <tbody>
                    {basketResult.bySector.map((s, idx) => (
                      <tr key={s.sector} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 font-bold text-primary">{s.label}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.tickerCount}</td>
                        <td className={`px-4 py-2.5 text-right font-bold ${s.avgTotalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {s.avgTotalReturn >= 0 ? "+" : ""}{s.avgTotalReturn.toFixed(2)}%
                        </td>
                        <td className={`px-4 py-2.5 text-right ${s.avgBuyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {s.avgBuyAndHoldReturn >= 0 ? "+" : ""}{s.avgBuyAndHoldReturn.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.totalTrades}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.totalTrades > 0 ? `${s.avgWinRate}%` : "—"}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.beatBuyAndHoldCount}/{s.tickerCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {basketResult.failed.length > 0 && (
              <p className="text-xs font-mono text-muted-foreground">
                Sem dados para: {basketResult.failed.map((f) => f.ticker).join(", ")}.
              </p>
            )}
          </div>
        )
      )}

      {/* Results */}
      {mode === "ticker" && result && (
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
                { label: "CAGR", value: `${result.cagr >= 0 ? "+" : ""}${result.cagr.toFixed(2)}%`, color: result.cagr >= 0 ? "text-green-400" : "text-red-400" },
                { label: "Sharpe Ratio", value: result.sharpe.toFixed(2), color: result.sharpe >= 1 ? "text-green-400" : result.sharpe >= 0 ? "text-yellow-400" : "text-red-400" },
              ].map(({ label, value, color }) => (
                <div key={label} className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                  <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: "Max Drawdown", value: `${result.maxDrawdown.toFixed(2)}%`, color: "text-red-400" },
                { label: "Win Rate", value: `${result.winRate}%`, color: result.winRate > 50 ? "text-green-400" : "text-yellow-400" },
                { label: "Média Ganho", value: `+${result.avgWin.toFixed(2)}%`, color: "text-green-400" },
                { label: "Média Perda", value: `${result.avgLoss.toFixed(2)}%`, color: "text-red-400" },
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

            {/* Equity curve: estratégia vs buy & hold */}
            {result.equityCurve && result.equityCurve.length > 0 && (
              <div className="border border-border rounded-lg bg-card p-4">
                <div className="text-xs font-mono text-muted-foreground uppercase tracking-widest mb-3">
                  Equity Curve — Estratégia vs Buy &amp; Hold
                </div>
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={result.equityCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f97316" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
                      tickLine={false}
                      axisLine={false}
                      interval="preserveStartEnd"
                      minTickGap={60}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
                      tickLine={false}
                      axisLine={false}
                      width={64}
                      tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontFamily: "monospace",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
                      formatter={(val: number, name: string) => [`$${val.toLocaleString()}`, name === "equity" ? "Estratégia" : "Buy & Hold"]}
                    />
                    <Legend
                      formatter={(value: string) => (value === "equity" ? "Estratégia" : "Buy & Hold")}
                      wrapperStyle={{ fontFamily: "monospace", fontSize: "11px" }}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#f97316" strokeWidth={1.5} fill="url(#equityGradient)" dot={false} isAnimationActive={false} />
                    <Area type="monotone" dataKey="buyHoldEquity" stroke="#6b7280" strokeWidth={1.5} fill="none" dot={false} strokeDasharray="4 3" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Trades table */}
            {result.trades.length > 0 && (
              <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  Últimas {result.trades.length} Operações
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Entrada</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Saída</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Preço Entr.</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Preço Saída</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">P&L%</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Saída via</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Resultado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((trade, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 text-muted-foreground">{trade.entryDate}</td>
                        <td className="px-4 py-2.5 text-muted-foreground">{trade.exitDate}</td>
                        <td className="px-4 py-2.5 text-foreground">${trade.entryPrice.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-foreground">${trade.exitPrice.toFixed(2)}</td>
                        <td className={`px-4 py-2.5 font-bold ${trade.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5 text-muted-foreground">
                          {trade.exitReason ? EXIT_REASON_LABEL[trade.exitReason] ?? trade.exitReason : "—"}
                        </td>
                        <td className="px-4 py-2.5 flex items-center gap-1">
                          <Badge variant="outline" className={trade.win ? "text-green-500 border-green-500/30 bg-green-500/10 text-[10px] font-mono" : "text-red-500 border-red-500/30 bg-red-500/10 text-[10px] font-mono"}>
                            {trade.win ? "WIN" : "LOSS"}
                          </Badge>
                          {trade.closedOpen && (
                            <Badge variant="outline" className="text-yellow-500 border-yellow-500/30 bg-yellow-500/10 text-[10px] font-mono">ABERTO</Badge>
                          )}
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

      {/* Sensitivity analysis */}
      {mode === "ticker" && sensitivityResult && (
        sensitivityResult.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {sensitivityResult.error}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>Sensibilidade — {sensitivityResult.ticker} · {sensitivityResult.strategy.toUpperCase()}</span>
              <span>{sensitivityResult.start} → {sensitivityResult.end}</span>
              {!sensitivityResult.baseline.error && (
                <span>
                  Config. atual: <span className={sensitivityResult.baseline.totalReturn >= 0 ? "text-green-400" : "text-red-400"}>
                    {sensitivityResult.baseline.totalReturn >= 0 ? "+" : ""}{sensitivityResult.baseline.totalReturn.toFixed(2)}%
                  </span>
                </span>
              )}
            </div>

            {Object.entries(
              sensitivityResult.variations.reduce<Record<string, SensitivityRun[]>>((acc, v) => {
                const key = v.param ?? "?";
                (acc[key] ??= []).push(v);
                return acc;
              }, {})
            ).map(([param, rows]) => (
              <div key={param} className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  {SENSITIVITY_PARAM_LABEL[param] ?? param}
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Valor</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Sharpe</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Max DD</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 font-bold text-primary">
                          {formatSensitivityValue(param, row.value ?? 0)}
                        </td>
                        {row.error ? (
                          <td colSpan={5} className="px-4 py-2.5 text-muted-foreground">{row.error}</td>
                        ) : (
                          <>
                            <td className={`px-4 py-2.5 text-right font-bold ${row.totalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {row.totalReturn >= 0 ? "+" : ""}{row.totalReturn.toFixed(2)}%
                            </td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.sharpe.toFixed(2)}</td>
                            <td className="px-4 py-2.5 text-right text-red-400">{row.maxDrawdown.toFixed(2)}%</td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.totalTrades}</td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.totalTrades > 0 ? `${row.winRate}%` : "—"}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
