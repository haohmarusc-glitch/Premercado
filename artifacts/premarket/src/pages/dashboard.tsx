import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import {
  useGetLatestReport,
  getGetLatestReportQueryKey,
  useGetObservationsSummary,
  getGetObservationsSummaryQueryKey,
  useGetTickerQuotes,
  getGetTickerQuotesQueryKey,
  useGetTickerChart,
  getGetTickerChartQueryKey,
  useGetAlertFiringsSummary,
  getGetAlertFiringsSummaryQueryKey,
  useListReports,
  getListReportsQueryKey,
} from "@workspace/api-client-react";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime, todayBRTDateString } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, TrendingUp, TrendingDown, Minus, RefreshCw, Bell, BellRing, Zap, ChevronDown, ChevronRight, Printer, Maximize2, Minimize2 } from "lucide-react";
import { exportToPDF } from "@/lib/export-pdf";
import { Link, useLocation } from "wouter";
import { CandleChart } from "@/components/candle-chart";
import { sessionGradientStops, hasExtendedSession, SESSION_COLORS } from "@/components/session-gradient";
import { useFullscreenEscape, useFullscreenChartHeight } from "@/hooks/use-fullscreen-chart";
import { IndicatorToggles } from "@/components/indicator-toggles";
import { attachIndicatorFields, INDICATOR_COLORS, type IndicatorKey } from "@/lib/indicators";
import { cn } from "@/lib/utils";
import { TradingViewChart } from "@/components/tradingview-chart";
import { TrendCard, useTrend } from "@/components/trend-card";
import { SmartMoneyCard } from "@/components/smart-money-card";
import { MarketAlertsCard } from "@/components/market-alerts-card";
import { AlertsChangeSummaryCard } from "@/components/alerts-change-summary-card";
import { ExitPlanSummaryCard } from "@/components/exit-plan-summary-card";

// ─── helpers ────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtVol(n: number | null | undefined) {
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

const PERIODS = [
  { key: "1d",  label: "1D" },
  { key: "5d",  label: "5D" },
  { key: "1mo", label: "1M" },
  { key: "3mo", label: "3M" },
  { key: "6mo", label: "6M" },
  { key: "1y",  label: "1Y" },
];

// ─── Sector groups (mirrors sector_contagion.py + observations.tsx) ─────────

const SECTOR_GROUPS = [
  { key: "memory",       label: "Memória",      tickers: ["MU", "SNDK", "WDC"] },
  { key: "interconnect", label: "Interconexão", tickers: ["SMCI", "ALAB", "CRDO", "ANET"] },
  { key: "power",        label: "Energia",      tickers: ["VRT"] },
  { key: "foundry",      label: "Fundição",     tickers: ["TSM", "ASML"] },
  { key: "other",        label: "Outros",       tickers: ["NVDA", "INTC", "GOOGL", "ARM", "TSLA"] },
];

// ─── Compact sentiment card ───────────────────────────────────────────────────

interface SentimentItem {
  ticker: string;
  bullish: number;
  bearish: number;
  neutral: number;
  lastSentiment: string;
  lastDate: string;
}

