import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

interface SessionMove {
  date: string;
  gap_pct: number;
  close_pct: number;
  intraday_range_pct: number;
  volume: number;
}

interface ReactionEvent {
  earnings_date: string;
  announcement_day: SessionMove | null;
  next_day: SessionMove | null;
}

interface ReactionSummary {
  n_events: number;
  gap_pct_mean: number;
  gap_pct_abs_mean: number;
  close_pct_mean: number;
  close_pct_abs_mean: number;
  close_pct_std: number | null;
  intraday_range_pct_mean: number;
  volume_ratio_mean: number | null;
  suggested_threshold_pct: number;
  current_price: number;
  r1_price: number;
  r2_price: number;
  s1_price: number;
  s2_price: number;
}

interface ReactionResult {
  ticker: string;
  error?: string;
  summary?: ReactionSummary;
  events?: ReactionEvent[];
}

const DEFAULT_TICKERS = "NVDA,SMCI,AVGO,SKHY,ARM";

function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

function SessionCell({ move }: { move: SessionMove | null }) {
  if (!move) return <span className="text-muted-foreground">sem pregão</span>;
  return (
    <span>
      gap <span className={move.gap_pct >= 0 ? "text-green-400" : "text-red-400"}>{fmtPct(move.gap_pct)}</span>
      {" / "}
      fech <span className={move.close_pct >= 0 ? "text-green-400" : "text-red-400"}>{fmtPct(move.close_pct)}</span>
    </span>
  );
}

// Interpretação em texto puro, calculada em cima dos mesmos campos de summary/events
// já retornados pelo backend -- nenhuma chamada de LLM, só regras diretas (mesmo
// princípio "não usa LLM" da análise em si).
function interpretResult(r: ReactionResult): string[] {
  if (!r.summary) return [];
  const s = r.summary;
  const notes: string[] = [];

  if (s.suggested_threshold_pct >= 8) {
    notes.push(
      `Volatilidade histórica alta: as reações passadas chegam a mover o preço ±${s.suggested_threshold_pct.toFixed(1)}% no extremo — vale reduzir o tamanho da posição e usar stops mais largos.`,
    );
  } else if (s.suggested_threshold_pct >= 4) {
    notes.push(
      `Volatilidade histórica moderada: espere oscilações de até ±${s.suggested_threshold_pct.toFixed(1)}% em torno do resultado.`,
    );
  } else {
    notes.push(
      `Volatilidade histórica baixa: as reações passadas ficaram dentro de ±${s.suggested_threshold_pct.toFixed(1)}%, sinal de que o mercado já precifica bem os resultados desse papel.`,
    );
  }

  if (Math.abs(s.close_pct_mean) >= 1) {
    const dir = s.close_pct_mean > 0 ? "positivo (alta)" : "negativo (queda)";
    notes.push(
      `Viés histórico ${dir}: em média o papel fechou ${fmtPct(s.close_pct_mean)} na janela de reação, com desvio de ${s.close_pct_std != null ? s.close_pct_std.toFixed(2) : "N/A"}pp — não é garantia de repetição.`,
    );
  } else {
    notes.push(
      `Sem viés direcional claro: a média de fechamento (${fmtPct(s.close_pct_mean)}) é próxima de zero, sugerindo reações historicamente equilibradas entre alta e baixa.`,
    );
  }

  if (s.gap_pct_abs_mean > 0 && s.close_pct_abs_mean > s.gap_pct_abs_mean * 1.3) {
    notes.push(
      `O movimento tende a se ampliar ao longo do pregão: o gap médio de abertura (${s.gap_pct_abs_mean.toFixed(2)}%) é bem menor que a variação até o fechamento (${s.close_pct_abs_mean.toFixed(2)}%).`,
    );
  } else if (s.gap_pct_abs_mean > 0 && s.gap_pct_abs_mean > s.close_pct_abs_mean * 1.3) {
    notes.push(
      `A maior parte do movimento historicamente acontece já na abertura: o gap médio (${s.gap_pct_abs_mean.toFixed(2)}%) é próximo ou maior que a variação até o fechamento (${s.close_pct_abs_mean.toFixed(2)}%).`,
    );
  }

  if (s.volume_ratio_mean != null && s.volume_ratio_mean >= 1.5) {
    notes.push(
      `O volume nos dias de reação costuma ser ${s.volume_ratio_mean.toFixed(1)}x a média do período — confirma que o mercado reage com convicção a esses resultados.`,
    );
  }

  const events = r.events ?? [];
  let nextBigger = 0;
  let annBigger = 0;
  let counted = 0;
  for (const e of events) {
    const a = e.announcement_day;
    const n = e.next_day;
    if (a && n) {
      counted++;
      if (Math.abs(n.close_pct) > Math.abs(a.close_pct)) nextBigger++;
      else annBigger++;
    }
  }
  if (counted >= 2) {
    if (nextBigger > annBigger) {
      notes.push(
        `Em ${nextBigger} de ${counted} eventos com as duas janelas disponíveis, o pregão SEGUINTE ao anúncio teve a reação maior — sinal de que o resultado tende a sair depois do fechamento (AMC).`,
      );
    } else if (annBigger > nextBigger) {
      notes.push(
        `Em ${annBigger} de ${counted} eventos com as duas janelas disponíveis, o próprio dia do anúncio teve a reação maior — sinal de que o resultado tende a sair antes da abertura (BMO).`,
      );
    }
  }

  if (s.n_events < 4) {
    notes.push(
      `Amostra pequena (${s.n_events} evento${s.n_events === 1 ? "" : "s"}) — trate os pontos acima como indicativos, não estatisticamente robustos.`,
    );
  }

  return notes;
}

