import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useSearch } from "wouter";
import { Activity, Gauge, ScanSearch, Sparkles, TrendingUp } from "lucide-react";
import { ExportarRelatorio, cabecalho, itens, tabela, pct } from "@/components/exportar-relatorio";
import { CamadaAusente, type AusenciaDeColeta } from "@/components/camada-ausente";
import { MarkdownContent } from "@/components/markdown";
import { benchmarkSugerido, temSugestaoConhecida } from "@/lib/benchmark-setor";
import { rotuloRvol } from "@/lib/indicators";

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
    // Presente só quando nível e direção das médias discordam (cruzamento
    // em reversão / enfraquecendo). Ver get_trend.classificar_cruzamento.
    maCruzamentoNota?: string;
    precoVsSma200?: string;
    estrutura?: string;
    macd?: string;
    rsi?: number;
    rsiNota?: string;
  };
  news?: {
    label?: string;
    score?: number;
    // Ver a nota em trend-card.tsx: manchete de outro papel descartada, e
    // manchete com ressalva, precisam APARECER. Foi contando notícia da AMD
    // como sentimento da ARM que esta tela recomendou "aguardar".
    positivas?: number;
    negativas?: number;
    ambiguas?: number;
    descartadas?: number;
    // Denominador do score. Sem ele o rótulo não distingue "2 a 1" de
    // "8 a 1" -- ver `minimoParaRotular` em get_trend.py.
    classificadas?: number;
    minimoParaRotular?: number;
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
  /**
   * De onde vieram as médias. "serie" é o normal -- mesmo `rolling(50)` do
   * painel Técnica, então os dois painéis batem. "yahoo" é o fallback quando
   * a série não veio: o campo pronto do Yahoo é caixa-preta e diverge ~0,8%,
   * e foi o que fez este painel discordar do Técnica três vezes.
   */
  smaOrigem?: "serie" | "yahoo" | "indisponivel";
  volAnnual?: number | null;
  betaSector?: number | null;
  daysUsed?: number | null;
  sectorMomentum?: { benchmark: string; momentumAnnualPct: number; lookbackDays: number } | null;
  fontesDegradadas?: Record<string, string>;
  quoteError?: string;
  cenarioError?: string;
  error?: string;
}

