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
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ChevronDown, ChevronRight, Plus, Pencil, Trash2, TrendingUp, DollarSign, Wallet, Activity } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, PieChart, Pie, Cell, Tooltip as RechartsTooltip } from "recharts";
import { useGetTickerChart } from "@workspace/api-client-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt$ = (n: number) =>
  `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
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
  firstPurchaseDate: string;
  notes: string;
  downAlertPcts: string;
  upAlertPcts: string;
}

const EMPTY_FORM: PosForm = {
  ticker: "",
  quantity: "",
  avgCost: "",
  investedAmount: "",
  firstPurchaseDate: "",
  notes: "",
  downAlertPcts: "10,15,20,30",
  upAlertPcts: "10,15,20,30,40,50",
};

function posToForm(p: PortfolioPosition): PosForm {
  return {
    ticker: p.ticker,
    quantity: String(p.quantity),
    avgCost: String(p.avgCost),
    investedAmount: String(p.investedAmount),
    firstPurchaseDate: p.firstPurchaseDate,
    notes: p.notes ?? "",
    downAlertPcts: p.downAlertPcts.join(","),
    upAlertPcts: p.upAlertPcts.join(","),
  };
}

function parseAlertPcts(s: string): number[] {
  return s
    .split(",")
    .map((x) => parseInt(x.trim(), 10))
    .filter((n) => !isNaN(n) && n > 0);
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

function PriceChart({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState<ChartPeriod>("1d");
  const { data, isLoading } = useGetTickerChart(
    { symbol: ticker, period },
    { query: { staleTime: period === "1d" ? 60_000 : 5 * 60_000 } },
  );

  const candles = data?.candles ?? [];
  const chartData = candles.map((c) => ({ t: c.t, v: c.c }));
  const isUp = candles.length >= 2
    ? (candles[candles.length - 1]?.c ?? 0) >= (candles[0]?.c ?? 0)
    : true;
  const color = isUp ? "#4ade80" : "#f87171";
  const min = chartData.length ? Math.min(...chartData.map((d) => d.v)) * 0.998 : 0;
  const max = chartData.length ? Math.max(...chartData.map((d) => d.v)) * 1.002 : 100;
  const tickCount = Math.min(6, candles.length);

  return (
    <div className="w-full mt-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
          {ticker} — histórico
        </span>
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
      </div>

      {isLoading ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          carregando...
        </div>
      ) : !chartData.length ? (
        <div className="h-24 flex items-center justify-center text-[10px] text-muted-foreground font-mono">
          sem dados
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={chartData} margin={{ top: 2, right: 4, bottom: 2, left: 4 }}>
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
              formatter={(value) => [`$${(value as number).toFixed(2)}`, ticker]}
              labelFormatter={(label) => {
                const d = new Date(label as number);
                if (period === "1d") return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
                return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
              }}
              contentStyle={{ background: "#09090b", border: "1px solid #27272a", borderRadius: "6px", fontSize: "11px", fontFamily: "monospace" }}
            />
            <Line type="monotone" dataKey="v" stroke={color} dot={false} strokeWidth={1.5} isAnimationActive={false} />
          </LineChart>
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
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: purchases = [], isLoading } = useListPortfolioPurchases(positionId);
  const deletePurchase = useDeletePortfolioPurchase();
  const createPurchase = useCreatePortfolioPurchase();

  const [addOpen, setAddOpen] = useState(false);
  const [purchaseDate, setPurchaseDate] = useState("");
  const [purchasePrice, setPurchasePrice] = useState("");
  const [amount, setAmount] = useState("");

  const [saleOpen, setSaleOpen] = useState(false);
  const [salePurchaseId, setSalePurchaseId] = useState<number | null>(null);
  const [saleDate, setSaleDate] = useState("");
  const [salePrice, setSalePrice] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: getListPortfolioPurchasesQueryKey(positionId) });

  const handleDelete = (purchaseId: number) => {
    deletePurchase.mutate({ purchaseId }, {
      onSuccess: invalidate,
      onError: () => toast({ variant: "destructive", title: "Erro ao remover compra" }),
    });
  };

  const handleAdd = () => {
    if (!purchaseDate || !amount) return;
    createPurchase.mutate(
      { positionId, data: { purchaseDate, amount: parseFloat(amount), purchasePrice: purchasePrice ? parseFloat(purchasePrice) : null } },
      {
        onSuccess: () => { invalidate(); setAddOpen(false); setPurchaseDate(""); setPurchasePrice(""); setAmount(""); },
        onError: () => toast({ variant: "destructive", title: "Erro ao adicionar compra" }),
      },
    );
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
  const totalUnrealizedPnl = currentPrice > 0
    ? purchases.filter(p => !p.saleDate && p.purchasePrice).reduce((s, p) => {
        const qty = p.amount / p.purchasePrice!;
        return s + (qty * (currentPrice - p.purchasePrice!));
      }, 0)
    : null;

  return (
    <>
      <tr>
        <td colSpan={13} className="px-6 py-4 bg-muted/20 border-b border-border/50">
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
                      <td colSpan={9} className="py-3 px-3 text-muted-foreground italic">Nenhuma compra registrada.</td>
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
                          {p.purchasePrice ? `$${p.purchasePrice.toFixed(2)}` : <span className="text-muted-foreground">—</span>}
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
                            <span>{unrealizedPnl >= 0 ? "+" : ""}{fmt$(unrealizedPnl)}<br/>
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
                            <span>{realizedPnl >= 0 ? "+" : ""}{fmt$(realizedPnl)}<br/>
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
                      <td className="px-3 py-2 text-right tabular-nums">{fmt$(totalInvested)}</td>
                      <td className={cn("px-3 py-2 text-right tabular-nums",
                        totalUnrealizedPnl == null ? "text-muted-foreground"
                        : totalUnrealizedPnl >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {totalUnrealizedPnl != null
                          ? `${totalUnrealizedPnl >= 0 ? "+" : ""}${fmt$(totalUnrealizedPnl)}`
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
                          ? `${totalRealizedPnl >= 0 ? "+" : ""}${fmt$(totalRealizedPnl)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2" colSpan={2} />
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}

          <Button size="sm" variant="outline" className="mt-3 h-6 text-[11px] font-mono"
            onClick={() => setAddOpen(true)}>
            <Plus className="h-3 w-3 mr-1" />
            Adicionar compra
          </Button>
          <PriceChart ticker={ticker} />
        </td>
      </tr>

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
              <Label className="text-xs font-mono">Preço no dia da compra ($)</Label>
              <Input type="number" placeholder="865.00" value={purchasePrice} onChange={(e) => setPurchasePrice(e.target.value)} className="font-mono text-xs h-8" />
            </div>
            <div>
              <Label className="text-xs font-mono">Total investido ($)</Label>
              <Input type="number" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value)} className="font-mono text-xs h-8" />
            </div>
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
                        {pnl >= 0 ? "+" : ""}${Math.abs(pnl).toFixed(2)}
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
    </>
  );
}

