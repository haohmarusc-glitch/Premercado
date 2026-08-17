import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Activity, Gauge, ScanSearch, Sparkles, TrendingUp } from "lucide-react";
import { ExportarRelatorio, cabecalho, itens, tabela, pct } from "@/components/exportar-relatorio";
import { MarkdownContent } from "@/components/markdown";

// Tela "Análise Rápida": os três comandos que antes só rodavam por SSH na VPS,
// agora como três botões sobre um ticker avulso. Cada botão bate numa rota que
// já existia (tendência/técnica) ou na nova /ticker-snapshot + reação a
// earnings — nada aqui calcula; a tela só orquestra e exibe.

interface TrendItem {
  ticker: string;
  price?: number;
  trend?: string;
  score?: number;
  components?: {
    maCruzamento?: string;
    precoVsSma200?: string;
    estrutura?: string;
    macd?: string;
    rsi?: number;
    rsiNota?: string;
  };
  news?: {
    label?: string;
    score?: number;
    destaques?: { title: string; tone: string }[];
  };
  confluence?: string;
  sinal?: string;
  sinalMotivo?: string;
  stale?: boolean;
  error?: string;
}

interface TechItem {
  ticker: string;
  price?: number;
  changePct?: number;
  rsi?: number;
  rsiSignal?: string;
  macdHistogram?: number;
  macdTrend?: string;
  sma20?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  pctAboveSma50?: number | null;
  pctAboveSma200?: number | null;
  volumeRatio?: number | null;
  rvol?: number | null;
  rvolSignal?: string;
  vwap?: number | null;
  priceVsVwapPct?: number | null;
  vwapSignal?: string;
  error?: string;
}

interface Snapshot {
  ticker: string;
  benchmark: string;
  price?: number | null;
  yearLow?: number | null;
  yearHigh?: number | null;
  sma50?: number | null;
  sma200?: number | null;
  volAnnual?: number | null;
  betaSector?: number | null;
  daysUsed?: number | null;
  sectorMomentum?: { benchmark: string; momentumAnnualPct: number; lookbackDays: number } | null;
  fontesDegradadas?: Record<string, string>;
  quoteError?: string;
  cenarioError?: string;
  error?: string;
}

interface SessionMove {
  date: string;
  gap_pct: number;
  close_pct: number;
  intraday_range_pct: number;
  volume: number;
}