interface AnaliseIA {
  markdown: string;
  usage?: { total_cost_usd?: number };
  /** Blocos da camada fundamental que VIERAM. */
  fontes?: string[];
  /** Blocos que NÃO vieram, com o motivo e a função que os busca. */
  ausencias?: AusenciaDeColeta[];
  truncado?: boolean;
  avisos?: string[];
  /**
   * Os painéis que a análise REALMENTE leu, coletados no servidor.
   * A tela passa a mostrar estes -- ver o comentário em `runIA`.
   */
  paineis?: {
    trend?: TrendItem | null;
    technicals?: TechItem | null;
    snapshot?: Snapshot | null;
    reaction?: ReactionResult | null;
  };
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
  // Datas de balanço servidas de cache vencido (rede fora). Mesmo vocabulário
  // de degradação do painel Tendência — ver agent/earnings_dates.py.
  stale?: boolean;
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
      // O `n` da correlação. O backend calcula `corr_n`, `corr_ic95`,
      // `corr_p_valor` e `corr_nota` desde o incidente da correlação de 0,85;
      // a interface local declarava só o coeficiente, então a tela não tinha
      // como mostrar o denominador nem se quisesse. Contrato mais estreito
      // que o payload apaga informação em silêncio.
      corr_n?: number;
      esticado_n?: number;
      esticado_caiu_n?: number;
      esticado_reacao_media?: number | null;
      descontado_n?: number;
      descontado_subiu_n?: number;
      descontado_reacao_media?: number | null;
      // Ver earnings-reaction.tsx: a janela de run-up termina hoje, então
      // logo após um balanço ela inclui o próprio pregão de reação.
      janela_contem_earnings?: boolean;
      pregoes_desde_earnings?: number;
      runup_atual_ex_evento_pct?: number;
    };
  };
  // `janela_reacao` diz QUAL das duas sessões é a reação medida: "anuncio"
  // para quem divulga antes da abertura, "seguinte" para quem divulga depois
  // do fechamento. Toda a estatística (correlação, médias, bandas) usa essa
  // sessão -- e sem ela marcada na tabela o leitor toma "Fech. dia" por
  // reação e chega a números que contradizem o resumo logo acima.
  events?: {
    earnings_date: string;
    runup_pct?: number | null;
    janela_reacao?: "anuncio" | "seguinte";
    janela_inferida?: boolean;
    announcement_day: SessionMove | null;
    next_day: SessionMove | null;
  }[];
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
  const search = useSearch();
  const [tickerInput, setTickerInput] = useState("");
  const [benchmark, setBenchmark] = useState("SMH");
  // Enquanto o usuário não editar o benchmark, ele acompanha o ticker.
  // Depois de uma edição manual, para de mudar sozinho — comparar NVDA
  // contra XLK é uma escolha legítima, e a tela não pode desfazê-la a cada
  // letra digitada no campo do ticker.
  const [benchmarkManual, setBenchmarkManual] = useState(false);
  const [trend, setTrend] = useState<TrendItem | null>(null);
  const [tech, setTech] = useState<TechItem | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [reaction, setReaction] = useState<ReactionResult | null>(null);
  const [analiseIA, setAnaliseIA] = useState<AnaliseIA | null>(null);

  const ticker = tickerInput.trim().toUpperCase();

  // Prefill via ?ticker= -- é o que faz o "Investigar" do Painel de Cenários
  // ser um toque só em vez de "abre a tela, digita o papel de novo". Mesma
  // convenção de /grafico?ticker= e /alerts?symbol=.
  //
  // Guarda o último ticker aplicado num ref, e não "só preenche se o campo
  // estiver vazio": navegando de /cenarios pra cá duas vezes seguidas com
  // tickers diferentes o wouter não desmonta o componente, então a segunda
  // navegação seria ignorada. O ref também é o que impede a URL de
  // sobrescrever o que o usuário digitou por cima depois.
  const ultimoTickerDaUrlRef = useRef<string | null>(null);
  useEffect(() => {
    const daUrl = new URLSearchParams(search).get("ticker")?.trim().toUpperCase() || null;
    if (!daUrl || daUrl === ultimoTickerDaUrlRef.current) return;
    ultimoTickerDaUrlRef.current = daUrl;
    setTickerInput(daUrl);
    // O benchmark acompanha o ticker novo pela mesma regra do campo de
    // digitação: sugestão automática até o usuário assumir o controle.
    if (!benchmarkManual) setBenchmark(benchmarkSugerido(daUrl));
  }, [search, benchmarkManual]);

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

  // A IA só transforma número em leitura, e cada clique custa tokens (o custo
  // volta na resposta e aparece na tela).
  //
  // Não manda mais os painéis. Mandava o que estivesse no React Query naquele
  // clique, e cada painel tem seu próprio ciclo de refresh -- nada garantia
  // que os quatro fossem do mesmo momento. Em 29/08/2026 a Técnica do MRVL
  // saiu de uma sessão anterior à do resto, e a prosa descreveu duas sessões
  // como se fossem uma. Agora o servidor coleta os quatro no mesmo processo.
  const runIA = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/analise-rapida/ia", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          benchmark: benchmark.trim().toUpperCase() || "SMH",
        }),
      });
      const data = await r.json();
      if (!r.ok || data.error) throw new Error(data.error || "Falha na análise com IA");
      return data as AnaliseIA;
    },
    onSuccess: (data) => {
      setAnaliseIA(data);
      // Adota os painéis que a análise leu. Sem isto o conserto só mudaria de
      // lugar: o servidor analisaria dado fresco e a tela continuaria
      // mostrando o que tinha em mão, com a prosa citando números que o
      // usuário não vê em lugar nenhum.
      const p = data.paineis;
      if (!p) return;
      if (p.trend) setTrend(p.trend);
      if (p.technicals) setTech(p.technicals);
      if (p.snapshot) setSnapshot(p.snapshot);
      if (p.reaction) setReaction(p.reaction);
    },
  });

  const temDados = Boolean(trend || tech || snapshot || reaction);

  function montarRelatorio(): string | null {
    if (!temDados) return null;
    const blocos: string[] = [cabecalho(`Análise rápida — ${ticker}`, `Benchmark de setor: ${benchmark.toUpperCase()}`)];

    if (trend && !trend.error) {
      blocos.push("## Tendência\n\n" + itens([
        ["Tendência", `${trend.trend ?? "—"} (score ${trend.score ?? "—"})`],
        ["Cruzamento de médias", trend.components?.maCruzamento
          ? `${trend.components.maCruzamento}${trend.components.maCruzamentoNota ? ` — ${trend.components.maCruzamentoNota}` : ""}`
          : "—"],
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
        ["RVOL", tech.rvol != null ? `${tech.rvol.toFixed(2)} (${rotuloRvol(tech.rvolSignal)})` : "—"],
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
        // Bandas de volatilidade, não estrutura de preço — ver comentário
        // equivalente em earnings-reaction.tsx.
        ["Banda de reação · alta", `R1 $${s.r1_price.toFixed(2)} · R2 $${s.r2_price.toFixed(2)}`],
        ["Banda de reação · baixa", `S1 $${s.s1_price.toFixed(2)} · S2 $${s.s2_price.toFixed(2)}`],
        ["Run-up atual", s.runup?.runup_atual_pct != null ? `${pct(s.runup.runup_atual_pct)} (${s.runup.estado_atual ?? "—"})` : "—"],
        ...(s.runup?.janela_contem_earnings
          ? ([
              ["Balanço dentro da janela", `sim — há ${s.runup.pregoes_desde_earnings} pregão(ões), o run-up bruto inclui a reação`],
              ["Run-up ex-evento", s.runup.runup_atual_ex_evento_pct != null ? `${pct(s.runup.runup_atual_ex_evento_pct)} (é este que define o estado)` : "—"],
            ] as [string, string | number | null | undefined][])
          : []),
      ]) + "\n\n_R1/R2/S1/S2 projetam a volatilidade histórica de earnings sobre o preço atual — não são suporte/resistência técnico._");
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
              onChange={(e) => {
                const novo = e.target.value;
                setTickerInput(novo);
                if (!benchmarkManual) setBenchmark(benchmarkSugerido(novo));
              }}
              placeholder="ex: INTC"
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Benchmark do setor (vol/beta)</label>
            <input
              type="text"
              value={benchmark}
              onChange={(e) => { setBenchmark(e.target.value); setBenchmarkManual(true); }}
              placeholder="SMH, KWEB, ITB..."
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {!benchmarkManual && temSugestaoConhecida(ticker) ? (
              <span className="text-[10px] font-mono text-muted-foreground/70">
                sugerido para {ticker} · pode trocar
              </span>
            ) : benchmarkManual ? (
              <button
                type="button"
                onClick={() => { setBenchmarkManual(false); setBenchmark(benchmarkSugerido(ticker)); }}
                className="text-[10px] font-mono text-primary/80 hover:text-primary text-left"
              >
                voltar ao sugerido
              </button>
            ) : null}
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
          {/* O que NÃO veio fica logo abaixo do que veio: as duas metades da
              mesma frase. Ver `camada-ausente.tsx` sobre por que a omissão
              sozinha não bastava. */}
          {analiseIA.ausencias?.length ? <CamadaAusente ausencias={analiseIA.ausencias} /> : null}
          {analiseIA.truncado && (
            <p className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400">
              ⚠ O texto bateu o limite de tamanho e terminou no meio — rode de novo para uma versão completa.
            </p>
          )}
          {/* Apontamentos do agent/analise_rapida_validator.py. Ficam ACIMA do
              texto e nunca no lugar dele: análise suprimida deixaria a página
              vazia sem dizer por quê. */}
          {analiseIA.avisos && analiseIA.avisos.length > 0 && (
            <div className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400 space-y-1 mb-3">
              <p className="font-semibold">
                ⚠ O validador apontou {analiseIA.avisos.length} problema(s) nesta análise:
              </p>
              {analiseIA.avisos.map((a, i) => (
                <p key={i}>{a}</p>
              ))}
              <p className="opacity-80">
                A análise fica abaixo assim mesmo — leia com estes pontos em mente.
              </p>
            </div>
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
                <Metric label="Médias" value={trend.components?.maCruzamento ?? "—"} sub={trend.components?.maCruzamentoNota}
                  // Sem tom quando nível e direção discordam: pintar de vermelho um
                  // cruzamento que já está revertendo é o próprio erro que a nota corrige.
                  tone={trend.components?.maCruzamentoNota ? undefined
                    : trend.components?.maCruzamento === "alta" ? "pos"
                    : trend.components?.maCruzamento === "baixa" ? "neg" : undefined} />
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
                  {/* O PLACAR, não só o rótulo. MRVL (27/08/2026) mostrou
                      "Notícias (positivo)" com três das quatro manchetes
                      exibidas falando de queda: o rótulo vinha de 2 a 1, com
                      três mistas fora do denominador e as duas positivas nem
                      entre os destaques. O backend já devolvia essa contagem
                      "VISIVEL de proposito" (get_trend.py) e o trend-card já
                      a mostrava; esta tela declarava `ambiguas` no tipo e
                      nunca renderizava. Sem o placar, o leitor não tem como
                      auditar o rótulo contra as manchetes que ele vê. */}
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1.5">
                    Notícias ({trend.news.label ?? "—"}
                    {trend.news.positivas != null && trend.news.negativas != null
                      ? ` · ${trend.news.positivas}+/${trend.news.negativas}-${
                          trend.news.ambiguas ? `/${trend.news.ambiguas}~` : ""}`
                      : ""}
                    {trend.news.classificadas != null && trend.news.minimoParaRotular != null
                     && trend.news.classificadas < trend.news.minimoParaRotular
                      // Ver a nota longa em trend-card.tsx: "amostra N" foi
                      // lido como "quantas manchetes apareceram" por três
                      // leitores atentos no mesmo dia. N é o denominador do
                      // score (positivas + negativas), que exclui ambíguas.
                      // Ver trend-card.tsx: "N de M" foi mal lido quatro
                      // vezes porque "de" promete denominador, e M é piso.
                      ? ` · só ${trend.news.classificadas} com tom definido, mínimo ${trend.news.minimoParaRotular} para rotular`
                      : ""})
                  </div>
                  <ul className="space-y-1">
                    {trend.news.destaques.map((d, i) => (
                      <li key={i} className="font-mono text-xs text-muted-foreground flex gap-2">
                        <span className={d.tone === "positivo" ? "text-green-400" : d.tone === "negativo" ? "text-red-400" : "text-amber-400"}>●</span>
                        <span>{d.title}</span>
                      </li>
                    ))}
                  </ul>
                  {!!trend.news.descartadas && (
                    <p className="text-[10px] font-mono text-muted-foreground/70 mt-1.5">
                      {trend.news.descartadas === 1
                        ? "1 manchete era de outro papel e ficou fora da conta"
                        : `${trend.news.descartadas} manchetes eram de outros papéis e ficaram fora da conta`}
                    </p>
                  )}
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
              <Metric label="RVOL" value={tech.rvol != null ? tech.rvol.toFixed(2) : "—"} sub={rotuloRvol(tech.rvolSignal)} />
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
                  <Metric
                    label="MM50 / MM200"
                    value={`${fmtUsd(snapshot.sma50)} / ${fmtUsd(snapshot.sma200)}`}
                    sub={snapshot.smaOrigem === "yahoo" ? "média do Yahoo (a série não veio)" : undefined}
                  />
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
                {/* Datas de balanço de cópia vencida: painel completo e sem
                    aviso não se distingue de um calculado sobre agenda atual. */}
                {reaction.stale && (
                  <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded bg-yellow-500/10 border border-yellow-500/40 text-yellow-400">
                    datas de balanço de cache — rede instável
                  </span>
                )}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Metric label="Eventos" value={String(reaction.summary.n_events)} sub={`threshold ±${reaction.summary.suggested_threshold_pct.toFixed(1)}%`} />
                  <Metric label="Fech. médio" value={fmtPct(reaction.summary.close_pct_mean)} sub={`|média| ${reaction.summary.close_pct_abs_mean.toFixed(2)}%`} tone={reaction.summary.close_pct_mean >= 0 ? "pos" : "neg"} />
                  <Metric label="R1 / R2" value={`${fmtUsd(reaction.summary.r1_price)} / ${fmtUsd(reaction.summary.r2_price)}`} sub="banda de reação" tone="pos" />
                  <Metric label="S1 / S2" value={`${fmtUsd(reaction.summary.s1_price)} / ${fmtUsd(reaction.summary.s2_price)}`} sub="banda de reação" tone="neg" />
                </div>
                {reaction.summary.runup?.estado_atual && (
                  <p className="font-mono text-xs text-muted-foreground">
                    Run-up atual: {fmtPct(reaction.summary.runup.runup_atual_pct)} →{" "}
                    <span className="text-foreground font-bold uppercase">{reaction.summary.runup.estado_atual}</span>
                    {reaction.summary.runup.corr_runup_reacao != null &&
                      ` · correlação run-up × reação ${reaction.summary.runup.corr_runup_reacao.toFixed(2)}` +
                      // O `n` ao lado, sempre. O payload calcula `corr_n`,
                      // `corr_ic95` e `corr_p_valor`; a tela mostrava só o
                      // coeficiente, e correlação sem o número de pares é
                      // indistinguível de uma robusta. No MRVL saiu "0.45"
                      // sobre SEIS pares -- dois dos oito eventos não têm par
                      // completo (o mais antigo não tem run-up, o mais recente
                      // ainda não tem reação).
                      (reaction.summary.runup.corr_n != null
                        ? ` · n=${reaction.summary.runup.corr_n}`
                        : "")}
                    {reaction.summary.runup.janela_contem_earnings && (
                      <span className="block text-amber-400/80">
                        ⚠ balanço há {reaction.summary.runup.pregoes_desde_earnings} pregão(ões), dentro da janela — o run-up bruto inclui a reação
                        {reaction.summary.runup.runup_atual_ex_evento_pct != null &&
                          ` · ex-evento ${fmtPct(reaction.summary.runup.runup_atual_ex_evento_pct)}`}
                      </span>
                    )}
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
                        {reaction.events.map((e, idx) => {
                          // Qual das duas colunas de fechamento É a reação
                          // medida. Sem isso a tabela convida ao erro: quem
                          // divulga depois do fechamento (AMC) reage no dia
                          // SEGUINTE, e ler "Fech. dia" como reação produz
                          // números que contradizem a correlação logo acima.
                          const reageNoSeguinte = e.janela_reacao === "seguinte";
                          const reageNoAnuncio = e.janela_reacao === "anuncio";
                          const marca = (ehAReacao: boolean) =>
                            ehAReacao ? " bg-primary/10 font-bold" : "";
                          const titulo = e.janela_inferida
                            ? "sessão da reação (AMC suposto — a fonte não trouxe horário)"
                            : "sessão da reação, medida pelo horário de divulgação";
                          return (
                          <tr key={e.earnings_date} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                            <td className="px-3 py-2 text-muted-foreground">{e.earnings_date}</td>
                            <td className="px-3 py-2 text-right text-muted-foreground">{e.runup_pct != null ? fmtPct(e.runup_pct) : "—"}</td>
                            <td className={`px-3 py-2 text-right ${(e.announcement_day?.gap_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {e.announcement_day ? fmtPct(e.announcement_day.gap_pct) : "—"}
                            </td>
                            <td
                              className={`px-3 py-2 text-right ${(e.announcement_day?.close_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}${marca(reageNoAnuncio)}`}
                              title={reageNoAnuncio ? titulo : undefined}
                            >
                              {e.announcement_day ? fmtPct(e.announcement_day.close_pct) : "—"}
                              {reageNoAnuncio && <span className="text-muted-foreground"> ◂</span>}
                            </td>
                            <td
                              className={`px-3 py-2 text-right ${(e.next_day?.close_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}${marca(reageNoSeguinte)}`}
                              title={reageNoSeguinte ? titulo : undefined}
                            >
                              {e.next_day ? fmtPct(e.next_day.close_pct) : "—"}
                              {reageNoSeguinte && <span className="text-muted-foreground"> ◂</span>}
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="font-mono text-[10px] text-muted-foreground/70">
                  <span className="bg-primary/10 font-bold px-1">◂ marca a sessão da reação</span> — é ela que entra na
                  correlação, nas médias e nas bandas. Quem divulga depois do fechamento reage no dia seguinte,
                  então "Fech. dia" nem sempre é a reação.
                </p>
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
