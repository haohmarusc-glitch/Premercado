import { useState, useMemo, useEffect, useRef, Fragment, useCallback } from "react";
import { useLocation } from "wouter";
import { useQueries } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import {
  useListPortfolioPositions,
  getListPortfolioPositionsQueryKey,
  useCreatePortfolioPosition,
  useUpdatePortfolioPosition,
  useDeletePortfolioPosition,
  useListPortfolioPurchases,
  getListPortfolioPurchasesQueryKey,
  useCreatePortfolioPurchase,
  useDeletePortfolioPurchase,
  useGetTickerQuotes,
  getGetTickerQuotesQueryKey,
  listPortfolioPurchases,
} from "@workspace/api-client-react";
import type { PortfolioPosition, NewsItem } from "@workspace/api-client-react";
import { useAuth } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { ChevronDown, ChevronRight, Plus, Pencil, Trash2, TrendingUp, DollarSign, Wallet, Activity, RefreshCw, LineChart as LineChartIcon, CandlestickChart as CandlestickChartIcon, Globe as GlobeIcon, Maximize2, Minimize2, Lock } from "lucide-react";
import { Line, ComposedChart, Bar, ReferenceDot, ReferenceLine, XAxis, YAxis, ResponsiveContainer, PieChart, Pie, Cell, Tooltip as RechartsTooltip } from "recharts";
import { useGetTickerChart, getGetTickerChartQueryKey, useGetNews, getGetNewsQueryKey } from "@workspace/api-client-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import { CandleShape, toCandleRangeData, candleDomain } from "@/components/candle-shape";
import { attachNewsMarkers, NewsMarkerShape, newsDotShape, parseNewsPublished } from "@/components/news-markers";
import { sessionGradientStops, hasExtendedSession, SESSION_COLORS } from "@/components/session-gradient";
import { useFullscreenEscape, useFullscreenChartHeight } from "@/hooks/use-fullscreen-chart";
import { IndicatorToggles } from "@/components/indicator-toggles";
import { attachIndicatorFields, INDICATOR_COLORS, type IndicatorKey } from "@/lib/indicators";
import { TradingViewChart } from "@/components/tradingview-chart";
import { useViewMode } from "@/lib/view-mode";

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt$ = (n: number) =>
  `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
const fmtVolumeShort = (n: number) => {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
};
// Fonte/cor dos eixos do gráfico de preço + painéis auxiliares -- maior e
// mais clara que o texto secundário padrão, pra ficar legível em cima do fundo escuro.
const AXIS_TICK = { fontSize: 14, fontFamily: "monospace", fill: "#d4d4d8" };
const CROSSHAIR_STROKE = "#a1a1aa";
// Posições da B3 (sufixo .SA no Yahoo) são cotadas e cadastradas em reais;
// os agregados da carteira convertem para USD pelo câmbio BRL=X ao vivo
const isB3 = (ticker: string) => ticker.toUpperCase().endsWith(".SA");
const fmtR$ = (n: number) =>
  `R$ ${Math.abs(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtMoney = (n: number, brl: boolean) => (brl ? fmtR$(n) : fmt$(n));
const fmtQty = (n: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 });

function daysSince(dateStr: string): number {
  return (Date.now() - new Date(dateStr).getTime()) / 86_400_000;
}

function getMaxDownAlert(pnlPct: number, thresholds: number[]): number | null {
  const crossed = thresholds.filter((t) => pnlPct <= -t);
  return crossed.length ? Math.max(...crossed) : null;
}

function getMaxUpAlert(pnlPct: number, thresholds: number[]): number | null {
  const crossed = thresholds.filter((t) => pnlPct >= t);
  return crossed.length ? Math.max(...crossed) : null;
}

type ExtendedQuoteFields = {
  marketState?: string | null;
  preMarketPrice?: number | null;
  postMarketPrice?: number | null;
};

// Prioriza o preço de pré/pós-mercado quando existir -- o pregão regular só
// reabre no dia seguinte, então usar só `price` esconde movimentos grandes
// fora do horário (ex.: pop de after-hours após guidance/resultado).
function pickExtendedPrice(q: ExtendedQuoteFields): { price: number; label: "Pré" | "Pós" } | null {
  const st = q.marketState ?? "";
  if (st.startsWith("PRE") && q.preMarketPrice != null) return { price: q.preMarketPrice, label: "Pré" };
  if (st.startsWith("POST") && q.postMarketPrice != null) return { price: q.postMarketPrice, label: "Pós" };
  if (q.postMarketPrice != null) return { price: q.postMarketPrice, label: "Pós" };
  if (q.preMarketPrice != null) return { price: q.preMarketPrice, label: "Pré" };
  return null;
}

// ── Position form ─────────────────────────────────────────────────────────────

interface PosForm {
  ticker: string;
  quantity: string;
  avgCost: string;
  investedAmount: string;
  dividends: string;
  isEtf: boolean;
  firstPurchaseDate: string;
  notes: string;
  downAlertPcts: string;
  upAlertPcts: string;
  notifyEmail: string;
}

const EMPTY_FORM: PosForm = {
  ticker: "",
  quantity: "",
  avgCost: "",
  investedAmount: "",
  dividends: "",
  isEtf: false,
  firstPurchaseDate: "",
  notes: "",
  downAlertPcts: "10,15,20,30",
  upAlertPcts: "10,15,20,30,40,50",
  notifyEmail: "",
};

function posToForm(p: PortfolioPosition): PosForm {
  return {
    ticker: p.ticker,
    quantity: String(p.quantity),
    avgCost: String(p.avgCost),
    investedAmount: String(p.investedAmount),
    dividends: p.dividends ? String(p.dividends) : "",
    isEtf: p.isEtf ?? false,
    firstPurchaseDate: p.firstPurchaseDate,
    notes: p.notes ?? "",
    downAlertPcts: p.downAlertPcts.join(","),
    upAlertPcts: p.upAlertPcts.join(","),
    notifyEmail: p.notifyEmail ?? "",
  };
}

// Deriva quantidade/investido/custo médio das compras ABERTAS (não vendidas),
// para a linha da posição refletir sempre as operações registradas. Só deriva
// quando há compras e todas têm preço; senão usa os valores salvos na posição.
function derivePosition(
  pos: { quantity: number; investedAmount: number; avgCost: number },
  purchases: Array<{ amount: number; purchasePrice?: number | null; saleDate?: string | null; salePrice?: number | null }>,
): { quantity: number; invested: number; avgCost: number; derived: boolean } {
  const open = purchases.filter((p) => !(p.saleDate && p.salePrice));
  const allHavePrice = open.length > 0 && open.every((p) => p.purchasePrice != null && p.purchasePrice > 0);
  if (!allHavePrice) {
    return { quantity: pos.quantity, invested: pos.investedAmount, avgCost: pos.avgCost, derived: false };
  }
  let quantity = 0;
  let invested = 0;
  for (const p of open) {
    quantity += p.amount / (p.purchasePrice as number);
    invested += p.amount;
  }
  return { quantity, invested, avgCost: quantity > 0 ? invested / quantity : pos.avgCost, derived: true };
}

function parseAlertPcts(s: string): number[] {
  return s
    .split(",")
    .map((x) => parseInt(x.trim(), 10))
    .filter((n) => !isNaN(n) && n > 0);
}

// ── Caixa disponível (USD não investido na corretora) ──────────────────────────
// Persistido no banco (settings) por modo (real/paper) via /api/portfolio/cash.
// É o "Disponível para investir" da corretora — entra no Patrimônio total mas
// não conta como investido.
type CashByMode = { real: number; simulated: number };

// Retorna null em caso de falha -- NUNCA um zero substituto, pra não arriscar
// sobrescrever visualmente um saldo real por uma falha transitória de rede.
async function fetchCash(): Promise<CashByMode | null> {
  try {
    const r = await fetch("/api/portfolio/cash", { credentials: "include" });
    if (!r.ok) return null;
    const d = await r.json();
    return { real: Number(d.real ?? 0), simulated: Number(d.simulated ?? 0) };
  } catch {
    return null;
  }
}

async function fetchFxRate(): Promise<number | null> {
  try {
    const r = await fetch("/api/fx/usdbrl", { credentials: "include" });
    if (!r.ok) return null;
    const d = await r.json();
    return typeof d.rate === "number" && d.rate > 0 ? d.rate : null;
  } catch {
    return null;
  }
}

async function persistCash(mode: "real" | "simulated", amount: number): Promise<void> {
  const r = await fetch("/api/portfolio/cash", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, amount }),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`Falha ao salvar caixa (HTTP ${r.status})`);
}

// ── Price chart with period selector ─────────────────────────────────────────

const CHART_PERIODS = [
  { label: "1D", value: "1d" },
  { label: "5D", value: "5d" },
  { label: "1M", value: "1mo" },
  { label: "1A", value: "1y" },
] as const;

type ChartPeriod = typeof CHART_PERIODS[number]["value"];

function formatXTick(ts: number, period: ChartPeriod): string {
  const d = new Date(ts);
  if (period === "1d")  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (period === "5d")  return d.toLocaleDateString("pt-BR", { weekday: "short", day: "numeric" });
  if (period === "1mo") return d.toLocaleDateString("pt-BR", { month: "short", day: "numeric" });
  return d.toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
}

type PortfolioChartVisual = "line" | "candle" | "tradingview";