interface ReactionResult {
  ticker: string;
  error?: string;
  summary?: {
    n_events: number;
    gap_pct_mean: number;
    close_pct_mean: number;
    close_pct_abs_mean: number;
    close_pct_std: number | null;
    volume_ratio_mean: number | null;
    suggested_threshold_pct: number;
    current_price: number;
    r1_price: number;
    r2_price: number;
    s1_price: number;
    s2_price: number;
    runup?: {
      runup_atual_pct?: number;
      estado_atual?: string;
      corr_runup_reacao?: number | null;
      esticado_n?: number;
      esticado_caiu_n?: number;
      esticado_reacao_media?: number | null;
      descontado_n?: number;
      descontado_subiu_n?: number;
      descontado_reacao_media?: number | null;
    };
  };
  events?: { earnings_date: string; runup_pct?: number | null; announcement_day: SessionMove | null; next_day: SessionMove | null }[];
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toFixed(2)}`;
}

async function getJson(url: string): Promise<unknown> {
  const r = await fetch(url, { credentials: "include" });
  const data = await r.json();
  if (!r.ok) throw new Error((data as { error?: string }).error || "Falha na requisição");
  return data;
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "pos" | "neg" }) {
  const cor = tone === "pos" ? "text-green-400" : tone === "neg" ? "text-red-400" : "text-foreground";
  return (
    <div className="border border-border/60 rounded-lg bg-background p-3">
      <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
      <div className={`text-lg font-bold font-mono ${cor}`}>{value}</div>
      {sub && <div className="text-[10px] font-mono text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}

function Painel({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-secondary/30 font-mono font-bold text-primary text-sm uppercase">
        {titulo}
      </div>
      <div className="p-4 space-y-4">{children}</div>
    </div>
  );
}

const TOM_TENDENCIA: Record<string, string> = {
  "alta forte": "text-green-400",
  alta: "text-green-400/80",
  lateral: "text-yellow-400",
  baixa: "text-red-400/80",
  "baixa forte": "text-red-400",
};

export default function AnaliseRapidaPage() {
  const [tickerInput, setTickerInput] = useState("");
  const [benchmark, setBenchmark] = useState("SMH");
  const [trend, setTrend] = useState<TrendItem | null>(null);
  const [tech, setTech] = useState<TechItem | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [reaction, setReaction] = useState<ReactionResult | null>(null);
  const [analiseIA, setAnaliseIA] = useState<{ markdown: string; usage?: { total_cost_usd?: number }; fontes?: string[]; truncado?: boolean } | null>(null);

  const ticker = tickerInput.trim().toUpperCase();

  const runTrend = useMutation({
    mutationFn: async () => {
      const data = (await getJson(`/api/trend?tickers=${encodeURIComponent(ticker)}`)) as { items: TrendItem[] };
      return data.items[0] ?? { ticker, error: "Sem resultado" };
    },
    // Dados novos invalidam a leitura da IA — texto antigo sobre painel novo
    // seria análise de outro retrato.
    onSuccess: (d) => { setTrend(d); setAnaliseIA(null); },
  });

  const runTech = useMutation({
    mutationFn: async () => {
      const data = (await getJson(`/api/technicals?tickers=${encodeURIComponent(ticker)}`)) as { items: TechItem[] };
      return data.items[0] ?? { ticker, error: "Sem resultado" };
    },
    onSuccess: (d) => { setTech(d); setAnaliseIA(null); },
  });

  const runNiveis = useMutation({
    mutationFn: async () => {
      // As duas fontes em paralelo; uma falhar não derruba a outra —
      // mesmo princípio de falha parcial do script de snapshot.
      const [snapRes, reacRes] = await Promise.allSettled([
        getJson(`/api/ticker-snapshot?ticker=${encodeURIComponent(ticker)}&benchmark=${encodeURIComponent(benchmark.trim().toUpperCase() || "SMH")}`),
        fetch("/api/earnings-reaction/run", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tickers: [ticker], lookback: 8 }),
        }).then(async (r) => {
          const data = await r.json();
          if (!r.ok) throw new Error(data.error || "Falha na reação a earnings");
          return data;
        }),
      ]);
      const snap = snapRes.status === "fulfilled"
        ? (snapRes.value as Snapshot)
        : { ticker, benchmark, error: String(snapRes.reason) } as Snapshot;
      const reac = reacRes.status === "fulfilled"
        ? ((reacRes.value as ReactionResult[])[0] ?? { ticker, error: "Sem resultado" })
        : { ticker, error: String(reacRes.reason) } as ReactionResult;
      return { snap, reac };
    },
    onSuccess: ({ snap, reac }) => {
      setSnapshot(snap);
      setReaction(reac);
      setAnaliseIA(null);
    },
  });

  // A IA só transforma número em leitura — precisa de painel coletado antes,
  // e cada clique custa tokens (o custo volta na resposta e aparece na tela).
  const runIA = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/analise-rapida/ia", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          benchmark: benchmark.trim().toUpperCase() || "SMH",
          trend, technicals: tech, snapshot, reaction,
        }),
      });
      const data = await r.json();
      if (!r.ok || data.error) throw new Error(data.error || "Falha na análise com IA");
      return data as { markdown: string; usage?: { total_cost_usd?: number }; fontes?: string[]; truncado?: boolean };
    },
    onSuccess: setAnaliseIA,
  });

  const temDados = Boolean(trend || tech || snapshot || reaction);

  function montarRelatorio(): string | null {
    if (!temDados) return null;
    const blocos: string[] = [cabecalho(`Análise rápida — ${ticker}`, `Benchmark de setor: ${benchmark.toUpperCase()}`)];

    if (trend && !trend.error) {
      blocos.push("## Tendência\n\n" + itens([
        ["Tendência", `${trend.trend ?? "—"} (score ${trend.score ?? "—"})`],
        ["Cruzamento de médias", trend.components?.maCruzamento ?? "—"],
        ["Preço vs MM200", trend.components?.precoVsSma200 ?? "—"],
        ["Estrutura", trend.components?.estrutura ?? "—"],
        ["MACD", trend.components?.macd ?? "—"],
        ["RSI", trend.components?.rsi != null ? `${trend.components.rsi.toFixed(1)} (${trend.components?.rsiNota ?? "—"})` : "—"],
        ["Notícias", trend.news?.label ?? "—"],
        ["Confluência", trend.confluence ?? "—"],
        ["Sinal", `${trend.sinal ?? "—"} — ${trend.sinalMotivo ?? ""}`],
      ]));
    }

    if (tech && !tech.error) {
      blocos.push("## Técnica\n\n" + itens([
        ["Preço", `${fmtUsd(tech.price)} (${fmtPct(tech.changePct)} no dia)`],
        ["RSI", tech.rsi != null ? `${tech.rsi.toFixed(1)} (${tech.rsiSignal ?? "—"})` : "—"],
        ["MACD", `${tech.macdTrend ?? "—"} (hist ${tech.macdHistogram?.toFixed(3) ?? "—"})`],
        ["MM20 / MM50", `${fmtUsd(tech.sma20)} / ${fmtUsd(tech.sma50)}`],
        ["Distância da MM50", fmtPct(tech.pctAboveSma50)],
        ["VWAP", `${fmtUsd(tech.vwap)} (${tech.vwapSignal ?? "—"})`],
        ["RVOL", tech.rvol != null ? `${tech.rvol.toFixed(2)} (${tech.rvolSignal ?? "—"})` : "—"],
      ]));
    }

    if (snapshot && !snapshot.error) {
      blocos.push("## Níveis e cenário\n\n" + itens([
        ["Preço", fmtUsd(snapshot.price)],
        ["Faixa 52 semanas", `${fmtUsd(snapshot.yearLow)} – ${fmtUsd(snapshot.yearHigh)}`],
        ["MM50 / MM200", `${fmtUsd(snapshot.sma50)} / ${fmtUsd(snapshot.sma200)}`],
        ["Vol anual", snapshot.volAnnual != null ? `${(snapshot.volAnnual * 100).toFixed(1)}%` : "—"],
        ["Beta vs benchmark", snapshot.betaSector != null ? snapshot.betaSector.toFixed(2) : "—"],
        ["Momentum do setor", snapshot.sectorMomentum ? `${fmtPct(snapshot.sectorMomentum.momentumAnnualPct)} anualizado (${snapshot.sectorMomentum.lookbackDays}d, ${snapshot.sectorMomentum.benchmark})` : "—"],
      ]));
    }

    if (reaction?.summary) {
      const s = reaction.summary;
      blocos.push("## Reação a earnings\n\n" + itens([
        ["Eventos", s.n_events],
        ["Fechamento médio", pct(s.close_pct_mean)],
        ["Média absoluta", `${s.close_pct_abs_mean.toFixed(2)}%`],
        ["Threshold sugerido", `±${s.suggested_threshold_pct.toFixed(2)}%`],
        ["Resistências", `R1 $${s.r1_price.toFixed(2)} · R2 $${s.r2_price.toFixed(2)}`],
        ["Suportes", `S1 $${s.s1_price.toFixed(2)} · S2 $${s.s2_price.toFixed(2)}`],
        ["Run-up atual", s.runup?.runup_atual_pct != null ? `${pct(s.runup.runup_atual_pct)} (${s.runup.estado_atual ?? "—"})` : "—"],
      ]));
      if (reaction.events?.length) {
        blocos.push("### Eventos\n\n" + tabela(
          ["Data", "Run-up", "Gap dia", "Fech. dia", "Fech. D+1"],
          reaction.events.map((e) => [
            e.earnings_date,
            e.runup_pct != null ? pct(e.runup_pct) : "—",
            e.announcement_day ? pct(e.announcement_day.gap_pct) : "—",
            e.announcement_day ? pct(e.announcement_day.close_pct) : "—",
            e.next_day ? pct(e.next_day.close_pct) : "—",
          ]),
        ));
      }
    }

    if (analiseIA) {
      blocos.push("## Análise com IA\n\n" + analiseIA.markdown);
    }

    return blocos.join("\n\n");
  }

  const botao = "px-5 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2";
  const spinner = <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <ScanSearch className="h-7 w-7 text-primary" /> ANÁLISE RÁPIDA
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Tendência, técnica e níveis/reações de um ticker avulso — os mesmos cálculos do agente,
          sem precisar do papel na carteira. Não usa LLM.
        </p>
      </div>

      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Ticker</label>
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value)}
              placeholder="ex: INTC"
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Benchmark do setor (vol/beta)</label>
            <input
              type="text"
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value)}
              placeholder="SMH, KWEB, ITB..."
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          <button onClick={() => runTrend.mutate()} disabled={!ticker || runTrend.isPending} className={botao}>
            {runTrend.isPending ? spinner : <TrendingUp className="h-4 w-4" />} Tendência
          </button>
          <button onClick={() => runTech.mutate()} disabled={!ticker || runTech.isPending} className={botao}>
            {runTech.isPending ? spinner : <Activity className="h-4 w-4" />} Técnica
          </button>
          <button onClick={() => runNiveis.mutate()} disabled={!ticker || runNiveis.isPending} className={botao}>
            {runNiveis.isPending ? spinner : <Gauge className="h-4 w-4" />} Níveis &amp; Reações
          </button>
          <button
            onClick={() => runIA.mutate()}
            disabled={!ticker || !temDados || runIA.isPending}
            title={temDados ? "Gera uma leitura em texto dos painéis coletados — consome tokens (custo aparece no resultado)" : "Rode ao menos um painel primeiro"}
            className={botao}
          >
            {runIA.isPending ? spinner : <Sparkles className="h-4 w-4" />} Análise com IA
          </button>
        </div>
        {(runTrend.isError || runTech.isError || runNiveis.isError || runIA.isError) && (
          <p className="text-sm text-red-400 font-mono">
            {String(runTrend.error ?? runTech.error ?? runNiveis.error ?? runIA.error)}
          </p>
        )}
        {temDados && (
          <div className="border-t border-border/40 pt-4">
            <ExportarRelatorio
              titulo={`Análise rápida — ${ticker}`}
              mode="tela_analise_rapida"
              tickers={[ticker]}
              construir={montarRelatorio}
            />
          </div>
        )}
      </div>

      {analiseIA && (
        <Painel titulo={`Análise com IA — ${ticker}`}>
          <p className="font-mono text-[10px] text-muted-foreground/70">
            Leitura gerada sobre os painéis coletados
            {analiseIA.fontes?.length
              ? ` + camada fundamental: ${analiseIA.fontes.join(", ")}`
              : " (camada fundamental indisponível nesta análise)"}
            . Não é recomendação de compra ou venda.
            {analiseIA.usage?.total_cost_usd != null && ` · custo desta análise: ~$${analiseIA.usage.total_cost_usd.toFixed(4)}`}
          </p>
          {analiseIA.truncado && (
            <p className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400">
              ⚠ O texto bateu o limite de tamanho e terminou no meio — rode de novo para uma versão completa.
            </p>
          )}
          <MarkdownContent content={analiseIA.markdown} />
        </Painel>
      )}

      {trend && (
        <Painel titulo={`Tendência — ${trend.ticker}`}>
          {trend.error ? (
            <p className="font-mono text-sm text-muted-foreground">⚠ {trend.error}</p>
          ) : (
            <>
              <div className="flex items-baseline gap-4 flex-wrap">
                <span className={`text-2xl font-bold font-mono ${TOM_TENDENCIA[trend.trend ?? ""] ?? "text-foreground"}`}>
                  {(trend.trend ?? "—").toUpperCase()}
                </span>
                <span className="font-mono text-sm text-muted-foreground">score {trend.score ?? "—"}</span>
                {trend.price != null && <span className="font-mono text-sm text-muted-foreground">{fmtUsd(trend.price)}</span>}
                {trend.stale && (
                  <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded bg-yellow-500/10 border border-yellow-500/40 text-yellow-400">
                    dado atrasado
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <Metric label="Médias" value={trend.components?.maCruzamento ?? "—"} tone={trend.components?.maCruzamento === "alta" ? "pos" : trend.components?.maCruzamento === "baixa" ? "neg" : undefined} />
                <Metric label="vs MM200" value={trend.components?.precoVsSma200 ?? "—"} tone={trend.components?.precoVsSma200 === "acima" ? "pos" : trend.components?.precoVsSma200 === "abaixo" ? "neg" : undefined} />
                <Metric label="Estrutura" value={trend.components?.estrutura ?? "—"} tone={trend.components?.estrutura === "alta" ? "pos" : trend.components?.estrutura === "baixa" ? "neg" : undefined} />
                <Metric label="MACD" value={trend.components?.macd ?? "—"} tone={trend.components?.macd === "bullish" ? "pos" : trend.components?.macd === "bearish" ? "neg" : undefined} />
                <Metric label="RSI" value={trend.components?.rsi != null ? trend.components.rsi.toFixed(1) : "—"} sub={trend.components?.rsiNota} />
              </div>
              <div className="font-mono text-sm space-y-1">
                <p><span className="text-muted-foreground">Sinal:</span> <span className="text-foreground font-bold">{trend.sinal ?? "—"}</span> <span className="text-muted-foreground">— {trend.sinalMotivo ?? ""}</span></p>
                {trend.confluence && <p className="text-muted-foreground text-xs">{trend.confluence}</p>}
              </div>
              {trend.news?.destaques && trend.news.destaques.length > 0 && (
                <div>
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1.5">
                    Notícias ({trend.news.label ?? "—"})
                  </div>
                  <ul className="space-y-1">
                    {trend.news.destaques.map((d, i) => (
                      <li key={i} className="font-mono text-xs text-muted-foreground flex gap-2">
                        <span className={d.tone === "positivo" ? "text-green-400" : d.tone === "negativo" ? "text-red-400" : "text-muted-foreground"}>●</span>
                        <span>{d.title}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Painel>
      )}

      {tech && (
        <Painel titulo={`Técnica — ${tech.ticker}`}>
          {tech.error ? (
            <p className="font-mono text-sm text-muted-foreground">⚠ {tech.error}</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Metric label="Preço" value={fmtUsd(tech.price)} sub={`${fmtPct(tech.changePct)} no dia`} tone={(tech.changePct ?? 0) >= 0 ? "pos" : "neg"} />
              <Metric label="RSI" value={tech.rsi != null ? tech.rsi.toFixed(1) : "—"} sub={tech.rsiSignal} />
              <Metric label="MACD" value={tech.macdTrend ?? "—"} sub={tech.macdHistogram != null ? `hist ${tech.macdHistogram.toFixed(3)}` : undefined} tone={tech.macdTrend === "bullish" ? "pos" : tech.macdTrend === "bearish" ? "neg" : undefined} />
              <Metric label="RVOL" value={tech.rvol != null ? tech.rvol.toFixed(2) : "—"} sub={tech.rvolSignal} />
              <Metric label="MM20" value={fmtUsd(tech.sma20)} />
              <Metric label="MM50" value={fmtUsd(tech.sma50)} sub={tech.pctAboveSma50 != null ? `${fmtPct(tech.pctAboveSma50)} de distância` : undefined} tone={(tech.pctAboveSma50 ?? 0) >= 0 ? "pos" : "neg"} />
              <Metric label="VWAP" value={fmtUsd(tech.vwap)} sub={tech.vwapSignal} />
              <Metric label="Volume vs média" value={tech.volumeRatio != null ? `${tech.volumeRatio.toFixed(2)}x` : "—"} />
            </div>
          )}
        </Painel>
      )}

      {(snapshot || reaction) && (
        <Painel titulo={`Níveis & Reações — ${ticker}`}>
          {snapshot && (
            snapshot.error ? (
              <p className="font-mono text-sm text-muted-foreground">⚠ {snapshot.error}</p>
            ) : (
              <>
                {snapshot.quoteError && (
                  <p className="font-mono text-xs text-yellow-400">⚠ Cotação ao vivo indisponível: {snapshot.quoteError}</p>
                )}
                {snapshot.cenarioError && (
                  <p className="font-mono text-xs text-yellow-400">⚠ Vol/beta indisponíveis: {snapshot.cenarioError}</p>
                )}
                {snapshot.fontesDegradadas && (
                  <p className="font-mono text-xs text-yellow-400">
                    ⚠ Vol/beta calculados sobre dado degradado ({Object.values(snapshot.fontesDegradadas).join(", ")})
                  </p>
                )}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Metric label="Preço" value={fmtUsd(snapshot.price)} />
                  <Metric label="52 semanas" value={`${fmtUsd(snapshot.yearLow)} – ${fmtUsd(snapshot.yearHigh)}`} />
                  <Metric label="MM50 / MM200" value={`${fmtUsd(snapshot.sma50)} / ${fmtUsd(snapshot.sma200)}`} />
                  <Metric
                    label={`Vol / Beta (${snapshot.benchmark})`}
                    value={snapshot.volAnnual != null ? `${(snapshot.volAnnual * 100).toFixed(1)}%` : "—"}
                    sub={snapshot.betaSector != null ? `beta ${snapshot.betaSector.toFixed(2)}` : undefined}
                  />
                </div>
                {snapshot.sectorMomentum && (
                  <p className="font-mono text-xs text-muted-foreground">
                    Momentum do setor ({snapshot.sectorMomentum.benchmark}):{" "}
                    <span className={snapshot.sectorMomentum.momentumAnnualPct >= 0 ? "text-green-400" : "text-red-400"}>
                      {fmtPct(snapshot.sectorMomentum.momentumAnnualPct)}
                    </span>{" "}
                    anualizado ({snapshot.sectorMomentum.lookbackDays} dias)
                  </p>
                )}
              </>
            )
          )}

          {reaction && (
            reaction.error ? (
              <p className="font-mono text-sm text-muted-foreground">⚠ Reação a earnings: {reaction.error}</p>
            ) : reaction.summary ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Metric label="Eventos" value={String(reaction.summary.n_events)} sub={`threshold ±${reaction.summary.suggested_threshold_pct.toFixed(1)}%`} />
                  <Metric label="Fech. médio" value={fmtPct(reaction.summary.close_pct_mean)} sub={`|média| ${reaction.summary.close_pct_abs_mean.toFixed(2)}%`} tone={reaction.summary.close_pct_mean >= 0 ? "pos" : "neg"} />
                  <Metric label="R1 / R2" value={`${fmtUsd(reaction.summary.r1_price)} / ${fmtUsd(reaction.summary.r2_price)}`} tone="pos" />
                  <Metric label="S1 / S2" value={`${fmtUsd(reaction.summary.s1_price)} / ${fmtUsd(reaction.summary.s2_price)}`} tone="neg" />
                </div>
                {reaction.summary.runup?.estado_atual && (
                  <p className="font-mono text-xs text-muted-foreground">
                    Run-up atual: {fmtPct(reaction.summary.runup.runup_atual_pct)} →{" "}
                    <span className="text-foreground font-bold uppercase">{reaction.summary.runup.estado_atual}</span>
                    {reaction.summary.runup.corr_runup_reacao != null &&
                      ` · correlação run-up × reação ${reaction.summary.runup.corr_runup_reacao.toFixed(2)}`}
                  </p>
                )}
                {reaction.events && reaction.events.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full font-mono text-sm">
                      <thead className="bg-secondary/20">
                        <tr>
                          <th className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">Earnings</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">Run-up</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">Gap dia</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">Fech. dia</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">Fech. D+1</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reaction.events.map((e, idx) => (
                          <tr key={e.earnings_date} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                            <td className="px-3 py-2 text-muted-foreground">{e.earnings_date}</td>
                            <td className="px-3 py-2 text-right text-muted-foreground">{e.runup_pct != null ? fmtPct(e.runup_pct) : "—"}</td>
                            <td className={`px-3 py-2 text-right ${(e.announcement_day?.gap_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {e.announcement_day ? fmtPct(e.announcement_day.gap_pct) : "—"}
                            </td>
                            <td className={`px-3 py-2 text-right ${(e.announcement_day?.close_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {e.announcement_day ? fmtPct(e.announcement_day.close_pct) : "—"}
                            </td>
                            <td className={`px-3 py-2 text-right ${(e.next_day?.close_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {e.next_day ? fmtPct(e.next_day.close_pct) : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="font-mono text-[10px] text-muted-foreground/70">
                  Para a interpretação completa do padrão de reação (esticado/descontado, viés, BMO/AMC), use a tela Reação a Earnings.
                </p>
              </>
            ) : null
          )}
        </Painel>
      )}
    </div>
  );
}
