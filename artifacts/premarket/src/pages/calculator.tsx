import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { Calculator, TrendingDown, TrendingUp, DollarSign, AlertTriangle, Activity, PieChart, Grid3x3 } from "lucide-react";

interface StopAtrResult {
  ticker: string;
  currentPrice: number;
  atr14: number;
  atrMultiplier: number;
  suggestedStop: number;
  stopDistancePct: number;
  error?: string;
}

interface ExposureResult {
  totalPositions: number;
  totalInvested: number;
  tickers: { ticker: string; investedAmount: number; pct: number }[];
  maxSinglePositionPct: number;
  concentrationRisk: "LOW" | "MEDIUM" | "HIGH";
  error?: string;
}

interface CorrelationResult {
  tickers: string[];
  matrix: (number | null)[][];
  pairs: { a: string; b: string; correlation: number }[];
  highCorrelationPairs: { a: string; b: string; correlation: number }[];
  skipped: string[];
  error?: string;
}

export function correlationColor(v: number | null): string {
  if (v == null) return "text-muted-foreground";
  const abs = Math.abs(v);
  if (abs >= 0.8) return v > 0 ? "text-red-400" : "text-blue-400";
  if (abs >= 0.5) return v > 0 ? "text-yellow-400" : "text-cyan-400";
  return "text-muted-foreground";
}

function fmt(n: number, decimals = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function Field({
  label, value, onChange, prefix, suffix, step = "0.01", min = "0",
}: {
  label: string; value: string; onChange: (v: string) => void;
  prefix?: string; suffix?: string; step?: string; min?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-widest">
        {label}
      </label>
      <div className="flex items-center border border-border rounded-md bg-secondary/40 focus-within:border-primary transition-colors">
        {prefix && (
          <span className="px-3 text-sm font-mono text-muted-foreground border-r border-border">{prefix}</span>
        )}
        <input
          type="number"
          min={min}
          step={step}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 bg-transparent px-3 py-2 text-sm font-mono text-foreground outline-none"
        />
        {suffix && (
          <span className="px-3 text-sm font-mono text-muted-foreground border-l border-border">{suffix}</span>
        )}
      </div>
    </div>
  );
}

function ResultRow({ label, value, highlight }: { label: string; value: string; highlight?: "green" | "red" | "yellow" }) {
  const color = highlight === "green" ? "text-green-400" : highlight === "red" ? "text-red-400" : highlight === "yellow" ? "text-yellow-400" : "text-foreground";
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-border/40 last:border-0">
      <span className="text-xs font-mono text-muted-foreground">{label}</span>
      <span className={`text-sm font-mono font-bold ${color}`}>{value}</span>
    </div>
  );
}