// ── Position form dialog ──────────────────────────────────────────────────────

interface PositionDialogProps {
  open: boolean;
  onClose: () => void;
  editing?: PortfolioPosition;
  onSaved: () => void;
}

function PositionDialog({ open, onClose, editing, onSaved }: PositionDialogProps) {
  const { toast } = useToast();
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

  const handleSave = () => {
    const payload = {
      ticker: form.ticker.trim().toUpperCase(),
      quantity: parseFloat(form.quantity),
      avgCost: parseFloat(form.avgCost),
      investedAmount: parseFloat(form.investedAmount),
      firstPurchaseDate: form.firstPurchaseDate,
      notes: form.notes || undefined,
      downAlertPcts: parseAlertPcts(form.downAlertPcts),
      upAlertPcts: parseAlertPcts(form.upAlertPcts),
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
          <div>
            <Label className="text-xs font-mono">Primeira compra *</Label>
            <Input
              type="date"
              value={form.firstPurchaseDate}
              onChange={upd("firstPurchaseDate")}
              className="font-mono text-xs h-8"
            />
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

  const { data: positions = [], isLoading } = useListPortfolioPositions();
  const { data: quotes = [] } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 60_000, staleTime: 55_000 },
  });

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  // undefined = dialog closed, null = new position, PortfolioPosition = editing
  const [dialogTarget, setDialogTarget] = useState<PortfolioPosition | null | undefined>(undefined);
  const deletePos = useDeletePortfolioPosition();

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
    const totalInvested = positions.reduce((s, p) => s + p.investedAmount, 0);
    return positions.map((p) => {
      const price = priceMap.get(p.ticker) ?? 0;
      const entry = changeMap.get(p.ticker);
      const qChange = entry?.change ?? null;
      const qChangePct = entry?.changePct ?? null;
      const currentValue = price > 0 ? p.quantity * price : 0;
      const pnlDollar = price > 0 ? currentValue - p.investedAmount : 0;
      const pnlPct = price > 0 && p.investedAmount > 0 ? (pnlDollar / p.investedAmount) * 100 : 0;
      const dailyChange = price > 0 && qChange != null ? p.quantity * qChange : null;
      const dailyChangePct = qChangePct;
      const weight = totalInvested > 0 ? (p.investedAmount / totalInvested) * 100 : 0;
      const downAlert = price > 0 ? getMaxDownAlert(pnlPct, p.downAlertPcts) : null;
      const upAlert = price > 0 ? getMaxUpAlert(pnlPct, p.upAlertPcts) : null;
      const is30d = daysSince(p.firstPurchaseDate) >= 30;
      const isSoldOut = soldPositionIds.has(p.id);
      return { pos: p, price, currentValue, pnlDollar, pnlPct, dailyChange, dailyChangePct, weight, downAlert, upAlert, is30d, isSoldOut };
    });
  }, [positions, priceMap, changeMap, soldPositionIds]);

  const rows = useMemo(() => allRows.filter((r) => !r.isSoldOut), [allRows]);
  const soldRows = useMemo(() => allRows.filter((r) => r.isSoldOut), [allRows]);

  const totals = useMemo(() => {
    const invested = rows.reduce((s, r) => s + r.pos.investedAmount, 0);
    const current = rows.reduce((s, r) => s + r.currentValue, 0);
    const pnl = current - invested;
    const pnlPct = invested > 0 && current > 0 ? (pnl / invested) * 100 : 0;
    const dailyChange = rows.some((r) => r.dailyChange != null)
      ? rows.reduce((s, r) => s + (r.dailyChange ?? 0), 0)
      : null;
    return { invested, current, pnl, pnlPct, dailyChange };
  }, [rows]);

  const hasPrices = quotes.length > 0;

  const allocData = useMemo(
    () => rows.filter((r) => r.currentValue > 0).map((r) => ({ name: r.pos.ticker, value: r.currentValue })),
    [rows],
  );

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
        <Button size="sm" className="font-mono text-xs" onClick={() => setDialogTarget(null)}>
          <Plus className="h-3.5 w-3.5 mr-1" />
          Nova posição
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-5 gap-3">
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
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">P&amp;L ($)</span>
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
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4">
            <div className="mb-1">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">P&amp;L (%)</span>
            </div>
            <div className={cn("text-xl font-bold font-mono tabular-nums",
              hasPrices && totals.current > 0
                ? totals.pnlPct >= 0 ? "text-green-400" : "text-red-400"
                : ""
            )}>
              {hasPrices && totals.current > 0 ? fmtPct(totals.pnlPct) : "—"}
            </div>
          </CardContent>
        </Card>
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
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-muted-foreground text-[11px]">
                <th className="w-8 py-2.5 pl-3" />
                <th className="text-left py-2.5 pl-1">Ticker</th>
                <th className="text-right pr-3">Qtde</th>
                <th className="text-right pr-3">Custo médio</th>
                <th className="text-right pr-3">Investido</th>
                <th className="text-right pr-3">Atual</th>
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
                  <td colSpan={13} className="py-10 text-center text-muted-foreground">
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
              {rows.map(({ pos, price, currentValue, pnlDollar, pnlPct, dailyChange, dailyChangePct, weight, downAlert, upAlert, is30d }) => {
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
                        {is30d && (
                          <Badge className="h-4 px-1 text-[9px] font-mono bg-amber-500/20 text-amber-400 border border-amber-500/30">
                            30d+
                          </Badge>
                        )}
                      </div>
                      {pos.notes && (
                        <div className="text-[10px] text-muted-foreground truncate max-w-[120px]">{pos.notes}</div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmtQty(pos.quantity)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmt$(pos.avgCost)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{fmt$(pos.investedAmount)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">
                      {hasPrice ? fmt$(currentValue) : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums", hasPrice && dailyChange != null ? (dayPos ? "text-green-400" : "text-red-400") : "text-muted-foreground")}>
                      {hasPrice && dailyChange != null
                        ? `${dailyChange >= 0 ? "+" : "-"}${fmt$(dailyChange)}`
                        : "—"}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold", hasPrice && dailyChangePct != null ? (dayPos ? "text-green-400" : "text-red-400") : "text-muted-foreground")}>
                      {hasPrice && dailyChangePct != null
                        ? fmtPct(dailyChangePct)
                        : "—"}
                    </td>
                    <td className={cn("py-2.5 pr-3 text-right tabular-nums", hasPrice ? (pnlPos ? "text-green-400" : "text-red-400") : "")}>
                      {hasPrice
                        ? `${pnlDollar >= 0 ? "+" : "-"}${fmt$(pnlDollar)}`
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
                  <th className="text-right pr-3">Preço atual</th>
                  <th className="text-right pr-3">Valor atual</th>
                  <th className="text-right pr-3">Receita total</th>
                  <th className="text-right pr-3">Lucro/Perda</th>
                  <th className="text-right pr-3">Retorno %</th>
                  <th className="text-right pr-3">Data encerr.</th>
                  <th className="pr-3" />
                </tr>
              </thead>
              <tbody>
                {soldRows.map(({ pos }) => {
                  const purchases = purchasesMap.get(pos.id) ?? [];
                  const totalInvested = purchases.reduce((s, p) => s + p.amount, 0);
                  const totalRevenue = purchases.reduce((s, p) => {
                    const qty = p.purchasePrice ? p.amount / p.purchasePrice : 0;
                    return s + qty * (p.salePrice ?? 0);
                  }, 0);
                  const pnl = totalRevenue - totalInvested;
                  const pnlPct = totalInvested > 0 ? (pnl / totalInvested) * 100 : 0;
                  const lastSaleDate = purchases
                    .map((p) => p.saleDate ?? "")
                    .filter(Boolean)
                    .sort()
                    .pop() ?? "—";
                  return (
                    <tr key={pos.id} className="border-b border-border/40 hover:bg-muted/10">
                      <td className="py-2.5 pl-4 font-semibold text-sm text-foreground">{pos.ticker}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-muted-foreground">{fmt$(totalInvested)}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-blue-400 font-semibold">
                        {priceMap.get(pos.ticker) ? `$${priceMap.get(pos.ticker)!.toFixed(2)}` : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-blue-400 font-semibold">
                        {priceMap.get(pos.ticker) ? fmt$(pos.quantity * priceMap.get(pos.ticker)!) : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums">{fmt$(totalRevenue)}</td>
                      <td className={cn("py-2.5 pr-3 text-right tabular-nums font-semibold",
                        pnl >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {pnl >= 0 ? "+" : ""}{fmt$(pnl)}
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
        />
      )}
    </div>
  );
}
