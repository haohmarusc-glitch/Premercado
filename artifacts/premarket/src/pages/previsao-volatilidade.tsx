import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Waves } from "lucide-react";
import { ExportarRelatorio, cabecalho, itens } from "@/components/exportar-relatorio";

// Tela "Previsão de Vol": o ciclo de vida da volatilidade por ticker, do
// ciclo_volatilidade.py. Prevê MAGNITUDE e FASE, nunca direção — a banda de
// amanhã diz "quanto o papel deve andar", não pra onde.

interface ItemVol {
  ticker: string;
  error?: string;
  fase?: "COMPRIMIDA" | "GATILHO" | "EXPANSAO" | "DECAIMENTO" | "NORMAL";
  motivos?: string[];
  preco?: number;
  sigmaDiaPct?: number;
  volEstruturalAnualPct?: number;
  ewmaAnualPct?: number;
  razaoRegime?: number;
  bandaAmanha?: { low: number; high: number; low2: number; high2: number };
  rangeHojePct?: number;
  rangesDecrescentes?: number;
  atrTendencia?: string;
  rvol?: number | null;
  squeeze?: boolean;
  squeezePercentil?: number | null;
  diasParaNormalizar?: number | null;
  diasUsados?: number;
  earningsProximo?: { data: string; dias: number };
  fonteHistorico?: string;
}

// Cor e descrição curta por fase — o vocabulário fixo do módulo.
const FASES: Record<string, { cor: string; borda: string; descricao: string }> = {
  COMPRIMIDA: { cor: "text-blue-400", borda: "border-blue-500/40 bg-blue-500/10", descricao: "mola armada — expansão provável, dia e direção incertos" },
  GATILHO: { cor: "text-orange-400", borda: "border-orange-500/40 bg-orange-500/10", descricao: "o range de hoje estourou o esperado, com volume" },
  EXPANSAO: { cor: "text-red-400", borda: "border-red-500/40 bg-red-500/10", descricao: "episódio vivo — vol rodando bem acima do normal do papel" },
  DECAIMENTO: { cor: "text-yellow-400", borda: "border-yellow-500/40 bg-yellow-500/10", descricao: "episódio morrendo — ranges encolhendo, meia-vida ~11 pregões" },
  NORMAL: { cor: "text-green-400", borda: "border-green-500/40 bg-green-500/10", descricao: "sem nada digno de nota" },
};

function fmtUsd(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toFixed(2)}`;
}

function montarRelatorio(items: ItemVol[]): string {
  const blocos = [cabecalho(
    `Previsão de volatilidade — ${items.map((i) => i.ticker).join(", ")}`,
    "Fase do ciclo de vol + banda de magnitude para amanhã (não prevê direção)",
  )];
  for (const i of items) {
    if (i.error || !i.fase) {
      blocos.push(`## ${i.ticker}\n\nSem resultado: ${i.error ?? "dados insuficientes"}`);
      continue;
    }
    blocos.push(`## ${i.ticker} — ${i.fase}\n\n` + itens([
      ["Fase", `${i.fase} (${FASES[i.fase]?.descricao ?? ""})`],
      ["Preço", fmtUsd(i.preco)],
      ["σ diário (EWMA)", `${i.sigmaDiaPct?.toFixed(2) ?? "—"}%`],
      ["Banda de amanhã (±1σ)", `${fmtUsd(i.bandaAmanha?.low)} – ${fmtUsd(i.bandaAmanha?.high)}`],
      ["Banda de amanhã (±2σ)", `${fmtUsd(i.bandaAmanha?.low2)} – ${fmtUsd(i.bandaAmanha?.high2)}`],
      ["Vol EWMA / estrutural (anual)", `${i.ewmaAnualPct?.toFixed(1) ?? "—"}% / ${i.volEstruturalAnualPct?.toFixed(1) ?? "—"}% (razão ${i.razaoRegime?.toFixed(2) ?? "—"})`],
      ["Range de hoje", `${i.rangeHojePct?.toFixed(2) ?? "—"}%`],
      ["Ranges decrescentes", i.rangesDecrescentes ?? "—"],
      ["ATR14", i.atrTendencia ?? "—"],
      ["RVOL", i.rvol != null ? `${i.rvol.toFixed(2)}x` : "—"],
      ["Squeeze", i.squeeze ? `sim (percentil ${i.squeezePercentil ?? "—"})` : "não"],
      ["Dias p/ normalizar", i.diasParaNormalizar ?? "—"],
      ["Earnings próximo", i.earningsProximo ? `${i.earningsProximo.data} (${i.earningsProximo.dias} dia(s))` : "não"],
    ]));
    if (i.motivos?.length) {
      blocos.push(i.motivos.map((m) => `- ${m}`).join("\n"));
    }
  }
  return blocos.join("\n\n");
}