function SentimentDot({ sentiment }: { sentiment: string }) {
  const cls =
    sentiment === "bullish" ? "bg-green-500" :
    sentiment === "bearish" ? "bg-red-500" : "bg-slate-500";
  return <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cls}`} />;
}

function SentimentMiniCard({ s }: { s: SentimentItem }) {
  const total = s.bullish + s.bearish + s.neutral || 1;
  const bullPct  = Math.round((s.bullish  / total) * 100);
  const bearPct  = Math.round((s.bearish  / total) * 100);
  const neutralPct = Math.round((s.neutral / total) * 100);

  return (
    <div className="border border-border rounded-lg p-3 bg-card font-mono hover:border-border/80 transition-colors">
      <div className="flex items-center justify-between mb-2.5">
        <span className="font-bold text-primary text-sm tracking-widest">{s.ticker}</span>
        <SentimentDot sentiment={s.lastSentiment} />
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-green-500 w-7 font-bold">BULL</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-green-500 rounded-full" style={{ width: `${bullPct}%` }} />
          </div>
          <span className="text-[10px] text-green-500 w-4 text-right font-bold">{s.bullish}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-red-500 w-7 font-bold">BEAR</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-red-500 rounded-full" style={{ width: `${bearPct}%` }} />
          </div>
          <span className="text-[10px] text-red-500 w-4 text-right font-bold">{s.bearish}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-400 w-7">NEU</span>
          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
            <div className="h-full bg-slate-500 rounded-full" style={{ width: `${neutralPct}%` }} />
          </div>
          <span className="text-[10px] text-slate-400 w-4 text-right">{s.neutral}</span>
        </div>
      </div>
      <div className="text-[9px] text-muted-foreground mt-2 border-t border-border/40 pt-1.5 truncate">
        {s.lastDate}
      </div>
    </div>
  );
}

// ─── QuoteCard ───────────────────────────────────────────────────────────────

interface QuoteCardProps {
  symbol: string;
  price?: number | null;
  change?: number | null;
  changePct?: number | null;
  open?: number | null;
  previousClose?: number | null;
  dayHigh?: number | null;
  dayLow?: number | null;
  volume?: number | null;
  error?: string | null;
  selected: boolean;
  onSelect: () => void;
}

function QuoteCard({
  symbol, price, change, changePct, open, previousClose,
  dayHigh, dayLow, volume, error, selected, onSelect,
}: QuoteCardProps) {
  const positive = change != null && change >= 0;
  const negative = change != null && change < 0;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left border rounded-lg p-4 font-mono transition-all duration-200 ${
        selected
          ? "border-primary bg-primary/10 ring-1 ring-primary/40"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}
      data-testid={`quote-card-${symbol}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <span className={`font-bold text-lg tracking-widest ${selected ? "text-primary" : "text-primary"}`}>
            {symbol}
          </span>
          {selected && (
            <span className="ml-2 text-[10px] text-primary/70 uppercase font-mono">● selecionado</span>
          )}
          {error && <p className="text-xs text-red-400 mt-0.5 font-sans">{error}</p>}
        </div>
        <div className="text-right">
          <div className="text-xl font-bold text-foreground">
            {price != null ? `$${fmt(price)}` : "—"}
          </div>
          {changePct != null && (
            <div
              className={`flex items-center gap-1 justify-end text-sm font-bold ${
                positive ? "text-green-400" : negative ? "text-red-400" : "text-muted-foreground"
              }`}
            >
              {positive ? <TrendingUp className="h-3.5 w-3.5" /> : negative ? <TrendingDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
              {positive ? "+" : ""}{fmt(changePct)}%
              <span className="text-xs font-normal opacity-70">
                ({positive ? "+" : ""}{fmt(change)})
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 pt-3 border-t border-border/50">
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Open</div>
          <div className="text-xs text-foreground">{open != null ? `$${fmt(open)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Prev</div>
          <div className="text-xs text-foreground">{previousClose != null ? `$${fmt(previousClose)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Hi / Lo</div>
          <div className="text-xs text-green-400">{dayHigh != null ? `$${fmt(dayHigh)}` : "—"}</div>
          <div className="text-xs text-red-400">{dayLow != null ? `$${fmt(dayLow)}` : "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest mb-0.5">Volume</div>
          <div className="text-xs text-foreground">{fmtVol(volume)}</div>
        </div>
      </div>
    </button>
  );
}

// ─── PriceChart ──────────────────────────────────────────────────────────────

function PriceChart({ symbol, period, height = 200 }: { symbol: string; period: string; height?: number }) {
  const [, navigate] = useLocation();
  const [mode, setMode] = useState<"line" | "candle" | "tradingview">("line");
  // Indicadores técnicos -- todos desligados por padrão. No modo candle (SVG
  // puro, sem recharts) só os painéis auxiliares (Volume/RSI/MACD) valem;
  // overlay (SMA/Bollinger) precisaria desenhar dentro do CandleChart, que
  // não tem esse suporte ainda -- IndicatorToggles já restringe as opções
  // mostradas nesse modo via `available`.
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
  // Crosshair: linha horizontal no preço sob o cursor, acompanhando a linha
  // vertical (cursor do tooltip, sincronizado com os painéis auxiliares
  // abaixo via syncId) -- precisa ser state (não só o ref acima) pra
  // re-renderizar e mover a <ReferenceLine> a cada movimento do mouse.
  const [hoverY, setHoverY] = useState<number | null>(null);
  const openChartMenu = useCallback((price: number, clientX: number, clientY: number) => {
    const x = Math.min(clientX, window.innerWidth - 230);
    const y = Math.min(clientY, window.innerHeight - 120);
    setChartMenu({ x, y, price });
  }, []);
  const handleChartMouseMove = useCallback((state: { activePayload?: { payload?: { price?: number } }[] }) => {
    const price = state?.activePayload?.[0]?.payload?.price;
    if (typeof price === "number") {
      hoverPriceRef.current = price;
      setHoverY(price);
    }
  }, []);
  const handleChartMouseLeave = useCallback(() => setHoverY(null), []);
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

  const candles = data?.candles ?? [];
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
  const showSessionColors = hasExtendedSession(candles);
  const sessionGradientId = `session-grad-${symbol}`;

  // Indicadores técnicos: anexa as séries por índice e expande o domínio do
  // eixo Y do painel de preço se Bollinger/SMA passarem do range de fechamentos.
  const chartDataInd = attachIndicatorFields(chartData, closes);
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
  const overlayValues: number[] = [];
  for (const r of chartDataInd) {
    if (showBollinger) {
      if (r.bbUpper != null) overlayValues.push(r.bbUpper);
      if (r.bbLower != null) overlayValues.push(r.bbLower);
    }
    if (showSma50 && r.sma50 != null) overlayValues.push(r.sma50);
    if (showSma21 && r.sma21 != null) overlayValues.push(r.sma21);
  }
  const areaDomain: [number, number] = overlayValues.length
    ? [Math.min(minP - pad, ...overlayValues), Math.max(maxP + pad, ...overlayValues)]
    : [minP - pad, maxP + pad];
  const subpanelHeight = 70;
  const lastSubpanel = showMacd ? "macd" : showRsi ? "rsi" : showVolume ? "volume" : null;
  // Tooltip com layout próprio (em vez de contentStyle/labelStyle/itemStyle)
  // pra destacar o ticker bem maior/mais forte que o preço -- esses três
  // props do recharts aplicam um único estilo pra tudo.
  const priceTooltipContent = ({ active, label, payload }: { active?: boolean; label?: string; payload?: { value?: number }[] }) => {
    if (!active || !payload?.length || payload[0]?.value == null) return null;
    return (
      <div className="rounded-md border px-3 py-2 font-mono" style={{ background: "#09090b", borderColor: "#27272a" }}>
        <div className="text-sm text-[#a1a1aa] mb-1">{label}</div>
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-extrabold text-primary leading-none">{symbol}</span>
          <span className="text-xl font-bold text-[#e4e4e7]">${fmt(payload[0].value)}</span>
        </div>
      </div>
    );
  };
  const indicatorToggleEl = (
    <IndicatorToggles
      enabled={indicators}
      onToggle={toggleIndicator}
      available={mode === "candle" ? ["volume", "rsi", "macd"] : undefined}
    />
  );

  // Painéis auxiliares (Volume/RSI/MACD) valem tanto no modo line quanto
  // candle -- só o overlay (SMA/Bollinger) é exclusivo do line (ver acima).
  const subpanelsEl = (
    <>
      {showVolume && (
        <div className="mt-1">
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
              <Bar dataKey="vol" fill="#52525b" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {showRsi && (
        <div className="mt-1">
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
              <Line dataKey="rsi" stroke="#facc15" dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {showMacd && (
        <div className="mt-1">
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
              <Line dataKey="macdLine" stroke={INDICATOR_COLORS.macdLine} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
              <Line dataKey="macdSignal" stroke={INDICATOR_COLORS.macdSignal} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
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

  if (mode === "candle") {
    return (
      <div>
        {toggle}
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
    {showSessionColors && (
      <div className="flex items-center justify-end gap-2 mb-1 text-[9px] font-mono text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.pre }} /> pré
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.post }} /> pós
        </span>
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
          content={priceTooltipContent}
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
    {subpanelsEl}
    </div>
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [period, setPeriod] = useState("1d");
  const [chartExpanded, setChartExpanded] = useState(false);
  useFullscreenEscape(chartExpanded, () => setChartExpanded(false));
  const chartHeight = useFullscreenChartHeight(chartExpanded, 220, 200);
  const [expandedFlashId, setExpandedFlashId] = useState<number | null>(null);
  const [sectorTab, setSectorTab] = useState<string>("all");

  const { data: report, isLoading: loadingReport } = useGetLatestReport({
    query: { queryKey: getGetLatestReportQueryKey(), retry: false },
  });

  const today = todayBRTDateString();
  const { data: allReports } = useListReports({
    query: {
      queryKey: getListReportsQueryKey(),
      staleTime: 30_000,
      refetchInterval: 60_000,
    },
  });
  const flashToday = allReports
    ?.filter((r) => r.mode === "premarket" && r.date === today)
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()) ?? [];

  const { data: summary, isLoading: loadingSummary } = useGetObservationsSummary({
    query: {
      queryKey: getGetObservationsSummaryQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    },
  });

  const filteredSummary = useMemo(() => {
    if (!summary) return [];
    if (sectorTab === "all") return summary;
    const group = SECTOR_GROUPS.find((g) => g.key === sectorTab);
    return group ? summary.filter((s) => group.tickers.includes(s.ticker)) : summary;
  }, [summary, sectorTab]);

  const sectorAggregate = useMemo(() => {
    if (!filteredSummary.length) return null;
    return filteredSummary.reduce(
      (acc, s) => ({ bull: acc.bull + s.bullish, bear: acc.bear + s.bearish, neutral: acc.neutral + s.neutral }),
      { bull: 0, bear: 0, neutral: 0 },
    );
  }, [filteredSummary]);

  const { data: alertsSummary } = useGetAlertFiringsSummary({
    query: {
      queryKey: getGetAlertFiringsSummaryQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    },
  });

  const { data: quotes, isLoading: loadingQuotes, dataUpdatedAt } = useGetTickerQuotes({
    query: {
      queryKey: getGetTickerQuotesQueryKey(),
      refetchInterval: 60_000,
      staleTime: 55_000,
    },
  });

  // Auto-select first ticker when quotes load
  useEffect(() => {
    if (!selectedSymbol && quotes && quotes.length > 0) {
      setSelectedSymbol(quotes[0].symbol);
    }
  }, [quotes, selectedSymbol]);

  const activeSymbol = selectedSymbol ?? (quotes?.[0]?.symbol ?? null);

  const updatedTime = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("pt-BR", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : null;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">DASHBOARD</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Latest intelligence & sentiment summary
          </p>
        </div>
      </div>

      <AlertsChangeSummaryCard />

      <ExitPlanSummaryCard />

      <MarketAlertsCard />

      {/* ── Chart (seletor de ticker aqui em cima -- não precisa rolar até os
           cards de cotação pra trocar de ativo) ── */}
      {activeSymbol && (
        <div className={cn(
          "border border-border rounded-lg overflow-hidden bg-card",
          chartExpanded && "fixed inset-0 z-50 rounded-none overflow-y-auto",
        )}>
          {/* Header */}
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-border bg-secondary/30">
            <div className="flex items-center gap-2">
              <select
                value={activeSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
                className="bg-background border border-border rounded px-2 py-1 font-mono font-extrabold text-primary text-2xl tracking-wide focus:outline-none focus:ring-1 focus:ring-primary"
                aria-label="Selecionar ticker"
              >
                {(quotes ?? []).map((q) => (
                  <option key={q.symbol} value={q.symbol}>{q.symbol}</option>
                ))}
              </select>
              <span className="text-[11px] font-mono text-muted-foreground hidden sm:inline">— Histórico de preço</span>
            </div>

            <div className="flex items-center gap-2">
              {/* Period selector */}
              <div className="flex items-center gap-1">
                {PERIODS.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => setPeriod(p.key)}
                    className={`px-2.5 py-1 rounded text-[11px] font-mono font-bold transition-colors ${
                      period === p.key
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                    }`}
                    data-testid={`period-btn-${p.key}`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* Expandir/recolher gráfico */}
              <button
                type="button"
                onClick={() => setChartExpanded((v) => !v)}
                className="p-1.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
                aria-label={chartExpanded ? "Recolher gráfico" : "Expandir gráfico"}
                title={chartExpanded ? "Recolher gráfico (Esc)" : "Expandir gráfico (tela cheia)"}
              >
                {chartExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          {/* Chart body */}
          <div className="p-4">
            <PriceChart symbol={activeSymbol} period={period} height={chartHeight} />
          </div>
        </div>
      )}

      {/* ── Quote cards (selectable) ── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
            Cotações — clique para ver o gráfico
          </h2>
          {updatedTime && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground">
              <RefreshCw className="h-3 w-3" />
              {updatedTime}
            </span>
          )}
        </div>

        {loadingQuotes ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : quotes && quotes.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {quotes.map((q) => (
              <QuoteCard
                key={q.symbol}
                {...q}
                selected={q.symbol === activeSymbol}
                onSelect={() => setSelectedSymbol(q.symbol)}
              />
            ))}
          </div>
        ) : (
          <div className="border border-dashed border-border rounded-lg p-6 text-center">
            <p className="text-xs font-mono text-muted-foreground">
              Sem dados de cotação. Adicione tickers em Settings.
            </p>
          </div>
        )}
      </div>

      {/* ── Alerts summary ── */}
      {alertsSummary && alertsSummary.total > 0 && (
        <Link href="/alerts">
          <div className="border border-border rounded-lg bg-card hover:border-primary/40 transition-colors cursor-pointer">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50">
              <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground uppercase tracking-widest">
                <Bell className="h-3.5 w-3.5" />
                Alertas de Preço
              </div>
              {alertsSummary.firingToday > 0 && (
                <div className="flex items-center gap-1.5 text-[11px] font-mono text-red-400 animate-pulse">
                  <BellRing className="h-3.5 w-3.5" />
                  {alertsSummary.firingToday} disparado{alertsSummary.firingToday > 1 ? "s" : ""} hoje
                </div>
              )}
            </div>
            <div className="grid grid-cols-3 divide-x divide-border/50">
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Total</div>
                <div className="text-xl font-bold font-mono">{alertsSummary.total}</div>
              </div>
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">Ativos</div>
                <div className={`text-xl font-bold font-mono ${alertsSummary.active > 0 ? "text-primary" : "text-muted-foreground"}`}>
                  {alertsSummary.active}
                </div>
              </div>
              <div className="px-4 py-3 text-center">
                <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1 flex items-center justify-center gap-1">
                  <Zap className="h-2.5 w-2.5" />
                  Hoje
                </div>
                <div className={`text-xl font-bold font-mono ${alertsSummary.firingToday > 0 ? "text-red-400" : "text-muted-foreground"}`}>
                  {alertsSummary.firingToday}
                </div>
              </div>
            </div>
          </div>
        </Link>
      )}

      {/* ── Trend (confluência técnico + notícias) ── */}
      {activeSymbol && <TrendCard symbol={activeSymbol} />}

      {/* ── Smart Money (Congresso + dark pool — opcional, precisa de chave) ── */}
      {activeSymbol && <SmartMoneyCard symbol={activeSymbol} />}

      {/* ── Sentiment summary ── */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
            Sentimento por Ativo
          </h2>
          {summary && (
            <span className="text-[10px] font-mono text-muted-foreground border border-border px-1.5 py-0.5 rounded">
              {filteredSummary.length}/{summary.length}
            </span>
          )}
        </div>

        {/* Sector filter */}
        {!loadingSummary && summary && summary.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            <button
              type="button"
              onClick={() => setSectorTab("all")}
              className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold transition-colors border ${
                sectorTab === "all"
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
              }`}
            >
              Todos
              <span className="ml-1 opacity-70">({summary.length})</span>
            </button>
            {SECTOR_GROUPS.map((g) => {
              const count = summary.filter((s) => g.tickers.includes(s.ticker)).length;
              if (count === 0) return null;
              return (
                <button
                  key={g.key}
                  type="button"
                  onClick={() => setSectorTab(g.key)}
                  className={`px-2.5 py-1 rounded-md font-mono text-xs font-bold transition-colors border ${
                    sectorTab === g.key
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-secondary border-border text-muted-foreground hover:text-foreground hover:border-border/80"
                  }`}
                >
                  {g.label}
                  <span className="ml-1 opacity-70">({count})</span>
                </button>
              );
            })}
          </div>
        )}

        {/* Sector aggregate row */}
        {sectorTab !== "all" && sectorAggregate && filteredSummary.length > 0 && (
          <div className="flex items-center gap-4 mb-3 px-3 py-2 border border-border/40 rounded-lg bg-secondary/20 font-mono text-xs">
            <span className="text-muted-foreground uppercase tracking-widest text-[10px]">Setor</span>
            <span className="text-green-500 font-bold">BULL {sectorAggregate.bull}</span>
            <span className="text-red-500 font-bold">BEAR {sectorAggregate.bear}</span>
            <span className="text-slate-400">NEU {sectorAggregate.neutral}</span>
            <span className={`ml-auto font-bold text-[11px] ${
              sectorAggregate.bull > sectorAggregate.bear ? "text-green-500" :
              sectorAggregate.bear > sectorAggregate.bull ? "text-red-500" : "text-slate-400"
            }`}>
              {sectorAggregate.bull > sectorAggregate.bear ? "↑ BULLISH" :
               sectorAggregate.bear > sectorAggregate.bull ? "↓ BEARISH" : "→ NEUTRO"}
            </span>
          </div>
        )}

        {loadingSummary ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        ) : filteredSummary.length > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {filteredSummary.map((s) => (
              <SentimentMiniCard key={s.ticker} s={s} />
            ))}
          </div>
        ) : (
          <div className="p-8 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono text-sm">
            Sem dados de sentimento.
          </div>
        )}
      </div>

      {/* ── Flash Scans de Hoje ── */}
      {flashToday.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Zap className="h-3.5 w-3.5 text-primary" />
            <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-widest">
              Flash Scans de Hoje
            </h2>
            <span className="px-1.5 py-0.5 rounded bg-primary/10 border border-primary/30 text-primary font-mono text-[10px]">
              {flashToday.length}
            </span>
          </div>

          <div className="space-y-2">
            {flashToday.map((scan) => {
              const isOpen = expandedFlashId === scan.id;
              const time = new Date(scan.createdAt).toLocaleTimeString("pt-BR", {
                hour: "2-digit",
                minute: "2-digit",
                timeZone: "America/Sao_Paulo",
              });
              return (
                <div
                  key={scan.id}
                  className="border border-primary/20 rounded-lg overflow-hidden bg-card"
                >
                  <button
                    type="button"
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-primary/5 transition-colors text-left"
                    onClick={() => setExpandedFlashId(isOpen ? null : scan.id)}
                  >
                    <div className="flex items-center gap-2">
                      {isOpen
                        ? <ChevronDown className="h-3.5 w-3.5 text-primary flex-shrink-0" />
                        : <ChevronRight className="h-3.5 w-3.5 text-primary flex-shrink-0" />}
                      <span className="font-mono text-primary font-bold text-sm">⚡ {time} BRT</span>
                    </div>
                    <span className="text-xs font-mono text-muted-foreground">
                      Flash Scan
                    </span>
                  </button>
                  {isOpen && (
                    <div className="border-t border-primary/10 px-5 py-4 bg-background">
                      <MarkdownContent content={scan.content} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Latest report ── */}
      <div>
        <h2 className="text-sm font-mono text-muted-foreground mb-4">LATEST PRE-MARKET REPORT</h2>
        {loadingReport ? (
          <div className="space-y-4">
            <Skeleton className="h-8 w-1/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : report ? (
          <Card className="bg-card border-border shadow-none rounded-sm">
            <CardHeader className="border-b border-border bg-secondary/30 pb-4">
              <div className="flex items-center justify-between">
                <CardTitle className="font-mono text-lg">{report.date}</CardTitle>
                <div className="flex items-center gap-3">
                  <div className="text-xs text-muted-foreground font-mono">
                    {formatDateTime(report.createdAt)}
                  </div>
                  <button
                    onClick={() => exportToPDF(`Relatório ${report.date}`, `<h1>Relatório ${report.date}</h1><pre>${report.content}</pre>`)}
                    className="flex items-center gap-1.5 px-2.5 py-1 border border-border rounded font-mono text-xs text-muted-foreground hover:text-foreground hover:border-border/80 transition-colors"
                    title="Exportar PDF"
                  >
                    <Printer className="h-3.5 w-3.5" /> Exportar PDF
                  </button>
                </div>
              </div>
              <div className="flex gap-2 mt-2">
                {report.tickers.map((t) => (
                  <Badge key={t} variant="secondary" className="font-mono bg-secondary border-border">
                    {t}
                  </Badge>
                ))}
              </div>
            </CardHeader>
            <CardContent className="pt-6">
              <MarkdownContent content={report.content} />
            </CardContent>
          </Card>
        ) : (
          <div className="p-12 text-center border border-dashed border-border rounded-sm bg-secondary/20">
            <AlertTriangle className="h-8 w-8 text-muted-foreground mx-auto mb-4" />
            <p className="text-muted-foreground font-mono">No reports available for today.</p>
            <p className="text-xs text-muted-foreground font-mono mt-2">
              Run the agent to generate a report.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