export default function CalculatorPage() {
  const [capital, setCapital]     = useState("10000");
  const [riskPct, setRiskPct]     = useState("1");
  const [entry, setEntry]         = useState("");
  const [stop, setStop]           = useState("");
  const [target, setTarget]       = useState("");

  const calc = useMemo(() => {
    const C = parseFloat(capital) || 0;
    const R = parseFloat(riskPct) || 0;
    const E = parseFloat(entry) || 0;
    const S = parseFloat(stop) || 0;
    const T = parseFloat(target) || 0;

    if (!C || !R || !E || !S || E <= 0 || S <= 0) return null;

    const maxLoss    = C * (R / 100);
    const riskPerSh  = Math.abs(E - S);
    if (riskPerSh === 0) return null;

    const shares     = Math.floor(maxLoss / riskPerSh);
    const position   = shares * E;
    const pctCapital = (position / C) * 100;
    const stopPct    = ((S - E) / E) * 100;

    let profitDollars: number | null = null;
    let rrRatio: number | null = null;
    let targetPct: number | null = null;

    if (T > 0) {
      profitDollars = shares * (T - E);
      rrRatio       = profitDollars / maxLoss;
      targetPct     = ((T - E) / E) * 100;
    }

    return { maxLoss, riskPerSh, shares, position, pctCapital, stopPct, profitDollars, rrRatio, targetPct };
  }, [capital, riskPct, entry, stop, target]);

  const rrColor = calc?.rrRatio != null
    ? calc.rrRatio >= 2 ? "green" : calc.rrRatio >= 1 ? "yellow" : "red"
    : undefined;

  // Stop por ATR (backend / yfinance)
  const [atrTicker, setAtrTicker] = useState("NVDA");
  const [atrMult, setAtrMult] = useState("2");
  const atrStop = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/risk/stop-distance", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: atrTicker.toUpperCase(), atrMultiplier: parseFloat(atrMult) || 2 }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as StopAtrResult;
    },
  });

  // Exposição da carteira (backend / DB)
  const exposure = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/risk/portfolio-exposure", { credentials: "include" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as ExposureResult;
    },
  });

  // Correlação entre os retornos das posições da carteira (backend / yfinance)
  const correlation = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/risk/portfolio-correlation", { credentials: "include" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as CorrelationResult;
    },
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500 max-w-2xl">
      {/* Header */}
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-3">
          <Calculator className="h-7 w-7 text-primary" />
          CALCULADORA DE RISCO
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Dimensione sua posição antes de entrar — proteja o capital primeiro.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Inputs */}
        <div className="space-y-4">
          <p className="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-widest">Parâmetros</p>

          <Field label="Capital da conta" value={capital} onChange={setCapital} prefix="$" step="100" />
          <Field label="Risco máximo por trade" value={riskPct} onChange={setRiskPct} suffix="%" step="0.1" min="0.1" />

          <div className="border-t border-border/40 pt-4 space-y-4">
            <Field label="Preço de entrada" value={entry} onChange={setEntry} prefix="$" step="0.01" />
            <Field label="Stop loss" value={stop} onChange={setStop} prefix="$" step="0.01" />
            <Field label="Alvo de lucro (opcional)" value={target} onChange={setTarget} prefix="$" step="0.01" />
          </div>
        </div>

        {/* Results */}
        <div className="space-y-4">
          <p className="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-widest">Resultado</p>

          {!calc ? (
            <div className="border border-dashed border-border rounded-lg p-8 text-center">
              <Calculator className="h-8 w-8 text-muted-foreground mx-auto mb-2 opacity-40" />
              <p className="text-xs font-mono text-muted-foreground">
                Preencha capital, risco %, entrada e stop.
              </p>
            </div>
          ) : (
            <div className="border border-border rounded-lg bg-card">
              <div className="p-4 border-b border-border bg-secondary/20 rounded-t-lg">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-muted-foreground">POSIÇÃO</span>
                  <span className="text-2xl font-mono font-bold text-primary">{calc.shares} ações</span>
                </div>
                <div className="text-xs font-mono text-muted-foreground mt-1">
                  ${fmt(calc.position)} · {fmt(calc.pctCapital)}% do capital
                </div>
              </div>

              <div className="p-4 space-y-0">
                <ResultRow
                  label="Perda máxima (risco)"
                  value={`-$${fmt(calc.maxLoss)}`}
                  highlight="red"
                />
                <ResultRow
                  label="Risco por ação"
                  value={`$${fmt(calc.riskPerSh)}`}
                />
                <ResultRow
                  label="Stop loss"
                  value={`${fmt(calc.stopPct, 2)}%`}
                  highlight="red"
                />
                {calc.profitDollars != null && (
                  <ResultRow
                    label="Lucro potencial"
                    value={`+$${fmt(calc.profitDollars)}`}
                    highlight="green"
                  />
                )}
                {calc.targetPct != null && (
                  <ResultRow
                    label="Alvo"
                    value={`+${fmt(calc.targetPct, 2)}%`}
                    highlight="green"
                  />
                )}
                {calc.rrRatio != null && (
                  <ResultRow
                    label="Risco/Retorno"
                    value={`1 : ${fmt(calc.rrRatio, 2)}`}
                    highlight={rrColor}
                  />
                )}
              </div>
            </div>
          )}

          {/* RR guide */}
          {calc?.rrRatio != null && (
            <div className={`rounded-lg border px-4 py-3 text-xs font-mono flex items-start gap-2 ${
              calc.rrRatio >= 2
                ? "border-green-500/30 bg-green-500/5 text-green-400"
                : calc.rrRatio >= 1
                ? "border-yellow-500/30 bg-yellow-500/5 text-yellow-400"
                : "border-red-500/30 bg-red-500/5 text-red-400"
            }`}>
              {calc.rrRatio >= 2
                ? <TrendingUp className="h-4 w-4 shrink-0 mt-0.5" />
                : <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />}
              <span>
                {calc.rrRatio >= 2
                  ? "Boa relação R/R. Você precisa acertar menos de 34% das operações para ser lucrativo."
                  : calc.rrRatio >= 1
                  ? "R/R aceitável. Mire em pelo menos 1:2 para manter a conta crescendo."
                  : "R/R abaixo de 1:1 — o risco supera o potencial de lucro. Ajuste o alvo ou o stop."}
              </span>
            </div>
          )}

          {/* Rules */}
          <div className="border border-border/40 rounded-lg p-4 space-y-2">
            <p className="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-widest mb-3">Regras de ouro</p>
            {[
              { icon: <DollarSign className="h-3 w-3" />, text: "Nunca arrisque mais de 1-2% do capital por trade" },
              { icon: <TrendingDown className="h-3 w-3" />, text: "Stop definido ANTES de entrar — nunca mova contra você" },
              { icon: <TrendingUp className="h-3 w-3" />, text: "Mire R/R ≥ 1:2 — 40% de acerto já é lucrativo" },
              { icon: <AlertTriangle className="h-3 w-3" />, text: "Earnings sem posição: volatilidade é imprevisível" },
            ].map((r, i) => (
              <div key={i} className="flex items-start gap-2 text-xs font-mono text-muted-foreground">
                <span className="text-primary mt-0.5 shrink-0">{r.icon}</span>
                {r.text}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Ferramentas avançadas (backend) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
        {/* Stop por ATR */}
        <div className="border border-border rounded-lg bg-card p-5 space-y-4">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-mono font-bold text-foreground uppercase tracking-widest">Stop por ATR</h2>
          </div>
          <p className="text-xs font-mono text-muted-foreground leading-relaxed">
            Calcula um stop baseado na volatilidade real (ATR de 14 dias) — distância que respeita o ruído do ativo.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Ticker" value={atrTicker} onChange={(v) => setAtrTicker(v.toUpperCase())} step="1" />
            <Field label="Multiplicador ATR" value={atrMult} onChange={setAtrMult} step="0.5" min="0.5" />
          </div>
          <button
            onClick={() => atrStop.mutate()}
            disabled={atrStop.isPending || !atrTicker.trim()}
            className="px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-xs font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {atrStop.isPending ? "Calculando..." : "Calcular Stop"}
          </button>
          {atrStop.isError && <p className="text-xs text-red-400 font-mono">{String(atrStop.error)}</p>}
          {atrStop.data && !atrStop.data.error && (
            <div className="border border-border rounded-lg bg-secondary/20 p-3 space-y-0">
              <ResultRow label="Preço atual" value={`$${fmt(atrStop.data.currentPrice)}`} />
              <ResultRow label="ATR (14d)" value={`$${fmt(atrStop.data.atr14)}`} />
              <ResultRow label="Stop sugerido" value={`$${fmt(atrStop.data.suggestedStop)}`} highlight="red" />
              <ResultRow label="Distância do stop" value={`-${fmt(atrStop.data.stopDistancePct)}%`} highlight="yellow" />
            </div>
          )}
        </div>

        {/* Exposição da carteira */}
        <div className="border border-border rounded-lg bg-card p-5 space-y-4">
          <div className="flex items-center gap-2">
            <PieChart className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-mono font-bold text-foreground uppercase tracking-widest">Exposição da Carteira</h2>
          </div>
          <p className="text-xs font-mono text-muted-foreground leading-relaxed">
            Analisa a concentração das suas posições. Concentração alta em um único ativo aumenta o risco.
          </p>
          <button
            onClick={() => exposure.mutate()}
            disabled={exposure.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-xs font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {exposure.isPending ? "Analisando..." : "Analisar Carteira"}
          </button>
          {exposure.isError && <p className="text-xs text-red-400 font-mono">{String(exposure.error)}</p>}
          {exposure.data && !exposure.data.error && (
            <div className="space-y-3">
              <div className="flex items-center justify-between border border-border rounded-lg bg-secondary/20 p-3">
                <div>
                  <div className="text-[10px] font-mono text-muted-foreground uppercase">Concentração</div>
                  <div className={`text-lg font-mono font-bold ${
                    exposure.data.concentrationRisk === "HIGH" ? "text-red-400"
                    : exposure.data.concentrationRisk === "MEDIUM" ? "text-yellow-400" : "text-green-400"
                  }`}>
                    {exposure.data.concentrationRisk}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase">Total investido</div>
                  <div className="text-lg font-mono font-bold text-foreground">${fmt(exposure.data.totalInvested)}</div>
                </div>
              </div>
              <div className="border border-border rounded-lg bg-card p-3 space-y-2">
                {exposure.data.tickers
                  .slice()
                  .sort((a, b) => b.pct - a.pct)
                  .map((t) => (
                    <div key={t.ticker} className="space-y-1">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="font-bold text-foreground">{t.ticker}</span>
                        <span className="text-muted-foreground">{fmt(t.pct)}% · ${fmt(t.investedAmount)}</span>
                      </div>
                      <div className="h-1.5 bg-secondary rounded-full overflow-hidden">
                        <div
                          className={`h-full ${t.pct > 30 ? "bg-red-400" : t.pct > 15 ? "bg-yellow-400" : "bg-green-400"}`}
                          style={{ width: `${Math.min(t.pct, 100)}%` }}
                        />
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>

        {/* Matriz de correlação da carteira */}
        <div className="border border-border rounded-lg bg-card p-5 space-y-4 md:col-span-2">
          <div className="flex items-center gap-2">
            <Grid3x3 className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-mono font-bold text-foreground uppercase tracking-widest">Correlação da Carteira</h2>
          </div>
          <p className="text-xs font-mono text-muted-foreground leading-relaxed">
            Diversificação por peso em dólar não é diversificação de risco se os ativos se movem juntos.
            Correlação de Pearson entre os retornos diários (6 meses) das posições atuais.
          </p>
          <button
            onClick={() => correlation.mutate()}
            disabled={correlation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-xs font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {correlation.isPending ? "Calculando..." : "Calcular Correlação"}
          </button>
          {correlation.isError && <p className="text-xs text-red-400 font-mono">{String(correlation.error)}</p>}
          {correlation.data?.error && <p className="text-xs text-red-400 font-mono">{correlation.data.error}</p>}
          {correlation.data && !correlation.data.error && (
            <div className="space-y-4">
              {correlation.data.highCorrelationPairs.length > 0 && (
                <div className="flex items-start gap-2 text-xs font-mono rounded px-3 py-2 border border-yellow-600/50 bg-yellow-500/5 text-yellow-500">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <span>
                    {correlation.data.highCorrelationPairs.length} par(es) com |correlação| ≥ 0.8 —
                    {" "}{correlation.data.highCorrelationPairs.map((p) => `${p.a}×${p.b} (${fmt(p.correlation)})`).join(", ")}.
                    Essas posições tendem a subir/cair juntas: o risco real da carteira é maior do que a soma das partes sugere.
                  </span>
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="text-xs font-mono border-collapse">
                  <thead>
                    <tr>
                      <th className="p-2 text-muted-foreground"></th>
                      {correlation.data.tickers.map((t) => (
                        <th key={t} className="p-2 text-muted-foreground font-bold">{t}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {correlation.data.matrix.map((row, i) => (
                      <tr key={correlation.data!.tickers[i]}>
                        <td className="p-2 text-muted-foreground font-bold">{correlation.data!.tickers[i]}</td>
                        {row.map((v, j) => (
                          <td
                            key={j}
                            className={`p-2 text-center border border-border/40 ${correlationColor(v)} ${i === j ? "bg-secondary/30" : ""}`}
                          >
                            {v != null ? fmt(v) : "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {correlation.data.skipped.length > 0 && (
                <p className="text-[10px] font-mono text-muted-foreground">
                  Sem dados suficientes para: {correlation.data.skipped.join(", ")}.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
