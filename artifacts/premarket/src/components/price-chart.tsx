import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  ComposedChart,
  Line,
  Bar,
  ReferenceLine,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { useGetTickerChart, getGetTickerChartQueryKey } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import { CandleChart } from "@/components/candle-chart";
import { sessionGradientStops, hasExtendedSession, filterCandlesBySession, SESSION_COLORS } from "@/components/session-gradient";
import { useDraggableOffset } from "@/hooks/use-draggable-offset";
import { IndicatorToggles } from "@/components/indicator-toggles";
import {
  attachIndicatorFields, INDICATOR_COLORS, type IndicatorKey,
  computeVwapSeries, computeCumulativeVolume, computeExpectedVolumePace,
} from "@/lib/indicators";
import { cn } from "@/lib/utils";
import { TradingViewChart } from "@/components/tradingview-chart";
import { useTrend } from "@/components/trend-card";
import { GripVertical } from "lucide-react";

// ─── Gráfico de preço com indicadores técnicos ──────────────────────────────
// Componente compartilhado entre Dashboard (embutido) e a tela Gráfico
// (como alternativa ao widget da TradingView) -- extraído em vez de duplicado
// pra garantir que os dois mostrem exatamente os mesmos números.

export function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtVol(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toString();
}

// Fonte/cor dos eixos do gráfico de preço + painéis auxiliares -- maior e
// mais clara que o texto secundário padrão, pra ficar legível em cima do fundo escuro.
const AXIS_TICK = { fontSize: 14, fontFamily: "monospace", fill: "#d4d4d8" };
const CROSSHAIR_STROKE = "#a1a1aa";