export default function PrevisaoVolatilidadePage() {
  const [tickersInput, setTickersInput] = useState("");
  const [items, setItems] = useState<ItemVol[] | null>(null);

  const run = useMutation({
    mutationFn: async () => {
      const tickers = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
      const qs = tickers.length ? `?tickers=${encodeURIComponent(tickers.join(","))}` : "";
      const r = await fetch(`/api/vol-cycle${qs}`, { credentials: "include" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Falha na análise");
      return (data as { items: ItemVol[] }).items;
    },
    onSuccess: setItems,
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Waves className="h-7 w-7 text-primary" /> PREVISÃO DE VOL
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Ciclo de vida da volatilidade: compressão → gatilho → expansão → decaimento. Prevê magnitude
          e fase, nunca direção. Véspera de earnings, use a Reação a Earnings. Não usa LLM.
        </p>
      </div>

      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-mono text-muted-foreground uppercase">
            Tickers (separados por vírgula — vazio usa a carteira)
          </label>
          <input
            type="text"
            value={tickersInput}
            onChange={(e) => setTickersInput(e.target.value)}
            placeholder="vazio = carteira · ex: INTC,PDD,BIDU"
            className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
        >
          {run.isPending ? (
            <>
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
              Rodando...
            </>
          ) : (
            <><Waves className="h-4 w-4" /> Rodar previsão</>
          )}
        </button>
        {run.isError && <p className="text-sm text-red-400 font-mono">{String(run.error)}</p>}
        {items && items.length > 0 && (
          <div className="border-t border-border/40 pt-4">
            <ExportarRelatorio
              titulo={`Previsão de vol — ${items.map((i) => i.ticker).join(", ")}`}
              mode="tela_previsao_vol"
              tickers={items.map((i) => i.ticker)}
              construir={() => montarRelatorio(items)}
            />
          </div>
        )}
      </div>

      {items && (
        <div className="space-y-4">
          {items.map((i) => {
            const fase = i.fase ? FASES[i.fase] : undefined;
            return (
              <div key={i.ticker} className="border border-border rounded-lg overflow-hidden">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center justify-between flex-wrap gap-2">
                  <span className="font-mono font-bold text-primary">{i.ticker}</span>
                  {i.fase && fase && (
                    <span className={`font-mono text-xs font-bold px-2.5 py-1 rounded border ${fase.borda} ${fase.cor}`}>
                      {i.fase}
                    </span>
                  )}
                </div>

                {i.error ? (
                  <p className="px-4 py-3 font-mono text-sm text-muted-foreground">⚠ {i.error}</p>
                ) : i.fase ? (
                  <div className="p-4 space-y-4">
                    <p className={`font-mono text-sm ${fase?.cor ?? ""}`}>{fase?.descricao}</p>

                    {i.earningsProximo && (
                      <p className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400">
                        ⚠ Balanço em {i.earningsProximo.dias} dia(s) ({i.earningsProximo.data}) — na véspera de
                        earnings a previsão certa vem do threshold da Reação a Earnings, não da banda de vol.
                      </p>
                    )}
                    {i.fonteHistorico && (
                      <p className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400">
                        ⚠ Calculado sobre dado degradado ({i.fonteHistorico})
                      </p>
                    )}

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Banda de amanhã (±1σ)</div>
                        <div className="text-lg font-bold font-mono text-foreground">
                          {fmtUsd(i.bandaAmanha?.low)} – {fmtUsd(i.bandaAmanha?.high)}
                        </div>
                        <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
                          ±2σ: {fmtUsd(i.bandaAmanha?.low2)} – {fmtUsd(i.bandaAmanha?.high2)}
                        </div>
                      </div>
                      <div className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">σ diário (EWMA)</div>
                        <div className="text-lg font-bold font-mono text-foreground">±{i.sigmaDiaPct?.toFixed(2)}%</div>
                        <div className="text-[10px] font-mono text-muted-foreground mt-0.5">preço {fmtUsd(i.preco)}</div>
                      </div>
                      <div className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Regime (EWMA/estrutural)</div>
                        <div className={`text-lg font-bold font-mono ${(i.razaoRegime ?? 1) >= 1.3 ? "text-red-400" : (i.razaoRegime ?? 1) <= 0.7 ? "text-blue-400" : "text-foreground"}`}>
                          {i.razaoRegime?.toFixed(2)}x
                        </div>
                        <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
                          {i.ewmaAnualPct?.toFixed(1)}% vs {i.volEstruturalAnualPct?.toFixed(1)}% a.a.
                        </div>
                      </div>
                      <div className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Fim estimado</div>
                        <div className="text-lg font-bold font-mono text-foreground">
                          {i.diasParaNormalizar != null ? `~${i.diasParaNormalizar} pregões` : "—"}
                        </div>
                        <div className="text-[10px] font-mono text-muted-foreground mt-0.5">meia-vida λ=0.94 ≈ 11 pregões</div>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1 font-mono text-xs text-muted-foreground">
                      <span>range hoje: <span className="text-foreground">{i.rangeHojePct?.toFixed(2)}%</span></span>
                      <span>ranges decrescentes: <span className="text-foreground">{i.rangesDecrescentes}</span></span>
                      <span>ATR14: <span className="text-foreground">{i.atrTendencia}</span></span>
                      <span>RVOL: <span className="text-foreground">{i.rvol != null ? `${i.rvol.toFixed(2)}x` : "—"}</span></span>
                      <span>squeeze: <span className="text-foreground">{i.squeeze ? `sim (p${i.squeezePercentil})` : "não"}</span></span>
                      <span>pregões usados: <span className="text-foreground">{i.diasUsados}</span></span>
                    </div>

                    {i.motivos && i.motivos.length > 0 && (
                      <ul className="space-y-1 border-t border-border/40 pt-3">
                        {i.motivos.map((m, idx) => (
                          <li key={idx} className="font-mono text-xs text-muted-foreground flex gap-2">
                            <span className="text-primary shrink-0">›</span>
                            <span>{m}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