export default function EarningsReactionPage() {
  const [tickersInput, setTickersInput] = useState(DEFAULT_TICKERS);
  const [lookback, setLookback] = useState("8");
  const [results, setResults] = useState<ReactionResult[] | null>(null);

  const run = useMutation({
    mutationFn: async () => {
      const tickers = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
      const r = await fetch("/api/earnings-reaction/run", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers, lookback: parseInt(lookback, 10) || 8 }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as ReactionResult[];
    },
    onSuccess: (data) => setResults(data),
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Gauge className="h-7 w-7 text-primary" /> REAÇÃO A EARNINGS
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Parametriza a volatilidade esperada em torno de resultados (gap, fechamento, volume) em vez de
          depender do calor do momento — não usa LLM, é cálculo direto sobre o histórico do yfinance.
        </p>
      </div>

      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex flex-col gap-1 sm:col-span-2">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Tickers (separados por vírgula)</label>
            <input
              type="text"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Earnings passados (lookback)</label>
            <input
              type="number" min="1" max="20"
              value={lookback}
              onChange={(e) => setLookback(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending || !tickersInput.trim()}
          className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
        >
          {run.isPending ? (
            <>
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
              Rodando...
            </>
          ) : (
            <><Gauge className="h-4 w-4" /> Rodar análise</>
          )}
        </button>
        {run.isError && <p className="text-sm text-red-400 font-mono">{String(run.error)}</p>}
      </div>

      {results && (
        <div className="space-y-4">
          {results.map((r) => (
            <div key={r.ticker} className="border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center justify-between">
                <span className="font-mono font-bold text-primary">{r.ticker}</span>
                {r.summary && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {r.summary.n_events} evento(s) · threshold sugerido ±{r.summary.suggested_threshold_pct.toFixed(2)}%
                  </span>
                )}
              </div>

              {r.error ? (
                <p className="px-4 py-3 font-mono text-sm text-muted-foreground">⚠ {r.error}</p>
              ) : r.summary ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4">
                    {[
                      { label: "Gap médio", value: fmtPct(r.summary.gap_pct_mean), sub: `|média| ${r.summary.gap_pct_abs_mean.toFixed(2)}%` },
                      { label: "Fechamento médio", value: fmtPct(r.summary.close_pct_mean), sub: `desvio ${r.summary.close_pct_std?.toFixed(2) ?? "N/A"}` },
                      { label: "Range intradiário", value: `${r.summary.intraday_range_pct_mean.toFixed(2)}%`, sub: "" },
                      { label: "Volume vs média", value: r.summary.volume_ratio_mean ? `${r.summary.volume_ratio_mean.toFixed(2)}x` : "N/A", sub: "" },
                    ].map(({ label, value, sub }) => (
                      <div key={label} className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                        <div className="text-lg font-bold font-mono text-foreground">{value}</div>
                        {sub && <div className="text-[10px] font-mono text-muted-foreground mt-0.5">{sub}</div>}
                      </div>
                    ))}
                  </div>

                  <div className="px-4 pb-4">
                    <div className="border border-border/60 rounded-lg bg-background p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-mono text-muted-foreground uppercase">
                          Níveis projetados (base: {fmtUsd(r.summary.current_price)})
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground/70">
                          bandas estatísticas, não suporte/resistência técnico
                        </span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">R2 · alvo extremo</div>
                          <div className="text-green-400 font-bold">{fmtUsd(r.summary.r2_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">R1 · alvo médio</div>
                          <div className="text-green-400/80 font-bold">{fmtUsd(r.summary.r1_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">S1 · suporte médio</div>
                          <div className="text-red-400/80 font-bold">{fmtUsd(r.summary.s1_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">S2 · risco extremo</div>
                          <div className="text-red-400 font-bold">{fmtUsd(r.summary.s2_price)}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {r.events && r.events.length > 0 && (
                    <div className="overflow-x-auto border-t border-border">
                      <table className="w-full font-mono text-sm">
                        <thead className="bg-secondary/20">
                          <tr>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Earnings</th>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Dia do anúncio</th>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Dia seguinte</th>
                          </tr>
                        </thead>
                        <tbody>
                          {r.events.map((e, idx) => (
                            <tr key={e.earnings_date} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                              <td className="px-4 py-2 text-muted-foreground">{e.earnings_date}</td>
                              <td className="px-4 py-2"><SessionCell move={e.announcement_day} /></td>
                              <td className="px-4 py-2"><SessionCell move={e.next_day} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {(() => {
                    const notes = interpretResult(r);
                    if (notes.length === 0) return null;
                    return (
                      <div className="px-4 pb-4 pt-3 border-t border-border">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">
                          Interpretação
                        </div>
                        <ul className="space-y-1.5">
                          {notes.map((note, idx) => (
                            <li key={idx} className="font-mono text-xs text-muted-foreground leading-relaxed flex gap-2">
                              <span className="text-primary shrink-0">›</span>
                              <span>{note}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })()}
                </>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