function fmtNewsDate(v: string | number | null | undefined): string {
  const ts = parseNewsPublished(v);
  if (ts == null) return "";
  return new Date(ts).toLocaleString("pt-BR", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function PriceChart({ ticker }: { ticker: string }) {
  const [, navigate] = useLocation();
  const [period, setPeriod] = useState<ChartPeriod>("1d");
  const [visual, setVisual] = useState<PortfolioChartVisual>("line");
  // Notícias abertas na caixa (modal) ao tocar num marcador amarelo do gráfico.
  const [activeNews, setActiveNews] = useState<NewsItem[] | null>(null);
  // Menu de botão direito ("criar alerta neste preço") -- só existe no
  // gráfico próprio (line/candle), não dá pra interceptar clique dentro do
  // iframe da TradingView. O preço vem do último ponto sob o cursor que o
  // próprio recharts já resolveu via onMouseMove (mesmo hit-test do
  // tooltip), guardado num ref pra não re-renderizar a cada movimento —
  // só lido quando o context menu de fato abre.
  const [chartMenu, setChartMenu] = useState<{ x: number; y: number; price: number } | null>(null);
  const hoverPriceRef = useRef<number | null>(null);
  // Crosshair: linha horizontal no preço sob o cursor, acompanhando a linha
  // vertical (cursor do tooltip) -- precisa ser state (não só o ref acima)
  // pra re-renderizar e mover a <ReferenceLine> a cada movimento do mouse.
  const [hoverY, setHoverY] = useState<number | null>(null);
  const handleChartMouseMove = useCallback((state: { activePayload?: { payload?: { v?: number; c?: number } }[] }) => {
    const p = state?.activePayload?.[0]?.payload;
    const price = p?.v ?? p?.c;
    if (typeof price === "number") {
      hoverPriceRef.current = price;
      setHoverY(price);
    }
  }, []);
  const handleChartMouseLeave = useCallback(() => setHoverY(null), []);
  const handleChartContextMenu = useCallback((_state: unknown, e: React.MouseEvent) => {
    if (hoverPriceRef.current == null) return;
    e.preventDefault();
    // Evita o menu vazar pra fora da tela quando o clique é perto da borda direita/de baixo.
    const x = Math.min(e.clientX, window.innerWidth - 230);
    const y = Math.min(e.clientY, window.innerHeight - 120);
    setChartMenu({ x, y, price: hoverPriceRef.current });
  }, []);
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
  // Indicadores técnicos (tendência/momento, volatilidade e confirmação) --
  // todos desligados por padrão, o usuário liga só o que quiser ver.
  const [indicators, setIndicators] = useState<Set<IndicatorKey>>(new Set());
  const toggleIndicator = useCallback((key: IndicatorKey) => {
    setIndicators((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  // "Expandir" agora é tela cheia de verdade (overlay fixed cobrindo a
  // viewport), não só um pouco mais alto -- ver useFullscreenChartHeight.
  const [expanded, setExpanded] = useState(false);
  useFullscreenEscape(expanded, () => setExpanded(false));
  const chartHeight = useFullscreenChartHeight(expanded, 190, visual === "tradingview" ? 480 : 200);
  const { data, isLoading } = useGetTickerChart(
    { symbol: ticker, period },
    {
      query: {
        queryKey: getGetTickerChartQueryKey({ symbol: ticker, period }),
        staleTime: period === "1d" ? 60_000 : 5 * 60_000,
        refetchInterval: period === "1d" ? 60_000 : false,
      },
    },
  );

  const { data: newsData } = useGetNews(
    { tickers: ticker },
    {
      query: {
        queryKey: getGetNewsQueryKey({ tickers: ticker }),
        staleTime: 5 * 60_000,
      },
    },
  );
  const newsItemsList = newsData?.items?.[0]?.news ?? [];

  const candles = data?.candles ?? [];
  const closes = candles.map((c) => c.c);
  const chartDataBase = candles.map((c) => ({ t: c.t, v: c.c, vol: c.v, session: c.session }));
  const isUp = candles.length >= 2
    ? (candles[candles.length - 1]?.c ?? 0) >= (candles[0]?.c ?? 0)
    : true;
  const color = isUp ? "#4ade80" : "#f87171";
  const showSessionColors = hasExtendedSession(candles);
  const sessionGradientId = `session-grad-${ticker}`;
  const min = chartDataBase.length ? Math.min(...chartDataBase.map((d) => d.v)) * 0.998 : 0;
  const max = chartDataBase.length ? Math.max(...chartDataBase.map((d) => d.v)) * 1.002 : 100;
  const tickCount = Math.min(6, candles.length);
  const chartData = attachNewsMarkers(chartDataBase, newsItemsList).map((c) => ({
    ...c,
    newsY: c.newsItems.length ? max : null,
  }));
  const candleDomainRange = candleDomain(candles);
  const candleData = attachNewsMarkers(toCandleRangeData(candles), newsItemsList);
  const candleNewsMarkers = candleData.filter((c) => c.newsItems.length > 0);
  // Sincroniza o crosshair (linha vertical) entre o painel de preço e os
  // painéis auxiliares (Volume/RSI/MACD) -- recharts casa por índice quando
  // os gráficos compartilham o mesmo syncId.
  const priceChartSyncId = `price-${ticker}`;

  // Indicadores técnicos (overlay no painel de preço + painéis auxiliares
  // embaixo) -- anexados por índice tanto nos dados de linha quanto de vela.
  const chartDataInd = attachIndicatorFields(chartData, closes);
  const candleDataInd = attachIndicatorFields(candleData, closes);
  const rsiRows = visual === "candle" ? candleDataInd : chartDataInd;
  const macdRows = rsiRows;
  const showVolume = indicators.has("volume");
  const showRsi = indicators.has("rsi");
  const showMacd = indicators.has("macd");
  const showSma21 = indicators.has("sma21");
  const showSma50 = indicators.has("sma50");
  const showBollinger = indicators.has("bollinger");
  // Bollinger/SMA podem passar do range de fechamentos -- expande o domínio
  // do eixo Y do painel de preço pra não cortar as linhas de overlay.
  const overlayValues: number[] = [];
  for (const r of chartDataInd) {
    if (showBollinger) {
      if (r.bbUpper != null) overlayValues.push(r.bbUpper);
      if (r.bbLower != null) overlayValues.push(r.bbLower);
    }
    if (showSma50 && r.sma50 != null) overlayValues.push(r.sma50);
    if (showSma21 && r.sma21 != null) overlayValues.push(r.sma21);
  }
  const yMin = overlayValues.length ? Math.min(min, ...overlayValues) : min;
  const yMax = overlayValues.length ? Math.max(max, ...overlayValues) : max;
  const candleYMin = overlayValues.length ? Math.min(candleDomainRange[0], ...overlayValues) : candleDomainRange[0];
  const candleYMax = overlayValues.length ? Math.max(candleDomainRange[1], ...overlayValues) : candleDomainRange[1];
  const volumeRows = visual === "candle" ? candleDataInd : chartDataInd;
  const volumeKey = visual === "candle" ? "v" : "vol";
  // Só o painel auxiliar mais embaixo mostra os labels de data no eixo X --
  // senão fica repetido em cada painel (Volume/RSI/MACD).
  const lastSubpanel = showMacd ? "macd" : showRsi ? "rsi" : showVolume ? "volume" : null;
  const subpanelHeight = 70;

  // Tooltip com layout próprio (em vez de contentStyle/labelStyle/itemStyle)
  // pra poder destacar o ticker bem maior/mais forte que o resto do texto --
  // esses três props do recharts aplicam um único estilo pra tudo, sem como
  // diferenciar nome do ticker vs. preço/OHLC.
  const fmtTooltipDate = (label: number) => {
    const d = new Date(label);
    if (period === "1d") return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  };
  // Linhas extras no tooltip pros indicadores atualmente ligados -- só mostra
  // o que estiver visível no gráfico no momento (mesmo critério do overlay/
  // painéis auxiliares), lendo os valores já anexados por índice em cada
  // ponto (ver attachIndicatorFields). `volKey` muda porque o modo line usa
  // "vol" (pra não colidir com o "v" que ali significa preço) e o candle usa
  // o "v" de verdade (volume real do Candle).
  const renderIndicatorRows = (p: {
    sma21?: number | null; sma50?: number | null;
    bbUpper?: number | null; bbLower?: number | null;
    rsi?: number | null; macdLine?: number | null; macdSignal?: number | null;
    vol?: number | null; v?: number | null;
  }, volKey: "vol" | "v") => {
    const rows: { label: string; value: string; color: string }[] = [];
    if (showSma21 && p.sma21 != null) rows.push({ label: "SMA21", value: `$${p.sma21.toFixed(2)}`, color: INDICATOR_COLORS.sma21 });
    if (showSma50 && p.sma50 != null) rows.push({ label: "SMA50", value: `$${p.sma50.toFixed(2)}`, color: INDICATOR_COLORS.sma50 });
    if (showBollinger && p.bbUpper != null) rows.push({ label: "BB Sup", value: `$${p.bbUpper.toFixed(2)}`, color: INDICATOR_COLORS.bollinger });
    if (showBollinger && p.bbLower != null) rows.push({ label: "BB Inf", value: `$${p.bbLower.toFixed(2)}`, color: INDICATOR_COLORS.bollinger });
    if (showVolume) {
      const vol = volKey === "vol" ? p.vol : p.v;
      if (vol != null) rows.push({ label: "Volume", value: fmtVolumeShort(vol), color: "#a1a1aa" });
    }
    if (showRsi && p.rsi != null) rows.push({ label: "IFR", value: p.rsi.toFixed(1), color: "#facc15" });
    if (showMacd && p.macdLine != null) rows.push({ label: "MACD", value: p.macdLine.toFixed(3), color: INDICATOR_COLORS.macdLine });
    if (showMacd && p.macdSignal != null) rows.push({ label: "Sinal", value: p.macdSignal.toFixed(3), color: INDICATOR_COLORS.macdSignal });
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
  const candleTooltipContent = ({ active, label, payload }: { active?: boolean; label?: number; payload?: { payload?: { o: number; h: number; l: number; c: number; v?: number; sma21?: number | null; sma50?: number | null; bbUpper?: number | null; bbLower?: number | null; rsi?: number | null; macdLine?: number | null; macdSignal?: number | null } }[] }) => {
    if (!active || !payload?.length) return null;
    const p = payload[0]?.payload;
    if (!p) return null;
    return (
      <div className="rounded-md border px-3 py-2 font-mono" style={{ background: "#09090b", borderColor: "#27272a" }}>
        <div className="text-sm text-[#a1a1aa] mb-1">{fmtTooltipDate(label as number)}</div>
        <div className="text-2xl font-extrabold text-primary leading-none mb-1">{ticker}</div>
        <div className="text-base text-[#e4e4e7]">
          O {p.o.toFixed(2)} · H {p.h.toFixed(2)} · L {p.l.toFixed(2)} · C {p.c.toFixed(2)}
        </div>
        {renderIndicatorRows(p, "v")}
      </div>
    );
  };
  const lineTooltipContent = ({ active, label, payload }: { active?: boolean; label?: number; payload?: { dataKey?: string; value?: number; payload?: { newsItems?: { title: string }[]; vol?: number | null; sma21?: number | null; sma50?: number | null; bbUpper?: number | null; bbLower?: number | null; rsi?: number | null; macdLine?: number | null; macdSignal?: number | null } }[] }) => {
    if (!active || !payload?.length) return null;
    const newsEntry = payload.find((it) => it.dataKey === "newsY" && it.payload?.newsItems?.length);
    if (newsEntry) {
      const items = newsEntry.payload!.newsItems!;
      return (
        <div className="rounded-md border px-3 py-2 font-mono max-w-xs" style={{ background: "#09090b", borderColor: "#27272a" }}>
          <div className="text-sm text-[#a1a1aa] mb-1">{fmtTooltipDate(label as number)}</div>
          <div className="text-sm text-[#e4e4e7]">📰 {items.map((n) => n.title).join(" · ")}</div>
        </div>
      );
    }
    const priceEntry = payload.find((it) => it.dataKey === "v");
    if (!priceEntry || priceEntry.value == null) return null;
    return (
      <div className="rounded-md border px-3 py-2 font-mono" style={{ background: "#09090b", borderColor: "#27272a" }}>
        <div className="text-sm text-[#a1a1aa] mb-1">{fmtTooltipDate(label as number)}</div>
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-extrabold text-primary leading-none">{ticker}</span>
          <span className="text-xl font-bold text-[#e4e4e7]">${priceEntry.value.toFixed(2)}</span>
        </div>
        {priceEntry.payload && renderIndicatorRows(priceEntry.payload, "vol")}
      </div>
    );
  };

  return (
    <div className={cn("w-full mt-3", expanded && "fixed inset-0 z-50 bg-background p-4 overflow-y-auto")}>
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="flex items-baseline gap-1.5 font-mono">
            <span className="text-2xl font-extrabold text-primary tracking-wide">{ticker}</span>
            <span className="text-[10px] text-muted-foreground uppercase tracking-widest">— histórico</span>
          </span>
          {visual === "line" && showSessionColors && (
            <span className="flex items-center gap-2 text-[9px] font-mono text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.pre }} /> pré
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-1.5 w-3 rounded-full" style={{ background: SESSION_COLORS.post }} /> pós
              </span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-0.5 border border-border rounded p-0.5">
            <button
              onClick={() => setVisual("line")}
              title="Linha"
              className={cn(
                "p-0.5 rounded transition-colors",
                visual === "line"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary",
              )}
            >
              <LineChartIcon className="h-3 w-3" />
            </button>
            <button
              onClick={() => setVisual("candle")}
              title="Vela"
              className={cn(
                "p-0.5 rounded transition-colors",
                visual === "candle"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary",
              )}
            >
              <CandlestickChartIcon className="h-3 w-3" />
            </button>
            <button
              onClick={() => setVisual("tradingview")}
              title="TradingView"
              className={cn(
                "p-0.5 rounded transition-colors",
                visual === "tradingview"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary",
              )}
            >
              <GlobeIcon className="h-3 w-3" />
            </button>
          </div>
          {visual !== "tradingview" && (
          <div className="flex gap-1">
            {CHART_PERIODS.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setPeriod(value)}
                className={cn(
                  "text-[10px] font-mono px-2 py-0.5 rounded border transition-colors",
                  period === value
                    ? "border-primary text-primary bg-primary/10"
                    : "border-border text-muted-foreground hover:text-foreground hover:border-foreground/40",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          )}
          {visual !== "tradingview" && (
            <IndicatorToggles enabled={indicators} onToggle={toggleIndicator} />
          )}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="p-1 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            aria-label={expanded ? "Recolher gráfico" : "Expandir gráfico"}
            title={expanded ? "Recolher gráfico (Esc)" : "Expandir gráfico (tela cheia)"}
          >
            {expanded ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
          </button>
        </div>
      </div>

      {visual === "tradingview" ? (
        <TradingViewChart symbol={ticker} height={chartHeight} />
      ) : isLoading ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          carregando...
        </div>
      ) : !chartData.length ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          sem dados
        </div>
      ) : visual === "candle" ? (
        <ResponsiveContainer width="100%" height={chartHeight}>
          <ComposedChart
            data={candleDataInd}
            margin={{ top: 2, right: 4, bottom: 2, left: 4 }}
            onMouseMove={handleChartMouseMove}
            onMouseLeave={handleChartMouseLeave}
            onContextMenu={handleChartContextMenu}
            syncId={priceChartSyncId}
          >
            <XAxis
              dataKey="t"
              tickFormatter={(v) => formatXTick(v as number, period)}
              tick={AXIS_TICK}
              tickCount={tickCount}
              interval="preserveStartEnd"
              minTickGap={50}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[candleYMin, candleYMax]}
              tick={AXIS_TICK}
              tickFormatter={(v) => `$${(v as number).toFixed(0)}`}
              width={42}
              axisLine={false}
              tickLine={false}
            />
            <RechartsTooltip
              cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }}
              content={candleTooltipContent}
            />
            <Bar dataKey="range" shape={CandleShape} isAnimationActive={false} />
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
            {candleNewsMarkers.map((m) => (
              <ReferenceDot
                key={m.t}
                x={m.t}
                y={candleDomainRange[1]}
                ifOverflow="visible"
                shape={newsDotShape(m.newsItems, () => setActiveNews(m.newsItems))}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          <ComposedChart
            data={chartDataInd}
            margin={{ top: 2, right: 4, bottom: 2, left: 4 }}
            onMouseMove={handleChartMouseMove}
            onMouseLeave={handleChartMouseLeave}
            onContextMenu={handleChartContextMenu}
            syncId={priceChartSyncId}
          >
            {showSessionColors && (
              <defs>
                <linearGradient id={sessionGradientId} x1="0" y1="0" x2="1" y2="0">
                  {sessionGradientStops(chartDataBase, color).map((s, i) => (
                    <stop key={i} offset={s.offset} stopColor={s.color} />
                  ))}
                </linearGradient>
              </defs>
            )}
            <XAxis
              dataKey="t"
              tickFormatter={(v) => formatXTick(v as number, period)}
              tick={AXIS_TICK}
              tickCount={tickCount}
              interval="preserveStartEnd"
              minTickGap={50}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={AXIS_TICK}
              tickFormatter={(v) => `$${(v as number).toFixed(0)}`}
              width={42}
              axisLine={false}
              tickLine={false}
            />
            <RechartsTooltip
              cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }}
              content={lineTooltipContent}
            />
            <Line
              type="monotone"
              dataKey="v"
              stroke={showSessionColors ? `url(#${sessionGradientId})` : color}
              dot={false}
              strokeWidth={1.5}
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
            <Bar dataKey="newsY" shape={(p: React.ComponentProps<typeof NewsMarkerShape>) => <NewsMarkerShape {...p} onSelect={setActiveNews} />} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* Painéis auxiliares (confirmação/momento) -- cada um só aparece se
          ligado em "Indicadores"; só o mais embaixo mostra datas no eixo X. */}
      {visual !== "tradingview" && showVolume && (
        <div className="mt-1">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">Volume</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={volumeRows} margin={{ top: 0, right: 4, bottom: 2, left: 4 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="t"
                tickFormatter={lastSubpanel === "volume" ? (v) => formatXTick(v as number, period) : undefined}
                tick={lastSubpanel === "volume" ? AXIS_TICK : false}
                tickCount={tickCount}
                interval="preserveStartEnd"
                minTickGap={50}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                domain={[0, "dataMax"]}
                tick={AXIS_TICK}
                tickFormatter={(v) => fmtVolumeShort(v as number)}
                width={42}
                axisLine={false}
                tickLine={false}
              />
              <RechartsTooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <Bar dataKey={volumeKey} fill="#52525b" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {visual !== "tradingview" && showRsi && (
        <div className="mt-1">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">IFR (RSI 14)</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={rsiRows} margin={{ top: 2, right: 4, bottom: 2, left: 4 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="t"
                tickFormatter={lastSubpanel === "rsi" ? (v) => formatXTick(v as number, period) : undefined}
                tick={lastSubpanel === "rsi" ? AXIS_TICK : false}
                tickCount={tickCount}
                interval="preserveStartEnd"
                minTickGap={50}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                domain={[0, 100]}
                ticks={[30, 70]}
                tick={AXIS_TICK}
                width={42}
                axisLine={false}
                tickLine={false}
              />
              <RechartsTooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <ReferenceLine y={70} stroke="#f87171" strokeDasharray="3 3" />
              <ReferenceLine y={30} stroke="#4ade80" strokeDasharray="3 3" />
              <Line dataKey="rsi" stroke="#facc15" dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {visual !== "tradingview" && showMacd && (
        <div className="mt-1">
          <div className="text-[11px] font-mono text-zinc-300 mb-0.5">MACD (12,26,9)</div>
          <ResponsiveContainer width="100%" height={subpanelHeight}>
            <ComposedChart data={macdRows} margin={{ top: 2, right: 4, bottom: 2, left: 4 }} syncId={priceChartSyncId}>
              <XAxis
                dataKey="t"
                tickFormatter={lastSubpanel === "macd" ? (v) => formatXTick(v as number, period) : undefined}
                tick={lastSubpanel === "macd" ? AXIS_TICK : false}
                tickCount={tickCount}
                interval="preserveStartEnd"
                minTickGap={50}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={AXIS_TICK}
                width={42}
                axisLine={false}
                tickLine={false}
              />
              <RechartsTooltip cursor={{ stroke: CROSSHAIR_STROKE, strokeDasharray: "3 3" }} content={() => null} />
              <ReferenceLine y={0} stroke="#3f3f46" />
              <Bar dataKey="macdHistPos" fill="#4ade80" isAnimationActive={false} />
              <Bar dataKey="macdHistNeg" fill="#f87171" isAnimationActive={false} />
              <Line dataKey="macdLine" stroke={INDICATOR_COLORS.macdLine} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
              <Line dataKey="macdSignal" stroke={INDICATOR_COLORS.macdSignal} dot={false} strokeWidth={1.25} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Menu de botão direito no gráfico próprio -- "criar alerta neste
          preço", direto pro preço sob o cursor no momento do clique. */}
      {chartMenu && (
        <div
          className="fixed z-[60] min-w-[220px] rounded-md border border-border bg-card shadow-lg py-1 font-mono text-xs"
          style={{ left: chartMenu.x, top: chartMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-muted-foreground border-b border-border/50">
            {ticker} · ${chartMenu.price.toFixed(2)}
          </div>
          <button
            type="button"
            className="w-full text-left px-3 py-1.5 hover:bg-secondary transition-colors flex items-center gap-1.5"
            onClick={() => {
              navigate(`/alerts?symbol=${ticker}&price=${chartMenu.price.toFixed(2)}&condition=above`);
              setChartMenu(null);
            }}
          >
            🔔 Alerta se subir acima de <span className="text-green-400 font-bold">${chartMenu.price.toFixed(2)}</span>
          </button>
          <button
            type="button"
            className="w-full text-left px-3 py-1.5 hover:bg-secondary transition-colors flex items-center gap-1.5"
            onClick={() => {
              navigate(`/alerts?symbol=${ticker}&price=${chartMenu.price.toFixed(2)}&condition=below`);
              setChartMenu(null);
            }}
          >
            🔔 Alerta se cair abaixo de <span className="text-red-400 font-bold">${chartMenu.price.toFixed(2)}</span>
          </button>
        </div>
      )}

      {/* Caixa de notícias — abre ao tocar no marcador amarelo. Fica aberta até
          fechar (ao contrário do tooltip de hover, que some no celular). */}
      <Dialog open={activeNews != null} onOpenChange={(o) => { if (!o) setActiveNews(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-base">📰 Notícias — {ticker}</DialogTitle>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto space-y-4 pr-1">
            {(activeNews ?? []).map((n, i) => (
              <div key={i} className="border-b border-border/40 pb-3 last:border-0 last:pb-0">
                <p className="text-sm font-semibold text-foreground leading-snug">{n.title}</p>
                {(n.source || fmtNewsDate(n.published)) && (
                  <p className="text-xs text-muted-foreground font-mono mt-1">
                    {[n.source, fmtNewsDate(n.published)].filter(Boolean).join(" · ")}
                  </p>
                )}
                {n.summary && (
                  <p className="text-sm text-muted-foreground mt-1.5 leading-snug">{n.summary}</p>
                )}
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Allocation donut chart ────────────────────────────────────────────────────

const ALLOC_COLORS = ["#6366f1", "#8b5cf6", "#a78bfa", "#818cf8", "#4f46e5", "#4338ca", "#7c3aed"];

interface AllocEntry { name: string; value: number }

function AllocationChart({ data }: { data: AllocEntry[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  if (!total) return null;

  return (
    <Card className="border-border bg-card">
      <CardContent className="p-4">
        <div className="text-xs font-mono text-muted-foreground uppercase tracking-wide mb-3">Alocação atual</div>
        <div className="flex items-center gap-6">
          <ResponsiveContainer width={120} height={120}>
            <PieChart>
              <Pie data={data} cx="50%" cy="50%" innerRadius={36} outerRadius={56} dataKey="value" strokeWidth={0}>
                {data.map((_, i) => <Cell key={i} fill={ALLOC_COLORS[i % ALLOC_COLORS.length]} />)}
              </Pie>
              <RechartsTooltip
                formatter={(value) => [fmt$(value as number), ""]}
                contentStyle={{ background: "#09090b", border: "1px solid #27272a", borderRadius: "6px", fontSize: "11px", fontFamily: "monospace" }}
                labelStyle={{ color: "#a1a1aa" }}
                itemStyle={{ color: "#e4e4e7" }}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-col gap-1.5 min-w-0">
            {data.map((d, i) => (
              <div key={d.name} className="flex items-center gap-2 text-[11px] font-mono">
                <div className="h-2 w-2 rounded-full flex-shrink-0" style={{ background: ALLOC_COLORS[i % ALLOC_COLORS.length] }} />
                <span className="text-foreground font-semibold">{d.name}</span>
                <span className="text-muted-foreground ml-auto pl-4 tabular-nums">
                  {((d.value / total) * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Purchases sub-table ───────────────────────────────────────────────────────

function PurchasesRow({ positionId, ticker, currentPrice }: { positionId: number; ticker: string; currentPrice: number }) {
  const { viewMode } = useViewMode();
  const isMobile = viewMode === "mobile";
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: purchases = [], isLoading } = useListPortfolioPurchases(positionId);
  const deletePurchase = useDeletePortfolioPurchase();
  const createPurchase = useCreatePortfolioPurchase();

  const [addOpen, setAddOpen] = useState(false);
  const [purchaseDate, setPurchaseDate] = useState("");
  const [purchasePrice, setPurchasePrice] = useState("");
  const [purchaseQty, setPurchaseQty] = useState("");
  const [amount, setAmount] = useState("");
  // Qual campo o usuario preencheu por ultimo (preco x quantidade) -- decide
  // qual e' a fonte de verdade ao salvar. null = nenhum (usa estimativa).
  const [addLastField, setAddLastField] = useState<"price" | "qty" | null>(null);

  const [saleOpen, setSaleOpen] = useState(false);
  const [salePurchaseId, setSalePurchaseId] = useState<number | null>(null);
  const [saleDate, setSaleDate] = useState("");
  const [salePrice, setSalePrice] = useState("");

  const [editOpen, setEditOpen] = useState(false);
  const [editPurchaseId, setEditPurchaseId] = useState<number | null>(null);
  const [editAmount, setEditAmount] = useState("");
  const [editPrice, setEditPrice] = useState("");
  const [editQty, setEditQty] = useState("");
  const [editLastField, setEditLastField] = useState<"price" | "qty">("price");
  const [editSaving, setEditSaving] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: getListPortfolioPurchasesQueryKey(positionId) });

  const handleDelete = (purchaseId: number) => {
    deletePurchase.mutate({ purchaseId }, {
      onSuccess: invalidate,
      onError: () => toast({ variant: "destructive", title: "Erro ao remover compra" }),
    });
  };

  const handleAdd = async () => {
    if (!purchaseDate || !amount) return;
    const amt = parseFloat(amount);
    // Fonte de verdade: se o usuário informou a quantidade da corretora, o
    // preço exato é amount/quantidade; se informou o preço, usa direto. Em
    // ambos os casos é um dado real, então marca priceManuallyEdited para
    // "Corrigir preços reais" não sobrescrever depois.
    let price: number | null = null;
    let manual = false;
    if (addLastField === "qty" && purchaseQty) {
      const q = parseFloat(purchaseQty);
      if (q > 0) { price = amt / q; manual = true; }
    } else if (purchasePrice) {
      price = parseFloat(purchasePrice);
      manual = true;
    }
    // Sem preço nem quantidade informados: busca o fechamento estimado da data
    // (yfinance). Fica corrigível pelo backfill -- não é dado real da corretora.
    if (price == null) {
      try {
        const r = await fetch(`/api/portfolio/historical-price?ticker=${encodeURIComponent(ticker)}&date=${purchaseDate}`, { credentials: "include" });
        const data = await r.json();
        if (r.ok && data.price != null) price = data.price;
      } catch { /* segue sem preço */ }
    }
    createPurchase.mutate(
      { id: positionId, data: { purchaseDate, amount: amt, purchasePrice: price, priceManuallyEdited: manual } },
      {
        onSuccess: () => { invalidate(); setAddOpen(false); setPurchaseDate(""); setPurchasePrice(""); setPurchaseQty(""); setAmount(""); setAddLastField(null); },
        onError: () => toast({ variant: "destructive", title: "Erro ao adicionar compra" }),
      },
    );
  };

  const [backfilling, setBackfilling] = useState(false);
  const handleBackfill = async (force: boolean) => {
    setBackfilling(true);
    try {
      const r = await fetch(`/api/portfolio/${positionId}/backfill-prices`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
        credentials: "include",
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Falhou");
      invalidate();
      const skippedNote = data.skippedManual > 0
        ? ` ${data.skippedManual} mantida(s) por já ter preço editado manualmente.`
        : "";
      toast({
        title: data.updated > 0 ? "✅ Preços corrigidos" : "Nada a corrigir",
        description: (data.updated > 0
          ? `${data.updated} compra(s) atualizada(s) com o preço real do dia.`
          : "Nenhuma compra precisou de ajuste.") + skippedNote,
      });
    } catch (e) {
      toast({ variant: "destructive", title: "Erro ao corrigir preços", description: String(e) });
    } finally {
      setBackfilling(false);
    }
  };

  const handleEditOpen = (purchase: { id: number; amount: number; purchasePrice?: number | null }) => {
    setEditPurchaseId(purchase.id);
    setEditAmount(String(purchase.amount));
    setEditPrice(purchase.purchasePrice != null ? String(purchase.purchasePrice) : "");
    setEditQty(purchase.purchasePrice && purchase.amount ? String(purchase.amount / purchase.purchasePrice) : "");
    setEditLastField("price");
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!editPurchaseId || !editAmount) return;
    // Se o usuário digitou a quantidade da corretora, o preço exato é
    // amount/quantidade; senão usa o preço digitado.
    const amt = parseFloat(editAmount);
    let price: number | null = null;
    if (editLastField === "qty" && editQty) {
      const q = parseFloat(editQty);
      price = q > 0 ? amt / q : null;
    } else {
      price = editPrice ? parseFloat(editPrice) : null;
    }
    setEditSaving(true);
    try {
      const r = await fetch(`/api/portfolio/purchases/${editPurchaseId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: amt,
          purchasePrice: price,
        }),
        credentials: "include",
      });
      if (!r.ok) throw new Error("Falhou");
      invalidate();
      setEditOpen(false);
      toast({ title: "Compra atualizada" });
    } catch {
      toast({ variant: "destructive", title: "Erro ao atualizar compra" });
    } finally {
      setEditSaving(false);
    }
  };

  const handleSaleOpen = (purchaseId: number) => {
    setSalePurchaseId(purchaseId);
    setSaleDate(new Date().toISOString().split("T")[0]);
    setSalePrice(currentPrice > 0 ? currentPrice.toFixed(2) : "");
    setSaleOpen(true);
  };

  const handleSaleSave = async () => {
    if (!salePurchaseId || !saleDate || !salePrice) return;
    try {
      await fetch(`/api/portfolio/purchases/${salePurchaseId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ saleDate, salePrice: parseFloat(salePrice) }),
        credentials: "include",
      });
      invalidate();
      setSaleOpen(false);
      toast({ title: "Venda registrada" });
    } catch {
      toast({ variant: "destructive", title: "Erro ao registrar venda" });
    }
  };

  // Totais
  const totalInvested = purchases.reduce((s, p) => s + p.amount, 0);
  const totalSold = purchases.filter(p => p.salePrice && p.saleDate).reduce((s, p) => {
    const qty = p.purchasePrice ? p.amount / p.purchasePrice : 0;
    return s + (qty * (p.salePrice ?? 0));
  }, 0);
  const totalRealizedPnl = purchases.filter(p => p.salePrice && p.purchasePrice).reduce((s, p) => {
    const qty = p.amount / p.purchasePrice!;
    return s + (qty * ((p.salePrice ?? 0) - p.purchasePrice!));
  }, 0);
  const openForPnl = purchases.filter(p => !p.saleDate && p.purchasePrice);
  const totalUnrealizedPnl = currentPrice > 0 && openForPnl.length > 0
    ? openForPnl.reduce((s, p) => {
        const qty = p.amount / p.purchasePrice!;
        return s + (qty * (currentPrice - p.purchasePrice!));
      }, 0)
    : null;

  // Calculado uma vez só e reaproveitado nas duas apresentações (tabela
  // desktop e cards mobile) -- evita duplicar a lógica de P&L em dois lugares
  // que podem divergir com o tempo.
  const enrichedPurchases = purchases.map((p) => {
    const qty = p.purchasePrice ? p.amount / p.purchasePrice : null;
    const isSold = !!p.saleDate && !!p.salePrice;
    const unrealizedPnl = !isSold && qty && currentPrice > 0 && p.purchasePrice
      ? qty * (currentPrice - p.purchasePrice)
      : null;
    const unrealizedPct = unrealizedPnl != null && p.amount > 0 ? (unrealizedPnl / p.amount) * 100 : null;
    const realizedPnl = isSold && qty && p.purchasePrice && p.salePrice
      ? qty * (p.salePrice - p.purchasePrice)
      : null;
    const realizedPct = realizedPnl != null && p.amount > 0 ? (realizedPnl / p.amount) * 100 : null;
    return { p, qty, isSold, unrealizedPnl, unrealizedPct, realizedPnl, realizedPct };
  });

  const body = (
    <>
      <div className="text-[10px] font-mono font-semibold text-muted-foreground mb-3 uppercase tracking-widest">
        Operações — {ticker}
      </div>

          {isLoading ? (
            <div className="text-xs text-muted-foreground font-mono">Carregando...</div>
          ) : isMobile ? (
            <div className="space-y-2">
              {enrichedPurchases.length === 0 && (
                <div className="text-xs text-muted-foreground font-mono italic">Nenhuma compra registrada.</div>
              )}
              {enrichedPurchases.map(({ p, qty, isSold, unrealizedPnl, unrealizedPct, realizedPnl, realizedPct }) => (
                <div key={p.id} className="border border-border/30 rounded p-3 text-xs font-mono space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold inline-flex items-center gap-1">
                      {p.priceManuallyEdited && (
                        <span title="Preço confirmado manualmente — não é sobrescrito por 'Corrigir preços reais'">
                          <Lock className="h-2.5 w-2.5 text-muted-foreground" />
                        </span>
                      )}
                      {p.purchaseDate}
                    </span>
                    <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold",
                      isSold ? "bg-muted text-muted-foreground" : "bg-green-500/10 text-green-400")}>
                      {isSold ? "Fechada" : "Aberta"}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
                    <div>
                      <div className="text-muted-foreground text-[10px] uppercase">Preço compra</div>
                      <div className="tabular-nums">{p.purchasePrice ? `$${p.purchasePrice.toFixed(2)}` : "—"}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground text-[10px] uppercase">Preço atual</div>
                      <div className="tabular-nums">{currentPrice > 0 ? <span className="text-blue-400">${currentPrice.toFixed(2)}</span> : "—"}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground text-[10px] uppercase">Total invest.</div>
                      <div className="tabular-nums">{fmt$(p.amount)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground text-[10px] uppercase">Valor atual</div>
                      <div className="tabular-nums font-semibold">
                        {!isSold && qty && currentPrice > 0 ? <span className="text-blue-400">{fmt$(qty * currentPrice)}</span> : "—"}
                      </div>
                    </div>
                    <div className="col-span-2">
                      <div className="text-muted-foreground text-[10px] uppercase">Lucro/Perda atual</div>
                      <div className={cn("tabular-nums font-semibold",
                        unrealizedPnl == null ? "text-muted-foreground" : unrealizedPnl >= 0 ? "text-green-400" : "text-red-400")}>
                        {unrealizedPnl != null
                          ? `${unrealizedPnl >= 0 ? "+" : "-"}${fmt$(unrealizedPnl)} (${unrealizedPct! >= 0 ? "+" : ""}${unrealizedPct!.toFixed(2)}%)`
                          : isSold ? "vendida" : "—"}
                      </div>
                    </div>
                    {isSold && (
                      <>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Data venda</div>
                          <div>{p.saleDate ?? "—"}</div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Preço venda</div>
                          <div className="tabular-nums">{p.salePrice ? `$${p.salePrice.toFixed(2)}` : "—"}</div>
                        </div>
                        <div className="col-span-2">
                          <div className="text-muted-foreground text-[10px] uppercase">Lucro/Perda venda</div>
                          <div className={cn("tabular-nums font-semibold",
                            realizedPnl == null ? "text-muted-foreground" : realizedPnl >= 0 ? "text-green-400" : "text-red-400")}>
                            {realizedPnl != null
                              ? `${realizedPnl >= 0 ? "+" : "-"}${fmt$(realizedPnl)} (${realizedPct! >= 0 ? "+" : ""}${realizedPct!.toFixed(2)}%)`
                              : "—"}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                  <div className="flex gap-1.5 pt-1">
                    {!isSold ? (
                      <Button size="sm" variant="outline"
                        className="h-6 px-2 text-[10px] font-mono text-green-400 border-green-500/30 hover:bg-green-500/10"
                        onClick={() => handleSaleOpen(p.id)}
                      >
                        Vender
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline"
                        className="h-6 px-2 text-[10px] font-mono text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
                        onClick={async () => {
                          await fetch(`/api/portfolio/purchases/${p.id}`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ saleDate: null, salePrice: null }),
                            credentials: "include",
                          });
                          invalidate();
                        }}
                      >
                        ↩ Desfazer
                      </Button>
                    )}
                    <Button size="sm" variant="ghost"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                      onClick={() => handleEditOpen(p)}
                      title="Editar valor/preço de compra"
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button size="sm" variant="ghost"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                      onClick={() => handleDelete(p.id)}
                      disabled={deletePurchase.isPending}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              ))}
              {enrichedPurchases.length > 0 && (
                <div className="border border-border/30 rounded p-3 text-xs font-mono bg-muted/20 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground text-[10px] uppercase">Total investido</span>
                    <span className="tabular-nums font-semibold">{fmt$(totalInvested)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground text-[10px] uppercase">P&amp;L aberto</span>
                    <span className={cn("tabular-nums font-semibold",
                      totalUnrealizedPnl == null ? "text-muted-foreground" : totalUnrealizedPnl >= 0 ? "text-green-400" : "text-red-400")}>
                      {totalUnrealizedPnl != null ? `${totalUnrealizedPnl >= 0 ? "+" : "-"}${fmt$(totalUnrealizedPnl)}` : "—"}
                    </span>
                  </div>
                  {totalSold > 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground text-[10px] uppercase">Total vendido</span>
                      <span className="tabular-nums">{fmt$(totalSold)}</span>
                    </div>
                  )}
                  {totalRealizedPnl !== 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground text-[10px] uppercase">P&amp;L realizado</span>
                      <span className={cn("tabular-nums font-semibold", totalRealizedPnl > 0 ? "text-green-400" : "text-red-400")}>
                        {totalRealizedPnl >= 0 ? "+" : "-"}{fmt$(totalRealizedPnl)}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono border border-border/30 rounded">
                <thead>
                  <tr className="bg-muted/30 text-muted-foreground text-[10px] uppercase tracking-wide">
                    <th className="text-left px-3 py-2">Data Compra</th>
                    <th className="text-right px-3 py-2">Preço Compra</th>
                    <th className="text-right px-3 py-2">Preço Atual</th>
                    <th className="text-right px-3 py-2">Valor Atual</th>
                    <th className="text-right px-3 py-2">Total Invest.</th>
                    <th className="text-right px-3 py-2">Lucro/Perda Atual</th>
                    <th className="text-right px-3 py-2">Data Venda</th>
                    <th className="text-right px-3 py-2">Preço Venda</th>
                    <th className="text-right px-3 py-2">Lucro/Perda Venda</th>
                    <th className="text-right px-3 py-2">Status</th>
                    <th className="px-2 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {purchases.length === 0 && (
                    <tr>
                      <td colSpan={10} className="py-3 px-3 text-muted-foreground italic">Nenhuma compra registrada.</td>
                    </tr>
                  )}
                  {enrichedPurchases.map(({ p, qty, isSold, unrealizedPnl, unrealizedPct, realizedPnl, realizedPct }) => {
                    return (
                      <tr key={p.id} className="border-t border-border/20 hover:bg-muted/10">
                        <td className="px-3 py-2 font-semibold">{p.purchaseDate}</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {p.purchasePrice ? (
                            <span className="inline-flex items-center gap-1 justify-end">
                              {p.priceManuallyEdited && (
                                <span title="Preço confirmado manualmente — não é sobrescrito por 'Corrigir preços reais'">
                                  <Lock className="h-2.5 w-2.5 text-muted-foreground" />
                                </span>
                              )}
                              {`$${p.purchasePrice.toFixed(2)}`}
                            </span>
                          ) : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {currentPrice > 0
                            ? <span className="text-blue-400">${currentPrice.toFixed(2)}</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums font-semibold">
                          {!isSold && qty && currentPrice > 0
                            ? <span className="text-blue-400">{fmt$(qty * currentPrice)}</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmt$(p.amount)}</td>

                        {/* Lucro/perda atual */}
                        <td className={cn("px-3 py-2 text-right tabular-nums font-semibold",
                          unrealizedPnl == null ? "text-muted-foreground"
                          : unrealizedPnl >= 0 ? "text-green-400" : "text-red-400"
                        )}>
                          {unrealizedPnl != null ? (
                            <span>{unrealizedPnl >= 0 ? "+" : "-"}{fmt$(unrealizedPnl)}<br/>
                            <span className="text-[10px] font-normal">{unrealizedPct! >= 0 ? "+" : ""}{unrealizedPct!.toFixed(2)}%</span></span>
                          ) : isSold ? <span className="text-muted-foreground text-[10px]">vendida</span> : "—"}
                        </td>

                        {/* Data venda */}
                        <td className="px-3 py-2 text-right">
                          {p.saleDate ?? <span className="text-muted-foreground">—</span>}
                        </td>

                        {/* Preço venda */}
                        <td className="px-3 py-2 text-right tabular-nums">
                          {p.salePrice ? `$${p.salePrice.toFixed(2)}` : <span className="text-muted-foreground">—</span>}
                        </td>

                        {/* Lucro/perda venda */}
                        <td className={cn("px-3 py-2 text-right tabular-nums font-semibold",
                          realizedPnl == null ? "text-muted-foreground"
                          : realizedPnl >= 0 ? "text-green-400" : "text-red-400"
                        )}>
                          {realizedPnl != null ? (
                            <span>{realizedPnl >= 0 ? "+" : "-"}{fmt$(realizedPnl)}<br/>
                            <span className="text-[10px] font-normal">{realizedPct! >= 0 ? "+" : ""}{realizedPct!.toFixed(2)}%</span></span>
                          ) : "—"}
                        </td>

                        {/* Status */}
                        <td className="px-3 py-2 text-right">
                          <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold",
                            isSold ? "bg-muted text-muted-foreground" : "bg-green-500/10 text-green-400"
                          )}>
                            {isSold ? "Fechada" : "Aberta"}
                          </span>
                        </td>

                        <td className="px-2 py-2">
                          <div className="flex gap-1">
                            {!isSold ? (
                              <Button size="sm" variant="outline"
                                className="h-5 px-2 text-[10px] font-mono text-green-400 border-green-500/30 hover:bg-green-500/10"
                                onClick={() => handleSaleOpen(p.id)}
                              >
                                Vender
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline"
                                className="h-5 px-2 text-[10px] font-mono text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
                                onClick={async () => {
                                  await fetch(`/api/portfolio/purchases/${p.id}`, {
                                    method: "PATCH",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ saleDate: null, salePrice: null }),
                                    credentials: "include",
                                  });
                                  invalidate();
                                }}
                              >
                                ↩ Desfazer
                              </Button>
                            )}
                            <Button size="sm" variant="ghost"
                              className="h-5 w-5 p-0 text-muted-foreground hover:text-foreground"
                              onClick={() => handleEditOpen(p)}
                              title="Editar valor/preço de compra"
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button size="sm" variant="ghost"
                              className="h-5 w-5 p-0 text-muted-foreground hover:text-destructive"
                              onClick={() => handleDelete(p.id)}
                              disabled={deletePurchase.isPending}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>

                {/* Linha de totais */}
                {purchases.length > 0 && (
                  <tfoot>
                    <tr className="border-t-2 border-border bg-muted/20 font-semibold">
                      <td className="px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">TOTAL</td>
                      <td className="px-3 py-2" />
                      <td className="px-3 py-2" />
                      <td className="px-3 py-2" />
                      <td className="px-3 py-2 text-right tabular-nums">{fmt$(totalInvested)}</td>
                      <td className={cn("px-3 py-2 text-right tabular-nums",
                        totalUnrealizedPnl == null ? "text-muted-foreground"
                        : totalUnrealizedPnl >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {totalUnrealizedPnl != null
                          ? `${totalUnrealizedPnl >= 0 ? "+" : "-"}${fmt$(totalUnrealizedPnl)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2" />
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {totalSold > 0 ? fmt$(totalSold) : "—"}
                      </td>
                      <td className={cn("px-3 py-2 text-right tabular-nums",
                        totalRealizedPnl === 0 ? "text-muted-foreground"
                        : totalRealizedPnl > 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {totalRealizedPnl !== 0
                          ? `${totalRealizedPnl >= 0 ? "+" : "-"}${fmt$(totalRealizedPnl)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2" colSpan={2} />
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}

          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <Button size="sm" variant="outline" className="h-6 text-[11px] font-mono"
              onClick={() => setAddOpen(true)}>
              <Plus className="h-3 w-3 mr-1" />
              Adicionar compra
            </Button>
            <Button size="sm" variant="outline"
              className="h-6 text-[11px] font-mono text-blue-400 border-blue-500/30 hover:bg-blue-500/10"
              onClick={() => handleBackfill(true)}
              disabled={backfilling}
              title="Busca o preço real de fechamento de cada data (yfinance) e substitui o preço de compra"
            >
              <RefreshCw className={cn("h-3 w-3 mr-1", backfilling && "animate-spin")} />
              {backfilling ? "Corrigindo..." : "Corrigir preços reais"}
            </Button>
          </div>
          <PriceChart ticker={ticker} />
    </>
  );

  return (
    <>
      {isMobile ? (
        <div className="px-4 py-4 bg-muted/20 border-b border-border/50 rounded-b-md">{body}</div>
      ) : (
        <tr>
          <td colSpan={13} className="px-6 py-4 bg-muted/20 border-b border-border/50">{body}</td>
        </tr>
      )}

      {/* Dialog — Nova compra */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">Nova compra — {ticker}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-xs font-mono">Data da compra</Label>
              <Input type="date" value={purchaseDate} onChange={(e) => setPurchaseDate(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">Total investido ($)</Label>
              <Input type="number" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">Quantidade (corretora)</Label>
              <Input type="number" placeholder="0.00000" value={purchaseQty} onChange={(e) => { setPurchaseQty(e.target.value); setAddLastField("qty"); }} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">ou Preço no dia da compra ($)</Label>
              <Input type="number" placeholder="865.00" value={purchasePrice} onChange={(e) => { setPurchasePrice(e.target.value); setAddLastField("price"); }} className="font-mono text-xs h-8" />
            </div>
            {(() => {
              const amt = parseFloat(amount);
              let price: number | null = null;
              let qty: number | null = null;
              if (addLastField === "qty" && parseFloat(purchaseQty) > 0) {
                qty = parseFloat(purchaseQty);
                price = amt > 0 ? amt / qty : null;
              } else if (parseFloat(purchasePrice) > 0) {
                price = parseFloat(purchasePrice);
                qty = amt > 0 ? amt / price : null;
              }
              return price != null && qty != null ? (
                <div className="rounded border border-border bg-muted/20 px-3 py-2 space-y-1 font-mono text-xs">
                  <div className="flex justify-between"><span className="text-muted-foreground">Preço</span><span className="font-semibold">${price.toFixed(2)}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Qtde</span><span className="font-semibold">{qty.toFixed(5)}</span></div>
                </div>
              ) : (
                <p className="text-[10px] text-muted-foreground font-mono leading-tight">
                  Informe a quantidade da corretora (recomendado) ou o preço. Deixe ambos em branco para estimar pelo fechamento do dia.
                </p>
              );
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setAddOpen(false)} className="font-mono text-xs">Cancelar</Button>
            <Button size="sm" onClick={handleAdd} disabled={!purchaseDate || !amount || createPurchase.isPending} className="font-mono text-xs">
              {createPurchase.isPending ? "Salvando..." : "Salvar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog — Registrar venda */}
      <Dialog open={saleOpen} onOpenChange={setSaleOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">Registrar venda — {ticker}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-xs font-mono">Data da venda</Label>
              <Input type="date" value={saleDate} onChange={(e) => setSaleDate(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">Preço de venda ($)</Label>
              <Input type="number" placeholder="0.00" value={salePrice} onChange={(e) => setSalePrice(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            {(() => {
              const purchase = salePurchaseId ? purchases.find(p => p.id === salePurchaseId) : null;
              const qty = purchase?.purchasePrice ? purchase.amount / purchase.purchasePrice : null;
              const salePriceNum = parseFloat(salePrice);
              const saleTotal = qty && !isNaN(salePriceNum) ? qty * salePriceNum : null;
              const purchaseCost = purchase?.amount ?? null;
              const pnl = saleTotal != null && purchaseCost != null ? saleTotal - purchaseCost : null;
              return saleTotal != null ? (
                <div className="rounded border border-border bg-muted/20 px-3 py-2 space-y-1 font-mono text-xs">
                  <div className="flex justify-between text-muted-foreground">
                    <span>Qtde</span>
                    <span>{qty?.toFixed(5)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Valor da venda</span>
                    <span className="font-semibold">${saleTotal.toFixed(2)}</span>
                  </div>
                  {pnl != null && (
                    <div className="flex justify-between border-t border-border pt-1">
                      <span className="text-muted-foreground">Lucro/Perda</span>
                      <span className={pnl >= 0 ? "text-green-400 font-semibold" : "text-red-400 font-semibold"}>
                        {pnl >= 0 ? "+" : "-"}${Math.abs(pnl).toFixed(2)}
                        {purchaseCost ? ` (${((pnl / purchaseCost) * 100).toFixed(1)}%)` : ""}
                      </span>
                    </div>
                  )}
                </div>
              ) : null;
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setSaleOpen(false)} className="font-mono text-xs">Cancelar</Button>
            <Button size="sm" onClick={handleSaleSave} disabled={!saleDate || !salePrice} className="font-mono text-xs">
              Confirmar venda
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog — Editar valor/preço de compra */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">Editar compra — {ticker}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-xs font-mono">Valor investido ($)</Label>
              <Input type="number" placeholder="0.00" value={editAmount} onChange={(e) => setEditAmount(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">Quantidade (corretora)</Label>
              <Input type="number" placeholder="0.00000" value={editQty} onChange={(e) => { setEditQty(e.target.value); setEditLastField("qty"); }} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">ou Preço de compra real ($)</Label>
              <Input type="number" placeholder="0.00" value={editPrice} onChange={(e) => { setEditPrice(e.target.value); setEditLastField("price"); }} className="font-mono text-xs h-8" />
            </div>
            {(() => {
              const amt = parseFloat(editAmount);
              let price: number | null = null;
              let qty: number | null = null;
              if (editLastField === "qty" && parseFloat(editQty) > 0) {
                qty = parseFloat(editQty);
                price = amt > 0 ? amt / qty : null;
              } else if (parseFloat(editPrice) > 0) {
                price = parseFloat(editPrice);
                qty = amt > 0 ? amt / price : null;
              }
              return price != null && qty != null ? (
                <div className="rounded border border-border bg-muted/20 px-3 py-2 space-y-1 font-mono text-xs">
                  <div className="flex justify-between"><span className="text-muted-foreground">Preço</span><span className="font-semibold">${price.toFixed(2)}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Qtde</span><span className="font-semibold">{qty.toFixed(5)}</span></div>
                </div>
              ) : null;
            })()}
            <p className="text-[10px] text-muted-foreground font-mono leading-tight">
              Dica: digite a <span className="text-foreground">Quantidade</span> exatamente como aparece na corretora — o preço exato é calculado a partir do valor investido.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setEditOpen(false)} className="font-mono text-xs">Cancelar</Button>
            <Button size="sm" onClick={handleEditSave} disabled={!editAmount || editSaving} className="font-mono text-xs">
              Salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── Position form dialog ──────────────────────────────────────────────────────

interface PositionDialogProps {
  open: boolean;
  onClose: () => void;
  editing?: PortfolioPosition;
  onSaved: () => void;
  isSimulated?: boolean;
}

function PositionDialog({ open, onClose, editing, onSaved, isSimulated }: PositionDialogProps) {
  const { toast } = useToast();
  const { user } = useAuth();
  const qc = useQueryClient();
  const createPos = useCreatePortfolioPosition();
  const updatePos = useUpdatePortfolioPosition();
  const createPurchase = useCreatePortfolioPurchase();
  const [form, setForm] = useState<PosForm>(editing ? posToForm(editing) : EMPTY_FORM);

  useEffect(() => {
    if (open) setForm(editing ? posToForm(editing) : EMPTY_FORM);
  }, [open, editing]);

  const upd =
    (k: keyof PosForm) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  const effectiveNotifyEmail = form.notifyEmail || user?.email || "";

  const handleSave = () => {
    const payload = {
      ticker: form.ticker.trim().toUpperCase(),
      quantity: parseFloat(form.quantity),
      avgCost: parseFloat(form.avgCost),
      investedAmount: parseFloat(form.investedAmount),
      dividends: form.dividends ? parseFloat(form.dividends) : 0,
      isEtf: form.isEtf,
      firstPurchaseDate: form.firstPurchaseDate,
      notes: form.notes || undefined,
      downAlertPcts: parseAlertPcts(form.downAlertPcts),
      upAlertPcts: parseAlertPcts(form.upAlertPcts),
      ...(effectiveNotifyEmail.trim() ? { notifyEmail: effectiveNotifyEmail.trim() } : {}),
      ...(isSimulated && !editing ? { isSimulated: true } : {}),
    };

    if (!payload.ticker || isNaN(payload.quantity) || isNaN(payload.avgCost) || isNaN(payload.investedAmount) || !payload.firstPurchaseDate) {
      toast({ variant: "destructive", title: "Preencha todos os campos obrigatórios" });
      return;
    }

    if (editing) {
      // Envia SOMENTE os campos que o usuário alterou em relação ao estado
      // com que o formulário abriu. Mandar o payload inteiro fazia um form
      // semeado com dados velhos (ex.: cache) sobrescrever campos que o
      // usuário nem tocou — ETF/dividendos "sumindo" ao salvar outra coisa.
      const orig = posToForm(editing);
      const diff: Record<string, unknown> = {};
      if (form.ticker.trim().toUpperCase() !== orig.ticker.trim().toUpperCase()) diff.ticker = payload.ticker;
      if (form.quantity !== orig.quantity) diff.quantity = payload.quantity;
      if (form.avgCost !== orig.avgCost) diff.avgCost = payload.avgCost;
      if (form.investedAmount !== orig.investedAmount) diff.investedAmount = payload.investedAmount;
      if (form.dividends !== orig.dividends) diff.dividends = payload.dividends;
      if (form.isEtf !== orig.isEtf) diff.isEtf = payload.isEtf;
      if (form.firstPurchaseDate !== orig.firstPurchaseDate) diff.firstPurchaseDate = payload.firstPurchaseDate;
      if (form.notes !== orig.notes) diff.notes = form.notes || undefined;
      if (form.downAlertPcts !== orig.downAlertPcts) diff.downAlertPcts = payload.downAlertPcts;
      if (form.upAlertPcts !== orig.upAlertPcts) diff.upAlertPcts = payload.upAlertPcts;
      if (form.notifyEmail !== orig.notifyEmail && effectiveNotifyEmail.trim()) diff.notifyEmail = effectiveNotifyEmail.trim();

      if (Object.keys(diff).length === 0) { onClose(); return; }

      updatePos.mutate(
        { id: editing.id, data: diff },
        { onSuccess: () => { onSaved(); onClose(); }, onError: () => toast({ variant: "destructive", title: "Erro ao atualizar posição" }) },
      );
    } else {
      createPos.mutate(
        { data: payload },
        {
          onSuccess: (created) => {
            // Cria o primeiro lote de compra junto, com os mesmos dados que
            // o usuário já preencheu aqui (data/valor/preço) -- sem isso a
            // posição nascia com totais agregados mas "Operações" vazio,
            // obrigando a duplicar os mesmos números em "Adicionar compra"
            // pra registrar até uma compra de 1 lote só.
            createPurchase.mutate(
              {
                id: created.id,
                data: {
                  purchaseDate: payload.firstPurchaseDate,
                  amount: payload.investedAmount,
                  purchasePrice: payload.avgCost,
                  priceManuallyEdited: true,
                },
              },
              {
                onSuccess: () => qc.invalidateQueries({ queryKey: getListPortfolioPurchasesQueryKey(created.id) }),
                onError: () => toast({ variant: "destructive", title: "Posição criada, mas falhou ao registrar o primeiro lote de compra — adicione manualmente em Operações." }),
              },
            );
            onSaved();
            onClose();
          },
          onError: () => toast({ variant: "destructive", title: "Erro ao criar posição" }),
        },
      );
    }
  };

  const isPending = createPos.isPending || updatePos.isPending;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">
            {editing ? `Editar ${editing.ticker}` : "Nova posição"}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-mono">Ticker *</Label>
              <Input
                value={form.ticker}
                onChange={upd("ticker")}
                placeholder="NVDA"
                className="font-mono text-xs h-8 uppercase"
              />
            </div>
            <div>
              <Label className="text-xs font-mono">Quantidade *</Label>
              <Input
                type="number"
                value={form.quantity}
                onChange={upd("quantity")}
                placeholder="0.00000"
                className="font-mono text-xs h-8"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-mono">Custo médio ($) *</Label>
              <Input
                type="number"
                value={form.avgCost}
                onChange={upd("avgCost")}
                placeholder="0.00"
                className="font-mono text-xs h-8"
              />
            </div>
            <div>
              <Label className="text-xs font-mono">Total investido ($) *</Label>
              <Input
                type="number"
                value={form.investedAmount}
                onChange={upd("investedAmount")}
                placeholder="0.00"
                className="font-mono text-xs h-8"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-mono">Primeira compra *</Label>
              <Input
                type="date"
                value={form.firstPurchaseDate}
                onChange={upd("firstPurchaseDate")}
                className="font-mono text-xs h-8"
              />
            </div>
            <div>
              <Label className="text-xs font-mono">Dividendos recebidos ($)</Label>
              <Input
                type="number"
                value={form.dividends}
                onChange={upd("dividends")}
                placeholder="0.00"
                className="font-mono text-xs h-8"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <Checkbox
              checked={form.isEtf}
              onCheckedChange={(v) => setForm((f) => ({ ...f, isEtf: v === true }))}
            />
            <span className="text-xs font-mono">É ETF / fundo (separa de "ações" no Patrimônio)</span>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-mono">Alertas baixa (%)</Label>
              <Input
                value={form.downAlertPcts}
                onChange={upd("downAlertPcts")}
                placeholder="10,15,20,30"
                className="font-mono text-xs h-8"
              />
            </div>
            <div>
              <Label className="text-xs font-mono">Alertas alta (%)</Label>
              <Input
                value={form.upAlertPcts}
                onChange={upd("upAlertPcts")}
                placeholder="15,20,30,40"
                className="font-mono text-xs h-8"
              />
            </div>
          </div>
          <div>
            <Label className="text-xs font-mono">E-mail de notificação</Label>
            <Input
              type="email"
              value={effectiveNotifyEmail}
              onChange={upd("notifyEmail")}
              placeholder={user?.email ?? "seu@email.com"}
              className="font-mono text-xs h-8"
            />
          </div>
          <div>
            <Label className="text-xs font-mono">Notas</Label>
            <Textarea
              value={form.notes}
              onChange={upd("notes")}
              rows={2}
              className="font-mono text-xs resize-none"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onClose} className="font-mono text-xs">
            Cancelar
          </Button>
          <Button size="sm" onClick={handleSave} disabled={isPending} className="font-mono text-xs">
            {isPending ? "Salvando..." : "Salvar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { viewMode } = useViewMode();
  const isMobile = viewMode === "mobile";
  const [mode, setMode] = useState<"real" | "simulated">("real");
  // "total" inclui pré/pós-mercado na Var $/% e no card Var. hoje; "regular"
  // usa só o pregão regular, igual ao "ganho do dia" que a corretora mostra.
  const [varMode, setVarMode] = useState<"total" | "regular">("total");

  const { data: allPositions = [], isLoading } = useListPortfolioPositions();
  const positions = (allPositions as Array<typeof allPositions[0] & { isSimulated?: boolean }>)
    .filter((p) => mode === "simulated" ? p.isSimulated : !p.isSimulated);
  const { data: quotes = [] } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 60_000, staleTime: 55_000 },
  });

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  // undefined = dialog closed, null = new position, PortfolioPosition = editing
  const [dialogTarget, setDialogTarget] = useState<PortfolioPosition | null | undefined>(undefined);
  const deletePos = useDeletePortfolioPosition();

  // Caixa disponível (USD não investido) — persistido no banco por modo
  const [cashByMode, setCashByMode] = useState<CashByMode>({ real: 0, simulated: 0 });
  const [cashLoadFailed, setCashLoadFailed] = useState(false);
  const [editingCash, setEditingCash] = useState(false);
  const [cashDraft, setCashDraft] = useState("");
  useEffect(() => {
    fetchCash().then((c) => {
      if (c) setCashByMode(c);
      else setCashLoadFailed(true);
    });
  }, []);
  const [fxRate, setFxRate] = useState<number | null>(null);
  useEffect(() => { fetchFxRate().then(setFxRate); }, []);
  useEffect(() => { setEditingCash(false); }, [mode]);
  const cash = mode === "real" ? cashByMode.real : cashByMode.simulated;
  const commitCash = async () => {
    // Campo vazio/inválido: cancela sem salvar, em vez de zerar o saldo real
    // por engano (ex.: campo limpo sem querer antes de confirmar).
    if (cashDraft.trim() === "") { setEditingCash(false); return; }
    const n = parseFloat(cashDraft);
    if (isNaN(n) || n < 0) {
      toast({ variant: "destructive", title: "Valor de caixa inválido", description: "Nada foi salvo." });
      return;
    }
    const val = n;
    setCashByMode((prev) => ({ ...prev, [mode]: val }));
    setEditingCash(false);
    try {
      await persistCash(mode, val);
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Erro ao salvar caixa",
        description: e instanceof Error ? e.message : undefined,
      });
      fetchCash().then((c) => { if (c) setCashByMode(c); });
    }
  };

  // Preço atual da posição: usa pré/pós-mercado quando existir (ver
  // pickExtendedPrice), senão cai pro último preço do pregão regular.
  const priceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const q of quotes as Array<{ symbol: string; price?: number | null } & ExtendedQuoteFields>) {
      const ext = pickExtendedPrice(q);
      m.set(q.symbol, ext?.price ?? q.price ?? 0);
    }
    return m;
  }, [quotes]);
  // Var. $/% tem duas variantes por ticker: "total" acompanha priceMap (usa
  // preço estendido contra o fechamento anterior quando existir -- inclui o
  // movimento de pré/pós-mercado) e "regular" usa regularMarketPrice (campo
  // explícito do Yahoo pro preço do pregão regular). Importante: NÃO dá pra
  // usar fast_info.last_price (o "price"/"change" que o servidor manda) como
  // "só regular" -- na prática ele já reflete o último trade mesmo fora do
  // pregão, então ficava contaminado com o mesmo movimento estendido do modo
  // "total". regularMarketPrice separa isso de verdade. Fail-open pro
  // change/changePct do servidor se o campo não vier. O toggle varMode
  // escolhe qual variante alimenta as colunas Var $/% e o card Var. hoje.
  const changeMap = useMemo(() => {
    const m = new Map<string, { change: number | null; changePct: number | null; regularChange: number | null; regularChangePct: number | null }>();
    for (const q of quotes as Array<{ symbol: string; change?: number | null; changePct?: number | null; previousClose?: number | null; regularMarketPrice?: number | null } & ExtendedQuoteFields>) {
      const ext = pickExtendedPrice(q);
      const hasRegular = q.regularMarketPrice != null && q.previousClose != null && q.previousClose !== 0;
      const regularChange = hasRegular ? q.regularMarketPrice! - q.previousClose! : (q.change ?? null);
      const regularChangePct = hasRegular ? ((q.regularMarketPrice! - q.previousClose!) / q.previousClose!) * 100 : (q.changePct ?? null);
      if (ext && q.previousClose != null && q.previousClose !== 0) {
        m.set(q.symbol, {
          change: ext.price - q.previousClose,
          changePct: ((ext.price - q.previousClose) / q.previousClose) * 100,
          regularChange,
          regularChangePct,
        });
      } else {
        m.set(q.symbol, { change: regularChange, changePct: regularChangePct, regularChange, regularChangePct });
      }
    }
    return m;
  }, [quotes]);
  // Badge informativo: só a variação dentro da própria sessão estendida
  // (pré ou pós), separado da variação total (já embutida em changeMap)
  const extMap = useMemo(() => {
    const m = new Map<string, { label: string; pct: number }>();
    for (const q of quotes as Array<{ symbol: string; preMarketChangePct?: number | null; postMarketChangePct?: number | null } & ExtendedQuoteFields>) {
      const ext = pickExtendedPrice(q);
      if (!ext) continue;
      const pct = ext.label === "Pré" ? q.preMarketChangePct : q.postMarketChangePct;
      if (pct != null) m.set(q.symbol, { label: ext.label, pct });
    }
    return m;
  }, [quotes]);

  // Fetch purchases for all positions to detect fully-sold ones
  const purchasesQueries = useQueries({
    queries: positions.map((p) => ({
      queryKey: getListPortfolioPurchasesQueryKey(p.id),
      queryFn: () => listPortfolioPurchases(p.id),
      staleTime: 60_000,
    })),
  });

  const soldPositionIds = useMemo(() => {
    const ids = new Set<number>();
    positions.forEach((p, i) => {
      const data = purchasesQueries[i]?.data ?? [];
      if (data.length > 0 && data.every((pur) => !!pur.saleDate && !!pur.salePrice)) {
        ids.add(p.id);
      }
    });
    return ids;
  }, [positions, purchasesQueries]);

  const purchasesMap = useMemo(() => {
    const m = new Map<number, typeof purchasesQueries[0]["data"]>();
    positions.forEach((p, i) => { m.set(p.id, purchasesQueries[i]?.data ?? []); });
    return m;
  }, [positions, purchasesQueries]);

  const allRows = useMemo(() => {
    const derivedAll = positions.map((p) => derivePosition(p, purchasesMap.get(p.id) ?? []));
    // Valores por linha ficam na moeda da posição (R$ para B3);
    // os campos *Usd alimentam os agregados em dólar
    const toUsd = (v: number, brl: boolean) => (brl && fxRate ? v / fxRate : v);
    const totalInvestedUsd = positions.reduce(
      (s, p, i) => s + toUsd(derivedAll[i].invested, isB3(p.ticker)), 0,
    );
    return positions.map((p, i) => {
      const d = derivedAll[i];
      const quantity = d.quantity;
      const invested = d.invested;
      const avgCost = d.avgCost;
      const isBrl = isB3(p.ticker);
      const hasPrice = priceMap.has(p.ticker);
      const price = priceMap.get(p.ticker) ?? 0;
      const entry = changeMap.get(p.ticker);
      const qChange = (varMode === "total" ? entry?.change : entry?.regularChange) ?? null;
      const qChangePct = (varMode === "total" ? entry?.changePct : entry?.regularChangePct) ?? null;
      const currentValue = hasPrice ? quantity * price : 0;
      const pnlDollar = hasPrice ? currentValue - invested : 0;
      const pnlPct = hasPrice && invested > 0 ? (pnlDollar / invested) * 100 : 0;
      const dailyChange = hasPrice && qChange != null ? quantity * qChange : null;
      const dailyChangePct = qChangePct;
      const investedUsd = toUsd(invested, isBrl);
      const currentValueUsd = hasPrice ? toUsd(currentValue, isBrl) : 0;
      const dailyChangeUsd = dailyChange != null ? toUsd(dailyChange, isBrl) : null;
      const weight = totalInvestedUsd > 0 ? (investedUsd / totalInvestedUsd) * 100 : 0;
      const downAlert = hasPrice ? getMaxDownAlert(pnlPct, p.downAlertPcts) : null;
      const upAlert = hasPrice ? getMaxUpAlert(pnlPct, p.upAlertPcts) : null;
      const is30d = daysSince(p.firstPurchaseDate) >= 30;
      const isSoldOut = soldPositionIds.has(p.id);
      return { pos: p, quantity, invested, avgCost, price, currentValue, pnlDollar, pnlPct, dailyChange, dailyChangePct, weight, downAlert, upAlert, is30d, isSoldOut, isBrl, investedUsd, currentValueUsd, dailyChangeUsd };
    });
  }, [positions, priceMap, changeMap, varMode, soldPositionIds, purchasesMap, fxRate]);

  const rows = useMemo(() => allRows.filter((r) => !r.isSoldOut), [allRows]);
  const soldRows = useMemo(() => allRows.filter((r) => r.isSoldOut), [allRows]);

  // Agregados sempre em USD (posições B3 convertidas pelo câmbio)
  const totals = useMemo(() => {
    const invested = rows.reduce((s, r) => s + r.investedUsd, 0);
    const current = rows.reduce((s, r) => s + r.currentValueUsd, 0);
    // Valor atual separado por tipo (ETF/fundo vs ação) pro Patrimônio total.
    const etfsCurrent = rows.reduce((s, r) => s + (r.pos.isEtf ? r.currentValueUsd : 0), 0);
    const stocksCurrent = current - etfsCurrent;
    const pnl = current - invested;
    const pnlPct = invested > 0 && current > 0 ? (pnl / invested) * 100 : 0;
    const dailyChange = rows.some((r) => r.dailyChangeUsd != null)
      ? rows.reduce((s, r) => s + (r.dailyChangeUsd ?? 0), 0)
      : null;
    return { invested, current, stocksCurrent, etfsCurrent, pnl, pnlPct, dailyChange };
  }, [rows]);

  // Lucro realizado das ações vendidas (proceeds já estão refletidos no caixa).
  const realized = useMemo(() => {
    let proceeds = 0;
    let pnl = 0;
    let invested = 0;
    for (const p of positions) {
      const brl = isB3(p.ticker);
      const conv = (v: number) => (brl && fxRate ? v / fxRate : v);
      const purchases = purchasesMap.get(p.id) ?? [];
      for (const pur of purchases) {
        if (pur.saleDate && pur.salePrice && pur.purchasePrice) {
          const qty = pur.amount / pur.purchasePrice;
          proceeds += conv(qty * pur.salePrice);
          pnl += conv(qty * (pur.salePrice - pur.purchasePrice));
          invested += conv(pur.amount);
        }
      }
    }
    return { proceeds, pnl, invested };
  }, [positions, purchasesMap, fxRate]);

  // Dividendos recebidos (informados manualmente por posição), somados em USD.
  const totalDividends = useMemo(() => {
    return positions.reduce((s, p) => {
      const brl = isB3(p.ticker);
      const conv = (v: number) => (brl && fxRate ? v / fxRate : v);
      return s + conv(p.dividends ?? 0);
    }, 0);
  }, [positions, fxRate]);

  // Patrimônio total = posições abertas (valor atual) + caixa + dividendos.
  // Espelha o "Patrimônio total" da corretora somado aos proventos recebidos.
  const netWorth = totals.current + cash + totalDividends;
  // P&L combinado = não realizado (abertas) + realizado (vendas) + dividendos.
  const combinedPnl = totals.pnl + realized.pnl + totalDividends;
  const combinedInvested = totals.invested + realized.invested;
  const combinedPnlPct = combinedInvested > 0 ? (combinedPnl / combinedInvested) * 100 : 0;

  const hasPrices = quotes.length > 0;

  const allocData = useMemo(
    () => rows.filter((r) => r.currentValueUsd > 0).map((r) => ({ name: r.pos.ticker, value: r.currentValueUsd })),
    [rows],
  );
  const hasBrl = useMemo(() => allRows.some((r) => r.isBrl), [allRows]);

  const toggleExpand = (id: number) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const handleDelete = (id: number, ticker: string) => {
    if (!confirm(`Remover ${ticker} da carteira?`)) return;
    deletePos.mutate(
      { id },
      {
        onSuccess: () => qc.invalidateQueries({ queryKey: getListPortfolioPositionsQueryKey() }),
        onError: () => toast({ variant: "destructive", title: "Erro ao remover posição" }),
      },
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono tracking-tight flex items-center gap-2">
            <Wallet className="h-6 w-6 text-primary" />
            Carteira
          </h1>
          <p className="text-xs text-muted-foreground font-mono mt-1">Posições, P&amp;L e alertas</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Aba Real / Simulado */}
          <div className="flex border border-border rounded-md overflow-hidden text-xs font-mono font-bold">
            <button
              onClick={() => setMode("real")}
              className={`px-3 py-1.5 transition-colors ${mode === "real" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary"}`}
            >
              REAL
            </button>
            <button
              onClick={() => setMode("simulated")}
              className={`px-3 py-1.5 transition-colors border-l border-border ${mode === "simulated" ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" : "text-muted-foreground hover:bg-secondary"}`}
            >
              PAPER
            </button>
          </div>
          <Button size="sm" className="font-mono text-xs" onClick={() => setDialogTarget(null)}>
            <Plus className="h-3.5 w-3.5 mr-1" />
            Nova posição
          </Button>
        </div>
      </div>
      {mode === "simulated" && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-yellow-500/30 bg-yellow-500/5 text-yellow-400 text-xs font-mono">
          <Activity className="h-3.5 w-3.5 shrink-0" />
          Modo Paper Trading — operações simuladas sem dinheiro real
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Investido</span>
              <DollarSign className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="text-xl font-bold font-mono tabular-nums">{fmt$(totals.invested)}</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Valor atual</span>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="text-xl font-bold font-mono tabular-nums">
              {hasPrices && totals.current > 0 ? fmt$(totals.current) : "—"}
            </div>
          </CardContent>
        </Card>
        {/* Caixa disponível (editável) */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Caixa (disponível)</span>
              {!editingCash && (
                <button
                  onClick={() => { setCashDraft(cash ? String(cash) : ""); setEditingCash(true); }}
                  className="text-muted-foreground hover:text-foreground"
                  title="Editar saldo em dólar não investido"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            {editingCash ? (
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  autoFocus
                  value={cashDraft}
                  onChange={(e) => setCashDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitCash(); if (e.key === "Escape") setEditingCash(false); }}
                  placeholder="0.00"
                  className="font-mono text-sm h-8"
                />
                <Button size="sm" className="h-8 px-2 text-xs font-mono" onClick={commitCash}>OK</Button>
              </div>
            ) : (
              <div className="text-xl font-bold font-mono tabular-nums">{fmt$(cash)}</div>
            )}
            {cashLoadFailed && !editingCash && (
              <div className="text-[10px] font-mono text-yellow-400 mt-0.5">
                Falha ao carregar o caixa salvo — recarregue a página antes de editar.
              </div>
            )}
          </CardContent>
        </Card>
        {/* Patrimônio total = valor atual + caixa -- ocupa a linha inteira no
            mobile (col-span-2): tem 5 linhas de valor, os outros cards do
            grid só têm 1, então espremer isso em meia largura estourava o
            card (números cortados fora da borda). */}
        <Card className="border-primary/40 bg-card col-span-2 md:col-span-1">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-primary uppercase tracking-wide">Patrimônio total</span>
              <Wallet className="h-4 w-4 text-primary" />
            </div>
            <div className="font-mono tabular-nums space-y-0.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wide">Ações</span>
                <span className="text-xl font-bold">{hasPrices && totals.current > 0 ? fmt$(totals.stocksCurrent) : "—"}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wide">ETFs</span>
                <span className="text-xl font-bold">{hasPrices && totals.current > 0 ? fmt$(totals.etfsCurrent) : "—"}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wide">Dividendos</span>
                <span className="text-xl font-bold">{fmt$(totalDividends)}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wide">Caixa</span>
                <span className="text-xl font-bold">{fmt$(cash)}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2 border-t border-primary/30 pt-1 mt-1">
                <span className="text-[10px] text-primary uppercase tracking-wide">Total</span>
                <span className="text-xl font-bold text-primary">
                  {hasPrices && totals.current > 0 ? fmt$(netWorth) : fmt$(cash + totalDividends)}
                </span>
              </div>
            </div>
            {hasBrl && (
              <div className={cn("text-[10px] font-mono mt-0.5", fxRate ? "text-muted-foreground" : "text-yellow-400")}>
                {fxRate
                  ? `posições B3 convertidas a R$ ${fxRate.toFixed(2)}/US$`
                  : "câmbio indisponível — valores B3 sem conversão"}
              </div>
            )}
          </CardContent>
        </Card>
        {/* P&L aberto (posições não vendidas) */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">P&amp;L aberto ($)</span>
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              hasPrices && totals.current > 0
                ? totals.pnl >= 0 ? "text-green-400" : "text-red-400"
                : ""
            )}>
              {hasPrices && totals.current > 0
                ? `${totals.pnl >= 0 ? "+" : "-"}${fmt$(totals.pnl)}`
                : "—"}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
              {hasPrices && totals.current > 0 ? fmtPct(totals.pnlPct) : ""}
            </div>
          </CardContent>
        </Card>
        {/* Lucro realizado (ações vendidas) */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Lucro realizado ($)</span>
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              realized.pnl > 0 ? "text-green-400" : realized.pnl < 0 ? "text-red-400" : ""
            )}>
              {realized.proceeds > 0
                ? `${realized.pnl >= 0 ? "+" : "-"}${fmt$(realized.pnl)}`
                : "—"}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
              {realized.proceeds > 0 ? `recebido ${fmt$(realized.proceeds)}` : ""}
            </div>
          </CardContent>
        </Card>
        {/* Dividendos recebidos (informados manualmente por posição) */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Dividendos ($)</span>
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              totalDividends > 0 ? "text-green-400" : ""
            )}>
              {totalDividends > 0 ? `+${fmt$(totalDividends)}` : "—"}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
              soma no patrimônio e P&amp;L total
            </div>
          </CardContent>
        </Card>
        {/* P&L total = aberto + realizado + dividendos */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">P&amp;L total ($)</span>
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              hasPrices && totals.current > 0
                ? combinedPnl >= 0 ? "text-green-400" : "text-red-400"
                : ""
            )}>
              {hasPrices && totals.current > 0
                ? `${combinedPnl >= 0 ? "+" : "-"}${fmt$(combinedPnl)}`
                : "—"}
            </div>
            <div className={cn("text-[10px] font-mono mt-0.5",
              hasPrices && totals.current > 0 ? (combinedPnlPct >= 0 ? "text-green-400" : "text-red-400") : "text-muted-foreground"
            )}>
              {hasPrices && totals.current > 0 ? fmtPct(combinedPnlPct) : ""}
            </div>
          </CardContent>
        </Card>
        {/* Var. hoje */}
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">Var. hoje</span>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              hasPrices && totals.dailyChange != null
                ? totals.dailyChange >= 0 ? "text-green-400" : "text-red-400"
                : ""
            )}>
              {hasPrices && totals.dailyChange != null
                ? `${totals.dailyChange >= 0 ? "+" : "-"}${fmt$(totals.dailyChange)}`
                : "—"}
            </div>
            <div className="flex gap-1 mt-2" title="Total inclui pré/pós-mercado; Regular é só o pregão, igual a maioria das corretoras">
              <button
                onClick={() => setVarMode("total")}
                className={cn("px-1.5 py-0.5 rounded text-[9px] font-mono uppercase border transition-colors",
                  varMode === "total" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:bg-secondary")}
              >
                Total
              </button>
              <button
                onClick={() => setVarMode("regular")}
                className={cn("px-1.5 py-0.5 rounded text-[9px] font-mono uppercase border transition-colors",
                  varMode === "regular" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:bg-secondary")}
              >
                Regular
              </button>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Allocation chart */}
      {allocData.length > 0 && <AllocationChart data={allocData} />}

      {/* Positions table */}
      <Card className="border-border bg-card overflow-hidden">
        <CardContent className="p-0">
          {isMobile ? (
            <div className="divide-y divide-border">
              {isLoading && (
                <div className="py-10 text-center text-muted-foreground text-xs font-mono">Carregando...</div>
              )}
              {!isLoading && rows.length === 0 && (
                <div className="py-10 text-center text-muted-foreground text-xs font-mono">Nenhuma posição cadastrada.</div>
              )}
              {rows.map(({ pos, quantity, invested, price, currentValue, pnlDollar, pnlPct, weight, downAlert, upAlert, is30d, isBrl }) => {
                const expanded = expandedIds.has(pos.id);
                const hasPrice = price > 0;
                const pnlPos = pnlPct >= 0;
                return (
                  <Fragment key={pos.id}>
                    <div className={cn("p-3", expanded && "bg-muted/10")}>
                      <div className="flex items-start justify-between gap-2">
                        <button
                          onClick={() => toggleExpand(pos.id)}
                          className="flex items-center gap-1.5 text-left flex-1 min-w-0 flex-wrap"
                        >
                          {expanded
                            ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
                          <span className="font-semibold text-foreground text-sm">{pos.ticker}</span>
                          {isBrl && (
                            <Badge className="h-4 px-1 text-[9px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                              BRL
                            </Badge>
                          )}
                          {is30d && (
                            <Badge className="h-4 px-1 text-[9px] font-mono bg-amber-500/20 text-amber-400 border border-amber-500/30">
                              30d+
                            </Badge>
                          )}
                          {(() => {
                            const ext = extMap.get(pos.ticker);
                            if (!ext) return null;
                            const up = ext.pct >= 0;
                            return (
                              <Badge
                                className={cn("h-4 px-1 text-[9px] font-mono border",
                                  up ? "bg-green-500/15 text-green-400 border-green-500/30" : "bg-red-500/15 text-red-400 border-red-500/30")}
                              >
                                {ext.label} {up ? "▲+" : "▼"}{ext.pct.toFixed(1)}%
                              </Badge>
                            );
                          })()}
                        </button>
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                            onClick={() => setDialogTarget(pos)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                            onClick={() => handleDelete(pos.id, pos.ticker)}
                            disabled={deletePos.isPending}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                      {pos.notes && (
                        <div className="text-[10px] text-muted-foreground truncate mt-0.5 ml-5">{pos.notes}</div>
                      )}

                      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-2 text-xs font-mono ml-5">
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Preço atual</div>
                          <div className="font-semibold text-blue-400 tabular-nums">
                            {hasPrice ? fmtMoney(price, isBrl) : "—"}
                            {extMap.has(pos.ticker) && (
                              <span className="ml-1 text-[9px] text-muted-foreground font-normal lowercase">{extMap.get(pos.ticker)!.label}</span>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Qtde</div>
                          <div className="tabular-nums">{fmtQty(quantity)}</div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Investido</div>
                          <div className="tabular-nums">{fmtMoney(invested, isBrl)}</div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">Valor atual</div>
                          <div className="font-semibold text-blue-400 tabular-nums">{hasPrice ? fmtMoney(currentValue, isBrl) : "—"}</div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">P&amp;L $</div>
                          <div className={cn("font-semibold tabular-nums", hasPrice ? (pnlPos ? "text-green-400" : "text-red-400") : "")}>
                            {hasPrice ? `${pnlDollar >= 0 ? "+" : "-"}${fmtMoney(pnlDollar, isBrl)}` : "—"}
                          </div>
                        </div>
                        <div>
                          <div className="text-muted-foreground text-[10px] uppercase">P&amp;L %</div>
                          <div className={cn("font-semibold tabular-nums", hasPrice ? (pnlPos ? "text-green-400" : "text-red-400") : "")}>
                            {hasPrice ? fmtPct(pnlPct) : "—"}
                          </div>
                        </div>
                        {pos.dividends > 0 && (
                          <div>
                            <div className="text-muted-foreground text-[10px] uppercase">Dividendos</div>
                            <div className="font-semibold text-green-400 tabular-nums">+{fmtMoney(pos.dividends, isBrl)}</div>
                          </div>
                        )}
                      </div>

                      {(downAlert != null || upAlert != null || weight > 0) && (
                        <div className="flex items-center gap-1.5 mt-2 ml-5">
                          {downAlert != null && (
                            <Badge className="h-4 px-1 text-[9px] font-mono bg-red-500/20 text-red-400 border border-red-500/30">
                              -{downAlert}%
                            </Badge>
                          )}
                          {upAlert != null && (
                            <Badge className="h-4 px-1 text-[9px] font-mono bg-green-500/20 text-green-400 border border-green-500/30">
                              +{upAlert}%
                            </Badge>
                          )}
                          <span className="text-[10px] text-muted-foreground ml-auto">Peso {weight.toFixed(1)}%</span>
                        </div>
                      )}
                    </div>
                    {expanded && <PurchasesRow positionId={pos.id} ticker={pos.ticker} currentPrice={price} />}
                  </Fragment>
                );
              })}
            </div>
          ) : (
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-muted-foreground text-[11px]">
                <th className="w-8 py-2.5 pl-3" />
                <th className="text-left py-2.5 pl-1">Ticker</th>
                <th className="text-right pr-3">Qtde</th>
                <th className="text-right pr-3">Custo médio</th>
                <th className="text-right pr-3">Preço atual</th>
                <th className="text-right pr-3">Investido</th>
                <th className="text-right pr-3">Valor atual</th>
                <th className="text-right pr-3" title={varMode === "total" ? "Inclui pré/pós-mercado" : "Só pregão regular"}>Var. $ {varMode === "regular" && "(reg)"}</th>
                <th className="text-right pr-3" title={varMode === "total" ? "Inclui pré/pós-mercado" : "Só pregão regular"}>Var. % {varMode === "regular" && "(reg)"}</th>
                <th className="text-right pr-3">P&amp;L $</th>
                <th className="text-right pr-3">P&amp;L %</th>
                <th className="text-right pr-3">Peso</th>
                <th className="text-right pr-3">Dividendos</th>
                <th className="text-right pr-3">Alertas</th>
                <th className="text-right pr-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={16} className="py-10 text-center text-muted-foreground">
                    Carregando...
                  </td>
                </tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr>
                  <td colSpan={14} className="py-10 text-center text-muted-foreground">
                    Nenhuma posição cadastrada.
                  </td>
                </tr>
              )}
              {rows.map(({ pos, quantity, invested, avgCost, price, currentValue, pnlDollar, pnlPct, dailyChange, dailyChangePct, weight, downAlert, upAlert, is30d, isBrl }) => {
                const expanded = expandedIds.has(pos.id);
                const hasPrice = price > 0;
                const pnlPos = pnlPct >= 0;
                const dayPos = (dailyChangePct ?? 0) >= 0;
                return (
                  <Fragment key={pos.id}>
                  <tr
                    className={cn(
                      "border-b border-border/40 hover:bg-muted/10 transition-colors",
                      expanded && "bg-muted/10",
                    )}
                  >
                    <td className="py-2.5 pl-3">
                      <button
                        onClick={() => toggleExpand(pos.id)}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        {expanded
                          ? <ChevronDown className="h-3.5 w-3.5" />
                          : <ChevronRight className="h-3.5 w-3.5" />}
                      </button>
                    </td>
                    <td className="py-2.5 pl-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-foreground text-sm">{pos.ticker}</span>
                        {isBrl && (
                          <Badge className="h-4 px-1 text-[9px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" title="Posição da B3 — valores da linha em reais; totais convertidos pelo câmbio">
                            BRL
                          </Badge>
                        )}
                        {is30d && (
                          <Badge className="h-4 px-1 text-[9px] font-mono bg-amber-500/20 text-amber-400 border border-amber-500/30">
                            30d+
                          </Badge>
                        )}
                        {(() => {
                          const ext = extMap.get(pos.ticker);
                          if (!ext) return null;
                          const up = ext.pct >= 0;
                          return (
                            <Badge
                              className={cn("h-4 px-1 text-[9px] font-mono border",
                                up ? "bg-green-500/15 text-green-400 border-green-500/30" : "bg-red-500/15 text-red-400 border-red-500/30")}
                              title={`Preço atual já é de ${ext.label === "Pré" ? "pré-mercado" : "after-hours"} -- variação desta sessão estendida`}
                            >
                              {ext.label} {up ? "▲+" : "▼"}{ext.pct.toFixed(1)}%
                            </Badge>
                          );
                        })()}
                      </div>
                      {pos.notes && (
                        <div className="text-[10px] text-muted-foreground truncate max-w-[120px]">{pos.notes}</div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmtQty(quantity)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmtMoney(avgCost, isBrl)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums font-semibold text-blue-400">
                      {hasPrice
                        ? <>
                            {fmtMoney(price, isBrl)}
                            {extMap.has(pos.ticker) && (
                              <span className="ml-1 text-[9px] text-muted-foreground font-normal lowercase">{extMap.get(pos.ticker)!.label}</span>
                            )}
                          </>
                        : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmtMoney(invested, isBrl)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums font-semibold text-blue-400">
                      {hasPrice ? fmtMoney(currentValue, isBrl) : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums", hasPrice && dailyChange != null ? (dayPos ? "text-green-400" : "text-red-400") : "text-muted-foreground")}>
                      {hasPrice && dailyChange != null
                        ? `${dailyChange >= 0 ? "+" : "-"}${fmtMoney(dailyChange, isBrl)}`
                        : "—"}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold", hasPrice && dailyChangePct != null ? (dayPos ? "text-green-400" : "text-red-400") : "text-muted-foreground")}>
                      {hasPrice && dailyChangePct != null
                        ? fmtPct(dailyChangePct)
                        : "—"}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums", hasPrice ? (pnlPos ? "text-green-400" : "text-red-400") : "")}>
                      {hasPrice
                        ? `${pnlDollar >= 0 ? "+" : "-"}${fmtMoney(pnlDollar, isBrl)}`
                        : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold", hasPrice ? (pnlPos ? "text-green-400" : "text-red-400") : "")}>
                      {hasPrice ? fmtPct(pnlPct) : <span className="text-muted-foreground font-normal">—</span>}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-muted-foreground">
                      {weight.toFixed(1)}%
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-green-400">
                      {pos.dividends > 0 ? `+${fmtMoney(pos.dividends, isBrl)}` : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {downAlert != null && (
                          <Badge className="h-4 px-1 text-[9px] font-mono bg-red-500/20 text-red-400 border border-red-500/30">
                            -{downAlert}%
                          </Badge>
                        )}
                        {upAlert != null && (
                          <Badge className="h-4 px-1 text-[9px] font-mono bg-green-500/20 text-green-400 border border-green-500/30">
                            +{upAlert}%
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                          onClick={() => setDialogTarget(pos)}
                        >
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                          onClick={() => handleDelete(pos.id, pos.ticker)}
                          disabled={deletePos.isPending}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                  {expanded && <PurchasesRow positionId={pos.id} ticker={pos.ticker} currentPrice={price} />}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          )}
        </CardContent>
      </Card>

      {/* Ações Vendidas */}
      {soldRows.length > 0 && (
        <Card className="border-border bg-card overflow-hidden">
          <CardContent className="p-0">
            <div className="px-4 py-3 border-b border-border bg-muted/20 flex items-center gap-2">
              <span className="text-xs font-mono font-semibold text-muted-foreground uppercase tracking-widest">
                Ações Vendidas
              </span>
              <Badge className="h-4 px-1.5 text-[9px] font-mono bg-muted text-muted-foreground border border-border">
                {soldRows.length}
              </Badge>
            </div>
            <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-border bg-muted/20 text-muted-foreground text-[11px]">
                  <th className="text-left py-2.5 pl-4">Ticker</th>
                  <th className="text-right pr-3">Investido</th>
                  <th className="text-right pr-3">Preço compra</th>
                  <th className="text-right pr-3">Preço venda</th>
                  <th className="text-right pr-3">Preço atual</th>
                  <th className="text-right pr-3" title="Variação do preço atual vs. o preço no dia da venda. Negativo = mais barata hoje (candidata a recompra)">Var. vs venda</th>
                  <th className="text-right pr-3">Receita total</th>
                  <th className="text-right pr-3">Lucro/Perda</th>
                  <th className="text-right pr-3">Retorno %</th>
                  <th className="text-right pr-3">Data encerr.</th>
                  <th className="pr-3" />
                </tr>
              </thead>
              <tbody>
                {soldRows.map(({ pos, isBrl }) => {
                  const purchases = purchasesMap.get(pos.id) ?? [];
                  const totalInvested = purchases.reduce((s, p) => s + p.amount, 0);
                  // Quantidade e receita das compras efetivamente vendidas
                  const soldLots = purchases.filter((p) => p.saleDate && p.salePrice && p.purchasePrice);
                  const totalSoldQty = soldLots.reduce((s, p) => s + p.amount / (p.purchasePrice as number), 0);
                  const soldInvested = soldLots.reduce((s, p) => s + p.amount, 0);
                  const totalRevenue = soldLots.reduce((s, p) => s + (p.amount / (p.purchasePrice as number)) * (p.salePrice as number), 0);
                  // Preço médio de compra e de venda (ponderados pela quantidade)
                  const avgBuyPrice = totalSoldQty > 0 ? soldInvested / totalSoldQty : null;
                  const avgSalePrice = totalSoldQty > 0 ? totalRevenue / totalSoldQty : null;
                  const curPrice = priceMap.get(pos.ticker) ?? null;
                  // Variação do preço atual vs. preço de venda (negativo = mais barata hoje)
                  const sinceSalePct = avgSalePrice && curPrice ? ((curPrice - avgSalePrice) / avgSalePrice) * 100 : null;
                  const pnl = totalRevenue - totalInvested;
                  const pnlPct = totalInvested > 0 ? (pnl / totalInvested) * 100 : 0;
                  const lastSaleDate = purchases
                    .map((p) => p.saleDate ?? "")
                    .filter(Boolean)
                    .sort()
                    .pop() ?? "—";
                  const expanded = expandedIds.has(pos.id);
                  return (
                    <Fragment key={pos.id}>
                    <tr className={cn("border-b border-border/40 hover:bg-muted/10", expanded && "bg-muted/10")}>
                      <td className="py-2.5 pl-4 font-semibold text-sm text-foreground">
                        <button onClick={() => toggleExpand(pos.id)} className="inline-flex items-center gap-1.5 hover:text-primary">
                          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          {pos.ticker}
                        </button>
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-muted-foreground">{fmtMoney(totalInvested, isBrl)}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums">
                        {avgBuyPrice != null ? fmtMoney(avgBuyPrice, isBrl) : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold">
                        {avgSalePrice != null ? fmtMoney(avgSalePrice, isBrl) : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-blue-400 font-semibold">
                        {curPrice != null ? fmtMoney(curPrice, isBrl) : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold",
                        sinceSalePct == null ? "text-muted-foreground"
                        : sinceSalePct < 0 ? "text-green-400" : "text-red-400"
                      )} title={sinceSalePct != null && sinceSalePct < 0 ? "Mais barata que no dia da venda — candidata a recompra" : undefined}>
                        {sinceSalePct != null
                          ? `${sinceSalePct < 0 ? "▼ " : "▲ +"}${sinceSalePct.toFixed(2)}%`
                          : "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums">{fmtMoney(totalRevenue, isBrl)}</td>
                      <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold",
                        pnl >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {pnl >= 0 ? "+" : "-"}{fmtMoney(pnl, isBrl)}
                      </td>
                      <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold",
                        pnlPct >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                      </td>
                      <td className="py-2.5 pr-3 text-right text-muted-foreground">{lastSaleDate}</td>
                      <td className="py-2.5 pr-3 text-right">
                        <Button size="sm" variant="ghost"
                          className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                          onClick={() => handleDelete(pos.id, pos.ticker)}
                          disabled={deletePos.isPending}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </td>
                    </tr>
                    {expanded && <PurchasesRow positionId={pos.id} ticker={pos.ticker} currentPrice={priceMap.get(pos.ticker) ?? 0} />}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Position dialog */}
      {dialogTarget !== undefined && (
        <PositionDialog
          open
          onClose={() => setDialogTarget(undefined)}
          editing={dialogTarget ?? undefined}
          onSaved={() => qc.invalidateQueries({ queryKey: getListPortfolioPositionsQueryKey() })}
          isSimulated={mode === "simulated"}
        />
      )}
    </div>
  );
}
