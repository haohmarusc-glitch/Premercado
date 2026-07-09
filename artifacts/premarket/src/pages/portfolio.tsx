import { useState, useMemo, useEffect, Fragment, useCallback } from "react";
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
import type { PortfolioPosition } from "@workspace/api-client-react";
import { useAuth } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ChevronDown, ChevronRight, Plus, Pencil, Trash2, TrendingUp, DollarSign, Wallet, Activity, RefreshCw, LineChart as LineChartIcon, CandlestickChart as CandlestickChartIcon, Globe as GlobeIcon, Maximize2, Minimize2, Lock } from "lucide-react";
import { Line, ComposedChart, Bar, ReferenceDot, XAxis, YAxis, ResponsiveContainer, PieChart, Pie, Cell, Tooltip as RechartsTooltip } from "recharts";
import { useGetTickerChart, getGetTickerChartQueryKey, useGetNews, getGetNewsQueryKey } from "@workspace/api-client-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import { CandleShape, toCandleRangeData, candleDomain } from "@/components/candle-shape";
import { attachNewsMarkers, NewsMarkerShape, newsDotShape } from "@/components/news-markers";
import { TradingViewChart } from "@/components/tradingview-chart";
import { useViewMode } from "@/lib/view-mode";

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt$ = (n: number) =>
  `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
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

// ── Position form ─────────────────────────────────────────────────────────────

interface PosForm {
  ticker: string;
  quantity: string;
  avgCost: string;
  investedAmount: string;
  dividends: string;
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

async function fetchCash(): Promise<CashByMode> {
  try {
    const r = await fetch("/api/portfolio/cash", { credentials: "include" });
    if (!r.ok) return { real: 0, simulated: 0 };
    const d = await r.json();
    return { real: Number(d.real ?? 0), simulated: Number(d.simulated ?? 0) };
  } catch {
    return { real: 0, simulated: 0 };
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

function PriceChart({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState<ChartPeriod>("1d");
  const [visual, setVisual] = useState<PortfolioChartVisual>("line");
  // Mesmo tamanho padrao/expandido do grafico do Dashboard (200/420 line-candle,
  // 480/900 TradingView) -- antes ficava fixo em 220/480, sem opcao de expandir.
  const [expanded, setExpanded] = useState(false);
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
  const chartDataBase = candles.map((c) => ({ t: c.t, v: c.c }));
  const isUp = candles.length >= 2
    ? (candles[candles.length - 1]?.c ?? 0) >= (candles[0]?.c ?? 0)
    : true;
  const color = isUp ? "#4ade80" : "#f87171";
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

  return (
    <div className="w-full mt-3">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
          {ticker} — histórico
        </span>
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
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="p-1 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            aria-label={expanded ? "Recolher gráfico" : "Expandir gráfico"}
            title={expanded ? "Recolher gráfico" : "Expandir gráfico"}
          >
            {expanded ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
          </button>
        </div>
      </div>

      {visual === "tradingview" ? (
        <TradingViewChart symbol={ticker} height={expanded ? 900 : 480} />
      ) : isLoading ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          carregando...
        </div>
      ) : !chartData.length ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          sem dados
        </div>
      ) : visual === "candle" ? (
        <ResponsiveContainer width="100%" height={expanded ? 420 : 200}>
          <ComposedChart data={candleData} margin={{ top: 2, right: 4, bottom: 2, left: 4 }}>
            <XAxis
              dataKey="t"
              tickFormatter={(v) => formatXTick(v as number, period)}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "#71717a" }}
              tickCount={tickCount}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={candleDomainRange}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "#71717a" }}
              tickFormatter={(v) => `$${(v as number).toFixed(0)}`}
              width={42}
              axisLine={false}
              tickLine={false}
            />
            <RechartsTooltip
              formatter={(_val: unknown, _name: string, item: { payload?: { o: number; h: number; l: number; c: number } }) => {
                const p = item?.payload;
                if (!p) return ["—", "OHLC"];
                return [`O ${p.o.toFixed(2)} · H ${p.h.toFixed(2)} · L ${p.l.toFixed(2)} · C ${p.c.toFixed(2)}`, ticker];
              }}
              labelFormatter={(label) => {
                const d = new Date(label as number);
                if (period === "1d") return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
                return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
              }}
              contentStyle={{ background: "#09090b", border: "1px solid #27272a", borderRadius: "6px", fontSize: "11px", fontFamily: "monospace" }}
              labelStyle={{ color: "#a1a1aa" }}
              itemStyle={{ color: "#e4e4e7" }}
            />
            <Bar dataKey="range" shape={CandleShape} isAnimationActive={false} />
            {candleNewsMarkers.map((m) => (
              <ReferenceDot
                key={m.t}
                x={m.t}
                y={candleDomainRange[1]}
                ifOverflow="visible"
                shape={newsDotShape(m.newsItems)}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <ResponsiveContainer width="100%" height={expanded ? 420 : 200}>
          <ComposedChart data={chartData} margin={{ top: 2, right: 4, bottom: 2, left: 4 }}>
            <XAxis
              dataKey="t"
              tickFormatter={(v) => formatXTick(v as number, period)}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "#71717a" }}
              tickCount={tickCount}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[min, max]}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "#71717a" }}
              tickFormatter={(v) => `$${(v as number).toFixed(0)}`}
              width={42}
              axisLine={false}
              tickLine={false}
            />
            <RechartsTooltip
              formatter={(value: number, name: string, item: { payload?: { newsItems?: { title: string }[] } }) => {
                if (name === "newsY") {
                  const items = item?.payload?.newsItems ?? [];
                  return [<span style={{ color: "#e4e4e7" }}>{items.map((n) => n.title).join(" · ")}</span>, "📰 Notícia"];
                }
                return [`$${value.toFixed(2)}`, ticker];
              }}
              labelFormatter={(label) => {
                const d = new Date(label as number);
                if (period === "1d") return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
                return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
              }}
              contentStyle={{ background: "#09090b", border: "1px solid #27272a", borderRadius: "6px", fontSize: "11px", fontFamily: "monospace" }}
              labelStyle={{ color: "#a1a1aa" }}
            />
            <Line type="monotone" dataKey="v" stroke={color} dot={false} strokeWidth={1.5} isAnimationActive={false} />
            <Bar dataKey="newsY" shape={NewsMarkerShape} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
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

  const body = (
    <>
      <div className="text-[10px] font-mono font-semibold text-muted-foreground mb-3 uppercase tracking-widest">
        Operações — {ticker}
      </div>

          {isLoading ? (
            <div className="text-xs text-muted-foreground font-mono">Carregando...</div>
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
                  {purchases.map((p) => {
                    const qty = p.purchasePrice ? p.amount / p.purchasePrice : null;
                    const isSold = !!p.saleDate && !!p.salePrice;

                    // Lucro/perda atual (não vendido)
                    const unrealizedPnl = !isSold && qty && currentPrice > 0 && p.purchasePrice
                      ? qty * (currentPrice - p.purchasePrice)
                      : null;
                    const unrealizedPct = unrealizedPnl != null && p.amount > 0
                      ? (unrealizedPnl / p.amount) * 100 : null;

                    // Lucro/perda da venda
                    const realizedPnl = isSold && qty && p.purchasePrice && p.salePrice
                      ? qty * (p.salePrice - p.purchasePrice)
                      : null;
                    const realizedPct = realizedPnl != null && p.amount > 0
                      ? (realizedPnl / p.amount) * 100 : null;

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
  const createPos = useCreatePortfolioPosition();
  const updatePos = useUpdatePortfolioPosition();
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
      updatePos.mutate(
        { id: editing.id, data: payload },
        { onSuccess: () => { onSaved(); onClose(); }, onError: () => toast({ variant: "destructive", title: "Erro ao atualizar posição" }) },
      );
    } else {
      createPos.mutate(
        { data: payload },
        { onSuccess: () => { onSaved(); onClose(); }, onError: () => toast({ variant: "destructive", title: "Erro ao criar posição" }) },
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
  const [editingCash, setEditingCash] = useState(false);
  const [cashDraft, setCashDraft] = useState("");
  useEffect(() => { fetchCash().then(setCashByMode); }, []);
  const [fxRate, setFxRate] = useState<number | null>(null);
  useEffect(() => { fetchFxRate().then(setFxRate); }, []);
  useEffect(() => { setEditingCash(false); }, [mode]);
  const cash = mode === "real" ? cashByMode.real : cashByMode.simulated;
  const commitCash = async () => {
    const n = parseFloat(cashDraft);
    const val = isNaN(n) || n < 0 ? 0 : n;
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
      fetchCash().then(setCashByMode);
    }
  };

  const priceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const q of quotes as Array<{ symbol: string; price?: number | null }>) {
      m.set(q.symbol, q.price ?? 0);
    }
    return m;
  }, [quotes]);
  const changeMap = useMemo(() => {
    const m = new Map<string, { change: number | null; changePct: number | null }>();
    for (const q of quotes as Array<{ symbol: string; change?: number | null; changePct?: number | null }>) {
      m.set(q.symbol, { change: q.change ?? null, changePct: q.changePct ?? null });
    }
    return m;
  }, [quotes]);
  // Pré-mercado / after-hours por ticker (movimento fora do pregão)
  const extMap = useMemo(() => {
    const m = new Map<string, { label: string; pct: number }>();
    for (const q of quotes as Array<{ symbol: string; marketState?: string | null; preMarketPrice?: number | null; preMarketChangePct?: number | null; postMarketPrice?: number | null; postMarketChangePct?: number | null }>) {
      const st = q.marketState ?? "";
      let label: string | null = null;
      let pct: number | null = null;
      if (st.startsWith("PRE") && q.preMarketPrice != null) { label = "Pré"; pct = q.preMarketChangePct ?? null; }
      else if (st.startsWith("POST") && q.postMarketPrice != null) { label = "Pós"; pct = q.postMarketChangePct ?? null; }
      else if (q.postMarketPrice != null) { label = "Pós"; pct = q.postMarketChangePct ?? null; }
      else if (q.preMarketPrice != null) { label = "Pré"; pct = q.preMarketChangePct ?? null; }
      if (label != null && pct != null) m.set(q.symbol, { label, pct });
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
      const qChange = entry?.change ?? null;
      const qChangePct = entry?.changePct ?? null;
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
  }, [positions, priceMap, changeMap, soldPositionIds, purchasesMap, fxRate]);

  const rows = useMemo(() => allRows.filter((r) => !r.isSoldOut), [allRows]);
  const soldRows = useMemo(() => allRows.filter((r) => r.isSoldOut), [allRows]);

  // Agregados sempre em USD (posições B3 convertidas pelo câmbio)
  const totals = useMemo(() => {
    const invested = rows.reduce((s, r) => s + r.investedUsd, 0);
    const current = rows.reduce((s, r) => s + r.currentValueUsd, 0);
    const pnl = current - invested;
    const pnlPct = invested > 0 && current > 0 ? (pnl / invested) * 100 : 0;
    const dailyChange = rows.some((r) => r.dailyChangeUsd != null)
      ? rows.reduce((s, r) => s + (r.dailyChangeUsd ?? 0), 0)
      : null;
    return { invested, current, pnl, pnlPct, dailyChange };
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
          </CardContent>
        </Card>
        {/* Patrimônio total = valor atual + caixa */}
        <Card className="border-primary/40 bg-card">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-primary uppercase tracking-wide">Patrimônio total</span>
              <Wallet className="h-4 w-4 text-primary" />
            </div>
            <div className="text-xl font-bold font-mono tabular-nums text-primary">
              {hasPrices && totals.current > 0 ? fmt$(netWorth) : fmt$(cash + totalDividends)}
            </div>
            <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
              ações {hasPrices ? fmt$(totals.current) : "—"} + caixa {fmt$(cash)}
              {totalDividends > 0 ? ` + dividendos ${fmt$(totalDividends)}` : ""}
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
              hasPrices
                ? combinedPnl >= 0 ? "text-green-400" : "text-red-400"
                : ""
            )}>
              {hasPrices
                ? `${combinedPnl >= 0 ? "+" : "-"}${fmt$(combinedPnl)}`
                : "—"}
            </div>
            <div className={cn("text-[10px] font-mono mt-0.5",
              hasPrices ? (combinedPnlPct >= 0 ? "text-green-400" : "text-red-400") : "text-muted-foreground"
            )}>
              {hasPrices ? fmtPct(combinedPnlPct) : ""}
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
                          <div className="font-semibold text-blue-400 tabular-nums">{hasPrice ? fmtMoney(price, isBrl) : "—"}</div>
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
                <th className="text-right pr-3">Var. $</th>
                <th className="text-right pr-3">Var. %</th>
                <th className="text-right pr-3">P&amp;L $</th>
                <th className="text-right pr-3">P&amp;L %</th>
                <th className="text-right pr-3">Peso</th>
                <th className="text-right pr-3">Alertas</th>
                <th className="text-right pr-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={15} className="py-10 text-center text-muted-foreground">
                    Carregando...
                  </td>
                </tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr>
                  <td colSpan={13} className="py-10 text-center text-muted-foreground">
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
                              title={`Movimento de ${ext.label === "Pré" ? "pré-mercado" : "after-hours"} vs. fechamento`}
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
                      {hasPrice ? fmtMoney(price, isBrl) : <span className="text-muted-foreground">—</span>}
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