function fmtLabel(ts: number, period: string) {
  const d = new Date(ts);
  if (period === "1d") return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (period === "5d") return d.toLocaleDateString("en-US", { weekday: "short", hour: "2-digit", hour12: false });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export const PERIODS = [
  { key: "1d", label: "1D" },
  { key: "5d", label: "5D" },
  { key: "1mo", label: "1M" },
  { key: "3mo", label: "3M" },
  { key: "6mo", label: "6M" },
  { key: "1y", label: "1Y" },
];

// Formato de cada ponto sob o cursor -- preço + indicadores anexados (ver
// attachIndicatorFields).
interface HoverRow {
  t?: number; label?: string; price?: number; vol?: number;
  sma21?: number | null; sma50?: number | null;
  bbUpper?: number | null; bbLower?: number | null;
  rsi?: number | null; macdLine?: number | null; macdSignal?: number | null;
  vwap?: number | null; cumVol?: number | null; expectedVol?: number | null;
}

export function PriceChart({ symbol, period, height = 200 }: { symbol: string; period: string; height?: number }) {
  const [, navigate] = useLocation();
  const [mode, setMode] = useState<"line" | "candle" | "tradingview">("line");
  // Mostra/esconde as barras de pré e pós-mercado no gráfico (preço,
  // indicadores, VWAP/RVOL) -- ligados por padrão. É preferência de exibição,
  // não reseta ao trocar de ticker/período (mesmo critério de `mode`/`indicators` abaixo).
  const [showPre, setShowPre] = useState(true);
  const [showPost, setShowPost] = useState(true);
  // Indicadores técnicos -- todos desligados por padrão. No modo candle (SVG
  // puro, sem recharts) só os painéis auxiliares (Volume/RSI/MACD/RVOL)
  // valem; overlay (SMA/Bollinger/VWAP) precisaria desenhar dentro do
  // CandleChart, que não tem esse suporte ainda -- IndicatorToggles já
  // restringe as opções mostradas nesse modo via `available`.
  const [indicators, setIndicators] = useState<Set<IndicatorKey>>(new Set());
  const toggleIndicator = useCallback((key: IndicatorKey) => {
    setIndicators((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  // Menu de botão direito ("criar alerta neste preço") -- só existe no
  // gráfico próprio (line/candle), não dá pra interceptar clique dentro do
  // iframe da TradingView. No modo line, o preço vem do hit-test que o
  // próprio recharts já resolve via onMouseMove (mesmo do tooltip); no modo
  // candle, o CandleChart (SVG puro) converte a posição do clique direto.
  const [chartMenu, setChartMenu] = useState<{ x: number; y: number; price: number } | null>(null);
  const hoverPriceRef = useRef<number | null>(null);
  // Container do gráfico (a div "relative" que envolve o SVG/recharts) --
  // grampeia o arraste da caixa de dados pra ela nunca sair da coluna do
  // gráfico (ver useDraggableOffset).
  const chartContainerRef = useRef<HTMLDivElement>(null);
  // Caixa de dados arrastável -- o usuário move pra onde quiser (a posição
  // padrão no canto às vezes fica em cima das próprias linhas do gráfico).
  const { offset: boxOffset, dragging: boxDragging, onMouseDown: onBoxMouseDown, onTouchStart: onBoxTouchStart, boxRef: hoverBoxRef } = useDraggableOffset("premercado:chart-hover-box-pos", chartContainerRef);
  // Crosshair (linha horizontal) + caixa de dados fixa no canto do gráfico
  // (em vez do tooltip flutuante do recharts, que seguia o cursor e tapava
  // as linhas) -- guarda a linha inteira sob o cursor, sincronizado com os
  // painéis auxiliares abaixo via syncId.
  const [hoverRow, setHoverRow] = useState<HoverRow | null>(null);
  // Como a caixa não limpa mais sozinha no mouseleave (ver comentário
  // abaixo), limpa manualmente ao trocar de ticker/período/modo pra não
  // deixar o dado de um gráfico antigo parado na tela.
  useEffect(() => setHoverRow(null), [symbol, period, mode]);
  const hoverY = hoverRow?.price ?? null;
  const openChartMenu = useCallback((price: number, clientX: number, clientY: number) => {
    const x = Math.min(clientX, window.innerWidth - 230);
    const y = Math.min(clientY, window.innerHeight - 120);
    setChartMenu({ x, y, price });
  }, []);
  const handleChartMouseMove = useCallback((state: { activePayload?: { payload?: HoverRow }[] }) => {
    const p = state?.activePayload?.[0]?.payload;
    if (!p) return;
    if (p.price != null) hoverPriceRef.current = p.price;
    setHoverRow(p);
  }, []);
  // A caixa NÃO some mais quando o mouse sai do gráfico -- ela fica parada
  // mostrando o último ponto visto até o próximo hover atualizar. Antes ela
  // sumia no mouseleave, o que quebrava o arrastar: quando o usuário movia o
  // mouse do gráfico até a caixa (já arrastada pra longe), o cursor cruzava
  // um espaço "morto" (nem gráfico, nem caixa) no meio do caminho, disparando
  // o mouseleave e apagando a caixa antes do cursor chegar nela.
  const handleChartMouseLeave = useCallback(() => {}, []);
  const handleChartContextMenu = useCallback((_state: unknown, e: React.MouseEvent) => {
    if (hoverPriceRef.current == null) return;
    e.preventDefault();
    openChartMenu(hoverPriceRef.current, e.clientX, e.clientY);
  }, [openChartMenu]);
  useEffect(() => {
    if (!chartMenu) return;
    const close = () => setChartMenu(null);
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [chartMenu]);
  const chartMenuEl = chartMenu && (
    <div
      className="fixed z-[60] min-w-[220px] rounded-md border border-border bg-card shadow-lg py-1 font-mono text-xs"
      style={{ left: chartMenu.x, top: chartMenu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="px-3 py-1.5 text-muted-foreground border-b border-border/50">
        {symbol} · ${chartMenu.price.toFixed(2)}
      </div>
      <button
        type="button"
        className="w-full text-left px-3 py-1.5 hover:bg-secondary transition-colors flex items-center gap-1.5"
        onClick={() => {
          navigate(`/alerts?symbol=${symbol}&price=${chartMenu.price.toFixed(2)}&condition=above`);
          setChartMenu(null);
        }}
      >
        🔔 Alerta se subir acima de <span className="text-green-400 font-bold">${chartMenu.price.toFixed(2)}</span>
      </button>
      <button
        type="button"
        className="w-full text-left px-3 py-1.5 hover:bg-secondary transition-colors flex items-center gap-1.5"
        onClick={() => {
          navigate(`/alerts?symbol=${symbol}&price=${chartMenu.price.toFixed(2)}&condition=below`);
          setChartMenu(null);
        }}
      >
        🔔 Alerta se cair abaixo de <span className="text-red-400 font-bold">${chartMenu.price.toFixed(2)}</span>
      </button>
    </div>
  );
  const { data: trendData } = useTrend(symbol);
  const { data, isLoading } = useGetTickerChart(
    { symbol, period },
    {
      query: {
        queryKey: getGetTickerChartQueryKey({ symbol, period }),
        staleTime: 55_000,
        // Só faz sentido reconsultar automaticamente no intraday (1D) — o
        // backend também cacheia 5D+ por vários minutos/hora (chart.ts TTL),
        // então repolling mais frequente nesses períodos não traria dado novo.
        refetchInterval: period === "1d" ? 60_000 : false,
      },
    },
  );

  // Barras de pré/pós-mercado ligadas por padrão -- o toggle abaixo deixa
  // tirar uma ou as duas do gráfico (preço, indicadores e VWAP/RVOL, que já
  // ignoram pré/pós por conta própria) sem precisar de outro fetch: filtra
  // localmente sobre o candle já baixado. hasExtendedSession roda sobre o
  // conjunto ORIGINAL (rawCandles), não o filtrado -- senão desligar "pré"
  // faria o próprio checkbox de "pré" sumir (nada mais pra detectar a sessão).
  const rawCandles = data?.candles ?? [];
  const showSessionColors = hasExtendedSession(rawCandles);
  const candles = filterCandlesBySession(rawCandles, showPre, showPost);
  const closes = candles.map((c) => c.c);
  const chartData = candles.map((c) => ({ t: c.t, price: c.c, vol: c.v, label: fmtLabel(c.t, period), session: c.session }));

  const prices = chartData.map((d) => d.price).filter(Boolean) as number[];
  const minP = prices.length ? Math.min(...prices) : 0;
  const maxP = prices.length ? Math.max(...prices) : 0;
  const pad = (maxP - minP) * 0.05 || 1;

  const first = prices[0];
  const last = prices[prices.length - 1];
  const up = last != null && first != null && last >= first;
  const color = up ? "#22c55e" : "#ef4444";
  const sessionGradientId = `session-grad-${symbol}`;

  // VWAP + RVOL são intradiários (resetam a cada pregão) -- só fazem sentido
  // em period="1d", tanto pelo overlay de preço (VWAP) quanto pelo painel
  // auxiliar (RVOL: volume acumulado vs. o "esperado" pra essa hora do
  // pregão, ver lib/indicators.ts). Fora do 1d, ficam fora da lista de
  // indicadores disponíveis (ver `availableIndicators` abaixo).
  const isIntraday = period === "1d";
  const vwapSeries = isIntraday ? computeVwapSeries(candles) : [];
  const cumVolSeries = isIntraday ? computeCumulativeVolume(candles) : [];

  // volAvg20 (média de volume diário dos últimos 20 dias) só existe no
  // /api/technicals (mesmo dado mostrado em Técnicos/Plano de Saída/Veredito)
  // -- busca sob demanda, só quando o painel de RVOL está ligado, pra não
  // gastar uma chamada extra à toa em quem nunca abre esse painel.
  const showRvolPanel = isIntraday && indicators.has("rvol");
  const { data: technicalsData } = useQuery({
    queryKey: ["price-chart-vol-avg20", symbol],
    queryFn: async () => {
      const r = await fetch(`/api/technicals?tickers=${encodeURIComponent(symbol)}`, { credentials: "include" });
      if (!r.ok) throw new Error("Falha ao buscar volAvg20");
      return (await r.json()) as { items: { ticker: string; volAvg20?: number | null }[] };
    },
    enabled: showRvolPanel,
    staleTime: 5 * 60_000,
  });
  const volAvg20 = technicalsData?.items.find((i) => i.ticker === symbol)?.volAvg20 ?? null;
  const expectedVolSeries = showRvolPanel ? computeExpectedVolumePace(candles, volAvg20) : [];

  // Indicadores técnicos: anexa as séries por índice e expande o domínio do
  // eixo Y do painel de preço se Bollinger/SMA/VWAP passarem do range de fechamentos.
  const chartDataInd = attachIndicatorFields(chartData, closes).map((row, i) => ({
    ...row,
    vwap: vwapSeries[i] ?? null,
    cumVol: cumVolSeries[i] ?? null,
    expectedVol: expectedVolSeries[i] ?? null,
  }));
  // Sincroniza o crosshair (linha vertical) entre o painel de preço e os
  // painéis auxiliares -- recharts casa por índice quando compartilham o
  // mesmo syncId. Só vale no modo line (candle usa o CandleChart, sem recharts).
  const priceChartSyncId = `price-${symbol}`;
  const showVolume = indicators.has("volume");
  const showRsi = indicators.has("rsi");
  const showMacd = indicators.has("macd");
  const showSma21 = mode === "line" && indicators.has("sma21");
  const showSma50 = mode === "line" && indicators.has("sma50");
  const showBollinger = mode === "line" && indicators.has("bollinger");
  const showVwap = mode === "line" && isIntraday && indicators.has("vwap");
  const overlayValues: number[] = [];
  for (const r of chartDataInd) {
    if (showBollinger) {
      if (r.bbUpper != null) overlayValues.push(r.bbUpper);
      if (r.bbLower != null) overlayValues.push(r.bbLower);
    }
    if (showSma50 && r.sma50 != null) overlayValues.push(r.sma50);
    if (showSma21 && r.sma21 != null) overlayValues.push(r.sma21);
    if (showVwap && r.vwap != null) overlayValues.push(r.vwap);
  }
  const areaDomain: [number, number] = overlayValues.length
    ? [Math.min(minP - pad, ...overlayValues), Math.max(maxP + pad, ...overlayValues)]
    : [minP - pad, maxP + pad];
  const subpanelHeight = 100;
  const lastSubpanel = showMacd ? "macd" : showRsi ? "rsi" : showRvolPanel ? "rvol" : showVolume ? "volume" : null;
  // Indicadores disponíveis no seletor -- VWAP/RVOL só aparecem em gráficos
  // intradiários (period="1d"); overlay (SMA/Bollinger/VWAP) só no modo line.
  const availableIndicators: IndicatorKey[] | undefined = mode === "candle"
    ? (isIntraday ? ["volume", "rsi", "macd", "rvol"] : ["volume", "rsi", "macd"])
    : (isIntraday ? undefined : ["sma21", "sma50", "bollinger", "volume", "rsi", "macd"]);
  // Tooltip com layout próprio (em vez de contentStyle/labelStyle/itemStyle)
  // pra destacar o ticker bem maior/mais forte que o preço -- esses três
  // props do recharts aplicam um único estilo pra tudo.
  // Linhas extras pros indicadores atualmente ligados -- só mostra o que
  // estiver visível no gráfico agora (mesmo critério do overlay/painéis).
  const renderIndicatorRows = (p: HoverRow) => {
    const rows: { label: string; value: string; color: string }[] = [];
    if (showSma21 && p.sma21 != null) rows.push({ label: "SMA21", value: `$${p.sma21.toFixed(2)}`, color: INDICATOR_COLORS.sma21 });
    if (showSma50 && p.sma50 != null) rows.push({ label: "SMA50", value: `$${p.sma50.toFixed(2)}`, color: INDICATOR_COLORS.sma50 });
    if (showBollinger && p.bbUpper != null) rows.push({ label: "BB Sup", value: `$${p.bbUpper.toFixed(2)}`, color: INDICATOR_COLORS.bollinger });
    if (showBollinger && p.bbLower != null) rows.push({ label: "BB Inf", value: `$${p.bbLower.toFixed(2)}`, color: INDICATOR_COLORS.bollinger });
    if (showVwap && p.vwap != null) rows.push({ label: "VWAP", value: `$${p.vwap.toFixed(2)}`, color: INDICATOR_COLORS.vwap });
    if (showVolume && p.vol != null) rows.push({ label: "Volume", value: fmtVol(p.vol), color: "#a1a1aa" });
    if (showRsi && p.rsi != null) rows.push({ label: "IFR", value: p.rsi.toFixed(1), color: "#facc15" });
    if (showMacd && p.macdLine != null) rows.push({ label: "MACD", value: p.macdLine.toFixed(3), color: INDICATOR_COLORS.macdLine });
    if (showMacd && p.macdSignal != null) rows.push({ label: "Sinal", value: p.macdSignal.toFixed(3), color: INDICATOR_COLORS.macdSignal });
    if (showRvolPanel && p.cumVol != null && p.expectedVol != null && p.expectedVol > 0) {
      rows.push({ label: "RVOL", value: `${(p.cumVol / p.expectedVol).toFixed(2)}x`, color: "#a1a1aa" });
    }
    if (!rows.length) return null;
    return (
      <div className="mt-1.5 pt-1.5 border-t border-[#27272a] space-y-0.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between gap-4 text-sm">
            <span className="flex items-center gap-1.5 text-[#a1a1aa]">
              <span className="inline-block h-2 w-2 rounded-full flex-shrink-0" style={{ background: r.color }} />
              {r.label}
            </span>
            <span className="font-semibold text-[#e4e4e7]">{r.value}</span>
          </div>
        ))}
      </div>
    );
  };
  // Caixa de dados fixa no canto do gráfico (em vez do tooltip flutuante do
  // recharts, que seguia o cursor e tapava as linhas) -- conteúdo lido
  // direto do state `hoverRow`, atualizado a cada onMouseMove. A barra do
  // topo (não só um iconezinho no canto) é a área de arrastar -- inteira
  // dentro dos limites da própria caixa, então não tem como ficar coberta/
  // fora de alcance por causa de outro elemento por perto.
  const hoverBoxContent = () => {
    if (!hoverRow || hoverRow.price == null) return null;
    return (
      <div className="rounded-md border font-mono overflow-hidden" style={{ background: "#09090b", borderColor: "#27272a" }}>
        <div
          onMouseDown={onBoxMouseDown}
          onTouchStart={onBoxTouchStart}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 border-b select-none pointer-events-auto touch-none",
            boxDragging ? "cursor-grabbing" : "cursor-grab",
          )}
          style={{ borderColor: "#27272a" }}
          title="Arrastar caixa"
        >
          <GripVertical className="h-3.5 w-3.5 text-[#71717a] flex-shrink-0" />
          <span className="text-sm text-[#a1a1aa]">{hoverRow.label}</span>
        </div>
        <div className="px-3 py-2">
          <div className="flex items-baseline gap-3">
            <span className="text-2xl font-extrabold text-primary leading-none">{symbol}</span>
            <span className="text-xl font-bold text-[#e4e4e7]">${fmt(hoverRow.price)}</span>
          </div>
          {renderIndicatorRows(hoverRow)}
        </div>
      </div>
    );
  };
  const indicatorToggleEl = (
    <IndicatorToggles
      enabled={indicators}
      onToggle={toggleIndicator}
      available={availableIndicators}
    />
  );

  // Painéis auxiliares (Volume/RSI/MACD/RVOL) valem tanto no modo line quanto
  // candle -- só o overlay (SMA/Bollinger/VWAP) é exclusivo do line (ver acima).
  const subpanelsEl = (
    <>
      {showVolume && (
        <div className="mt-3">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">Volume</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={chartDataInd} margin={{ top: 0, right: 8, bottom: 2, left: 0 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="label"
                tick={lastSubpanel === "volume" ? AXIS_TICK : false}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis
                domain={[0, "dataMax"]}
                tick={AXIS_TICK}
                tickFormatter={(v: number) => fmtVol(v)}
                width={60}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <Bar dataKey="vol" fill="#a1a1aa" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {showRsi && (
        <div className="mt-3">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">IFR (RSI 14)</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={chartDataInd} margin={{ top: 2, right: 8, bottom: 2, left: 0 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="label"
                tick={lastSubpanel === "rsi" ? AXIS_TICK : false}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis
                domain={[0, 100]}
                ticks={[30, 70]}
                tick={AXIS_TICK}
                width={60}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <ReferenceLine y={70} stroke="#f87171" strokeDasharray="3 3" />
              <ReferenceLine y={30} stroke="#4ade80" strokeDasharray="3 3" />
              <Line dataKey="rsi" stroke="#facc15" dot={false} strokeWidth={2.7} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {showMacd && (
        <div className="mt-3">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">MACD (12,26,9)</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={chartDataInd} margin={{ top: 2, right: 8, bottom: 2, left: 0 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="label"
                tick={lastSubpanel === "macd" ? AXIS_TICK : false}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis tick={AXIS_TICK} width={60} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <ReferenceLine y={0} stroke="#3f3f46" />
              <Bar dataKey="macdHistPos" fill="#4ade80" isAnimationActive={false} />
              <Bar dataKey="macdHistNeg" fill="#f87171" isAnimationActive={false} />
              <Line dataKey="macdLine" stroke={INDICATOR_COLORS.macdLine} dot={false} strokeWidth={2.7} isAnimationActive={false} connectNulls />
              <Line dataKey="macdSignal" stroke={INDICATOR_COLORS.macdSignal} dot={false} strokeWidth={2.7} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {showRvolPanel && (
        <div className="mt-3">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">
            RVOL · volume acumulado vs. esperado {volAvg20 == null && "(carregando média de 20d...)"}
          </div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={chartDataInd} margin={{ top: 2, right: 8, bottom: 2, left: 0 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="label"
                tick={lastSubpanel === "rvol" ? AXIS_TICK : false}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis
                domain={[0, "dataMax"]}
                tick={AXIS_TICK}
                tickFormatter={(v: number) => fmtVol(v)}
                width={60}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <Line dataKey="expectedVol" stroke="#71717a" strokeDasharray="4 3" dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
              <Line dataKey="cumVol" stroke="#a1a1aa" dot={false} strokeWidth={2} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </>
  );

  const toggle = (
    <div className="flex justify-end items-center gap-1 mb-1">
      {([["line", "Linha"], ["candle", "Velas"], ["tradingview", "TradingView"]] as const).map(([key, label]) => (
        <button
          key={key}
          onClick={() => setMode(key)}
          className={`px-2 py-0.5 rounded text-[11px] font-mono border transition-colors ${
            mode === key
              ? "bg-primary text-primary-foreground border-primary"
              : "text-muted-foreground border-border hover:text-foreground"
          }`}
        >
          {label}
        </button>
      ))}
      {mode !== "tradingview" && indicatorToggleEl}
    </div>
  );

  // Modo TradingView busca os próprios dados no iframe deles -- não depende
  // do carregamento/disponibilidade do nosso /api/ticker-chart. Usa uma altura
  // bem maior que os outros modos, já que o widget tem toolbar própria e fica
  // apertado em alturas pequenas. `height` só passa de 300 quando o gráfico
  // está expandido (tela cheia) -- nesse caso usa a altura real da viewport
  // em vez de forçar um valor fixo.
  if (mode === "tradingview") {
    const tvHeight = height > 300 ? height : 480;
    return (
      <div>
        {toggle}
        <TradingViewChart symbol={symbol} height={tvHeight} />
      </div>
    );
  }

  if (isLoading) {
    return (
      <div>
        {toggle}
        <div className="flex items-center justify-center h-48">
          <span className="text-xs font-mono text-muted-foreground animate-pulse">Carregando gráfico...</span>
        </div>
      </div>
    );
  }

  if (!chartData.length) {
    return (
      <div>
        {toggle}
        <div className="flex items-center justify-center h-48">
          <span className="text-xs font-mono text-muted-foreground">Sem dados para este período.</span>
        </div>
      </div>
    );
  }

  // Checkbox de pré/pós-mercado -- marca/desmarca pra tirar/colocar essas
  // barras do gráfico (preço, indicadores, VWAP/RVOL). Só aparece quando o
  // período baixado realmente tem candle de fora do pregão regular
  // (hasExtendedSession sobre rawCandles, não sobre o já filtrado).
  const sessionToggleEl = showSessionColors && (
    <div className="flex items-center justify-end gap-3 mb-1 text-[9px] font-mono text-muted-foreground">
      <label className="flex items-center gap-1 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={showPre}
          onChange={(e) => setShowPre(e.target.checked)}
          className="h-2.5 w-2.5"
        />
        <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.pre }} /> pré
      </label>
      <label className="flex items-center gap-1 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={showPost}
          onChange={(e) => setShowPost(e.target.checked)}
          className="h-2.5 w-2.5"
        />
        <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.post }} /> pós
      </label>
    </div>
  );

  if (mode === "candle") {
    return (
      <div>
        {toggle}
        {sessionToggleEl}
        <CandleChart
          candles={candles}
          height={height}
          labelFor={(ts) => fmtLabel(ts, period)}
          markers={trendData?.news?.destaques}
          onPriceContextMenu={openChartMenu}
        />
        {chartMenuEl}
        {subpanelsEl}
      </div>
    );
  }

  return (
    <div>
    {toggle}
    {chartMenuEl}
    {sessionToggleEl}
    <div className="relative" ref={chartContainerRef}>
      {hoverRow && (
        <div
          ref={hoverBoxRef}
          className="absolute z-10 max-w-[220px] pointer-events-none"
          style={{ top: 4 + boxOffset.y, right: Math.max(4, 4 - boxOffset.x) }}
        >
          {hoverBoxContent()}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={chartDataInd}
        margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
        onMouseMove={handleChartMouseMove}
        onMouseLeave={handleChartMouseLeave}
        onContextMenu={handleChartContextMenu}
        syncId={priceChartSyncId}
      >
        <defs>
          <linearGradient id={`grad-${symbol}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
          {showSessionColors && (
            <linearGradient id={sessionGradientId} x1="0" y1="0" x2="1" y2="0">
              {sessionGradientStops(chartData, color).map((s, i) => (
                <stop key={i} offset={s.offset} stopColor={s.color} />
              ))}
            </linearGradient>
          )}
        </defs>
        <XAxis
          dataKey="label"
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
          minTickGap={60}
        />
        <YAxis
          domain={areaDomain}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={60}
          tickFormatter={(v: number) => `$${fmt(v)}`}
        />
        <Tooltip
          cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }}
          content={() => null}
        />
        <Area
          type="monotone"
          dataKey="price"
          stroke={showSessionColors ? `url(#${sessionGradientId})` : color}
          strokeWidth={1.5}
          fill={`url(#grad-${symbol})`}
          dot={false}
          activeDot={{ r: 3, fill: color }}
          isAnimationActive={false}
        />
        {showSma21 && <Line dataKey="sma21" stroke={INDICATOR_COLORS.sma21} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />}
        {showSma50 && <Line dataKey="sma50" stroke={INDICATOR_COLORS.sma50} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />}
        {showBollinger && <Line dataKey="bbUpper" stroke={INDICATOR_COLORS.bollinger} strokeDasharray="4 3" dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />}
        {showBollinger && <Line dataKey="bbLower" stroke={INDICATOR_COLORS.bollinger} strokeDasharray="4 3" dot={false} strokeWidth={1} isAnimationActive={false} connectNulls />}
        {showVwap && <Line dataKey="vwap" stroke={INDICATOR_COLORS.vwap} dot={false} strokeWidth={1.5} isAnimationActive={false} connectNulls />}
        {hoverY != null && (
          <ReferenceLine
            y={hoverY}
            stroke={CROSSHAIR_STROKE}
            strokeDasharray="3 3"
            ifOverflow="visible"
            label={{ value: `$${hoverY.toFixed(2)}`, position: "right", fill: "#e4e4e7", fontSize: 15, fontWeight: 700, fontFamily: "monospace" }}
          />
        )}
      </ComposedChart>
      </ResponsiveContainer>
    </div>
    {subpanelsEl}
    </div>
  );
}
