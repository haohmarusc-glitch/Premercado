import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useListExitPlan,
  useCreateExitPlanItem,
  useUpdateExitPlanItem,
  useDeleteExitPlanItem,
  getListExitPlanQueryKey,
  useListAlerts,
  useCreateAlert,
  getListAlertsQueryKey,
} from "@workspace/api-client-react";
import type { ExitPlanItem, PriceAlert } from "@workspace/api-client-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Flag, Plus, Trash2, CheckCircle2, SkipForward, RotateCcw, Newspaper, BellPlus, ChevronDown, ChevronUp } from "lucide-react";
import { useTacticalContext, tacticalSignal, type TacticalContext, type Tone } from "@/hooks/use-tactical-context";

// ─── Plano de saída — 16/jul/2026 ────────────────────────────────────────────
// Seed inicial da análise feita no chat (carteira Nomad, prazo real 30/set,
// dinheiro usado em out/2026). Só usado pra popular a tabela na primeira
// visita à página -- depois disso os itens vivem só no banco (exit_plan_items).
const SEED_ITEMS = [
  {
    ticker: "SKHY", phase: 1, phaseLabel: "Fase 1 · 17–24 de julho",
    targetDate: "2026-07-17", action: "Vender",
    rationale: "Sem tese pra segurar: comprado 1 dia antes do selloff, sem histórico técnico (IPO em 10/jul).",
    eventDate: null,
  },
  {
    ticker: "GOOGL", phase: 1, phaseLabel: "Fase 1 · 17–24 de julho",
    targetDate: "2026-07-20", action: "Vender antes do earnings",
    rationale: "Trava o valor atual e remove o risco binário do resultado — não vale segurar earnings de algo que já vai sair no trimestre.",
    eventDate: "2026-07-21",
  },
  {
    ticker: "TSLA", phase: 1, phaseLabel: "Fase 1 · 17–24 de julho",
    targetDate: "2026-07-21", action: "Vender antes do earnings",
    rationale: "Mesma lógica da GOOGL: sem motivo pra correr risco de earnings numa posição que já será liquidada.",
    eventDate: "2026-07-22",
  },
  {
    ticker: "SMCI", phase: 2, phaseLabel: "Fase 2 · 27 jul – 15 ago",
    targetDate: "2026-08-03", action: "Vender antes do earnings",
    rationale: "Earnings confirmado 04/ago, -37% e sem reversão técnica — não segurar apostando numa recuperação incerta.",
    eventDate: "2026-08-04",
  },
  {
    ticker: "ARM", phase: 2, phaseLabel: "Fase 2 · 27 jul – 15 ago",
    targetDate: "2026-08-15", action: "Vender (na força, se houver repique)",
    rationale: "RSI perto de sobrevenda — usar qualquer repique pós-resultados de capex das big techs. Confirme a data de earnings de agosto antes.",
    eventDate: null,
  },
  {
    ticker: "AVGO", phase: 2, phaseLabel: "Fase 2 · 27 jul – 15 ago",
    targetDate: "2026-08-15", action: "Vender (na força, se houver repique)",
    rationale: "Um dos nomes mais citados no medo de desaceleração de capex de IA — vende na força ou no fim da janela se ela não vier.",
    eventDate: null,
  },
  {
    ticker: "MRVL", phase: 2, phaseLabel: "Fase 2 · 27 jul – 15 ago",
    targetDate: "2026-08-15", action: "Vender (na força, se houver repique)",
    rationale: "Citado como o principal afetado pelo medo de capex de IA do dia 16/jul.",
    eventDate: null,
  },
  {
    ticker: "NVDA", phase: 3, phaseLabel: "Fase 3 · 18 ago – 20 set",
    targetDate: "2026-09-01", action: "Vender na semana pós-earnings",
    rationale: "Maior posição e melhor qualidade — dá tempo até o earnings de 26/ago, mas sem estender pra setembro.",
    eventDate: "2026-08-26",
  },
  {
    ticker: "ETF", phase: 3, phaseLabel: "Fase 3 · 18 ago – 20 set",
    targetDate: "2026-09-15", action: "Vender",
    rationale: "Cota ~US$100, baixa volatilidade, sem risco de tese/earnings — fecha a conta perto do prazo, sem pressa. Ticker exato não confirmado; edite/recrie se identificar qual é.",
    eventDate: null,
  },
];

type Urgency = "critico" | "atencao" | "info" | "done";

function daysUntil(dateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(dateStr + "T00:00:00");
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function urgencyOf(item: ExitPlanItem): Urgency {
  if (item.status !== "pending") return "done";
  const d = daysUntil(item.targetDate);
  if (d < 0) return "critico";
  if (d <= 3) return "atencao";
  return "info";
}

const URGENCY_STYLE: Record<Urgency, string> = {
  critico: "border-red-500/40 bg-red-500/10 text-red-400",
  atencao: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  info: "border-border bg-secondary/30 text-muted-foreground",
  done: "border-emerald-500/30 bg-emerald-500/5 text-emerald-500/80",
};

function urgencyLabel(item: ExitPlanItem): string {
  if (item.status === "sold") return "✅ Vendido";
  if (item.status === "skipped") return "⏭ Pulado";
  const d = daysUntil(item.targetDate);
  if (d < 0) return `🔴 Vencido há ${Math.abs(d)}d`;
  if (d === 0) return "🟡 Hoje";
  if (d <= 3) return `🟡 Faltam ${d}d`;
  return `Faltam ${d}d`;
}

const TONE_STYLE: Record<Tone, string> = {
  critico: "border-red-500/40 bg-red-500/10 text-red-400",
  atencao: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  info: "border-border bg-secondary/30 text-muted-foreground",
  bom: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
};

const ALERT_CONDITIONS = [
  { value: "above", label: "sobe acima de" },
  { value: "below", label: "cai abaixo de" },
];

function ItemRow({
  item, ctx, alertsForTicker, onCreateAlert, creatingAlert,
}: {
  item: ExitPlanItem;
  ctx: TacticalContext;
  alertsForTicker: PriceAlert[];
  onCreateAlert: (input: { symbol: string; condition: string; thresholdPct: number }) => void;
  creatingAlert: boolean;
}) {
  const qc = useQueryClient();
  const [showSellForm, setShowSellForm] = useState(false);
  const [soldPrice, setSoldPrice] = useState("");
  const [soldAt, setSoldAt] = useState(() => new Date().toISOString().slice(0, 10));
  const [showAlertForm, setShowAlertForm] = useState(false);
  const [alertCondition, setAlertCondition] = useState("above");
  const [alertPct, setAlertPct] = useState("5");
  const [showNews, setShowNews] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: getListExitPlanQueryKey() });
  const update = useUpdateExitPlanItem({ mutation: { onSuccess: invalidate } });
  const remove = useDeleteExitPlanItem({ mutation: { onSuccess: invalidate } });

  const urgency = urgencyOf(item);
  const tech = ctx.technicalsByTicker.get(item.ticker);
  const signal = tacticalSignal(item.ticker, ctx);
  const headlines = ctx.newsByTicker.get(item.ticker) ?? [];
  const latestHeadline = headlines[0];

  return (
    <div className={cn("border rounded-md px-3 py-2.5 space-y-2", URGENCY_STYLE[urgency])} data-testid={`exit-plan-item-${item.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-mono font-bold text-sm text-foreground">{item.ticker}</span>
          <span className="text-xs">{item.action}</span>
          {tech?.price != null && (
            <span className="font-mono text-[11px] text-muted-foreground">
              US$ {tech.price.toFixed(2)}
              {tech.changePct != null && (
                <span className={tech.changePct >= 0 ? "text-emerald-400" : "text-red-400"}>
                  {" "}{tech.changePct >= 0 ? "▲" : "▼"} {Math.abs(tech.changePct).toFixed(1)}%
                </span>
              )}
            </span>
          )}
          {tech?.rsi != null && (
            <Badge variant="outline" className="text-[10px] font-mono">RSI {tech.rsi.toFixed(0)}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono font-bold whitespace-nowrap">{urgencyLabel(item)}</span>
          <button
            onClick={() => remove.mutate({ id: item.id })}
            className="text-muted-foreground/60 hover:text-red-400 transition-colors"
            title="Remover item"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground/90">{item.rationale}</p>

      {signal && (
        <div className={cn("border rounded px-2 py-1 text-[11px] font-mono", TONE_STYLE[signal.tone])}>
          {signal.label}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-muted-foreground">
        <span>Prazo: {item.targetDate}</span>
        {item.eventDate && <Badge variant="outline" className="text-[10px] font-mono">Earnings {item.eventDate}</Badge>}
        {item.status === "sold" && item.soldPrice != null && (
          <span>· Vendido em {item.soldAt} a US$ {Number(item.soldPrice).toFixed(2)}</span>
        )}
        {alertsForTicker.length > 0 && (
          <Badge variant="outline" className="text-[10px] font-mono">
            {alertsForTicker.length} alerta{alertsForTicker.length !== 1 ? "s" : ""} ativo{alertsForTicker.length !== 1 ? "s" : ""}
          </Badge>
        )}
      </div>

      {latestHeadline && (
        <div>
          <button
            onClick={() => setShowNews((s) => !s)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <Newspaper className="h-3 w-3" />
            <span className="truncate max-w-[420px]">{latestHeadline.title}</span>
            {showNews ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          {showNews && (
            <div className="mt-1 space-y-1 pl-4">
              {headlines.slice(0, 3).map((h, i) => (
                <p key={i} className="text-[11px] text-muted-foreground/80">
                  <span className="font-mono">[{h.source || "?"}]</span> {h.title}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {item.status === "pending" && !showAlertForm && (
        <div className="pt-1">
          <Button size="sm" variant="ghost" className="h-7 text-xs text-muted-foreground" onClick={() => setShowAlertForm(true)}>
            <BellPlus className="h-3.5 w-3.5 mr-1" /> Criar alerta de preço
          </Button>
        </div>
      )}

      {showAlertForm && (
        <div className="flex flex-wrap items-end gap-2 pt-1">
          <div>
            <Label className="text-[10px]">Se {item.ticker}</Label>
            <select
              value={alertCondition}
              onChange={(e) => setAlertCondition(e.target.value)}
              className="h-7 text-xs bg-background border border-border rounded px-2"
            >
              {ALERT_CONDITIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <Label className="text-[10px]">% do fechamento anterior</Label>
            <Input
              type="number" step="0.5" value={alertPct}
              onChange={(e) => setAlertPct(e.target.value)}
              className="h-7 w-20 text-xs"
            />
          </div>
          <Button
            size="sm" className="h-7 text-xs"
            disabled={creatingAlert || !alertPct}
            onClick={() => {
              onCreateAlert({ symbol: item.ticker, condition: alertCondition, thresholdPct: parseFloat(alertPct) });
              setShowAlertForm(false);
            }}
          >
            Criar
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setShowAlertForm(false)}>Cancelar</Button>
        </div>
      )}

      {item.status === "pending" && !showSellForm && (
        <div className="flex gap-2 pt-1">
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setShowSellForm(true)}>
            <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> Marcar vendido
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 text-xs text-muted-foreground"
            onClick={() => update.mutate({ id: item.id, data: { status: "skipped" } })}
          >
            <SkipForward className="h-3.5 w-3.5 mr-1" /> Pular
          </Button>
        </div>
      )}

      {item.status === "pending" && showSellForm && (
        <div className="flex flex-wrap items-end gap-2 pt-1">
          <div>
            <Label className="text-[10px]">Preço de venda</Label>
            <Input
              type="number" step="0.01" value={soldPrice}
              onChange={(e) => setSoldPrice(e.target.value)}
              className="h-7 w-28 text-xs" placeholder="US$"
            />
          </div>
          <div>
            <Label className="text-[10px]">Data</Label>
            <Input type="date" value={soldAt} onChange={(e) => setSoldAt(e.target.value)} className="h-7 text-xs" />
          </div>
          <Button
            size="sm" className="h-7 text-xs"
            disabled={!soldPrice}
            onClick={() => {
              update.mutate({ id: item.id, data: { status: "sold", soldAt, soldPrice: parseFloat(soldPrice) } });
              setShowSellForm(false);
            }}
          >
            Confirmar
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setShowSellForm(false)}>Cancelar</Button>
        </div>
      )}

      {item.status !== "pending" && (
        <Button
          size="sm" variant="ghost" className="h-7 text-xs text-muted-foreground"
          onClick={() => update.mutate({ id: item.id, data: { status: "pending", soldAt: null, soldPrice: null } })}
        >
          <RotateCcw className="h-3.5 w-3.5 mr-1" /> Reabrir
        </Button>
      )}
    </div>
  );
}

const EMPTY_FORM = { ticker: "", phase: "1", phaseLabel: "", targetDate: "", action: "", rationale: "", eventDate: "" };

export default function ExitPlanPage() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });

  const { data, isLoading } = useListExitPlan({
    query: { queryKey: getListExitPlanQueryKey() },
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: getListExitPlanQueryKey() });
  const create = useCreateExitPlanItem({ mutation: { onSuccess: invalidate } });

  async function seedPlan() {
    for (const item of SEED_ITEMS) {
      await create.mutateAsync({ data: item });
    }
  }

  const items = data ?? [];
  const pendingCount = items.filter((i) => i.status === "pending").length;
  const overdueCount = items.filter((i) => i.status === "pending" && daysUntil(i.targetDate) < 0).length;
  const dueSoonCount = items.filter((i) => i.status === "pending" && daysUntil(i.targetDate) >= 0 && daysUntil(i.targetDate) <= 3).length;

  const phases = Array.from(new Set(items.map((i) => i.phase))).sort((a, b) => a - b);

  // ── Contexto tático (técnicos, notícias, contágio setorial) + alertas ──────
  const tickers = Array.from(new Set(items.map((i) => i.ticker)));
  const ctx = useTacticalContext(tickers);

  const { data: alerts } = useListAlerts({
    query: { queryKey: getListAlertsQueryKey(), staleTime: 55_000, refetchInterval: 60_000 },
  });
  const alertsByTicker = new Map<string, PriceAlert[]>();
  for (const a of alerts ?? []) {
    const list = alertsByTicker.get(a.symbol) ?? [];
    list.push(a);
    alertsByTicker.set(a.symbol, list);
  }

  const createAlertInvalidate = () => qc.invalidateQueries({ queryKey: getListAlertsQueryKey() });
  const createAlert = useCreateAlert({ mutation: { onSuccess: createAlertInvalidate } });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Flag className="h-7 w-7 text-primary" /> PLANO DE SAÍDA
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            {pendingCount} pendente{pendingCount !== 1 ? "s" : ""}
            {overdueCount > 0 && <span className="text-red-400"> · {overdueCount} vencido{overdueCount !== 1 ? "s" : ""}</span>}
            {dueSoonCount > 0 && <span className="text-amber-400"> · {dueSoonCount} no prazo curto</span>}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setShowForm((s) => !s)}>
          <Plus className="h-4 w-4 mr-1" /> Novo item
        </Button>
      </div>

      {showForm && (
        <div className="border border-border rounded-lg p-4 bg-card space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <Label className="text-xs">Ticker</Label>
              <Input value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })} />
            </div>
            <div>
              <Label className="text-xs">Fase</Label>
              <Input type="number" value={form.phase} onChange={(e) => setForm({ ...form, phase: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs">Rótulo da fase</Label>
              <Input value={form.phaseLabel} onChange={(e) => setForm({ ...form, phaseLabel: e.target.value })} placeholder="Fase 2 · ago" />
            </div>
            <div>
              <Label className="text-xs">Prazo alvo</Label>
              <Input type="date" value={form.targetDate} onChange={(e) => setForm({ ...form, targetDate: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs">Ação</Label>
              <Input value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })} placeholder="Vender antes do earnings" />
            </div>
            <div>
              <Label className="text-xs">Earnings/evento (opcional)</Label>
              <Input type="date" value={form.eventDate} onChange={(e) => setForm({ ...form, eventDate: e.target.value })} />
            </div>
            <div className="col-span-2 md:col-span-4">
              <Label className="text-xs">Motivo</Label>
              <Input value={form.rationale} onChange={(e) => setForm({ ...form, rationale: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!form.ticker || !form.targetDate || !form.action || !form.rationale}
              onClick={() => {
                create.mutate({
                  data: {
                    ticker: form.ticker, phase: parseInt(form.phase, 10) || 1,
                    phaseLabel: form.phaseLabel || `Fase ${form.phase}`,
                    targetDate: form.targetDate, action: form.action,
                    rationale: form.rationale, eventDate: form.eventDate || null,
                  },
                });
                setForm({ ...EMPTY_FORM });
                setShowForm(false);
              }}
            >
              Adicionar
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>Cancelar</Button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : items.length === 0 ? (
        <div className="border border-dashed border-border rounded-lg p-8 text-center space-y-3">
          <p className="text-sm text-muted-foreground">Nenhum item no plano de saída ainda.</p>
          <Button onClick={() => seedPlan()} disabled={create.isPending}>
            Carregar plano de 16/jul/2026
          </Button>
        </div>
      ) : (
        <div className="space-y-6">
          {phases.map((phase) => {
            const phaseItems = items.filter((i) => i.phase === phase);
            return (
              <div key={phase} className="border border-border rounded-lg overflow-hidden bg-card">
                <div className="px-4 py-3 border-b border-border bg-secondary/30">
                  <span className="font-mono font-bold text-sm tracking-wider uppercase text-muted-foreground">
                    {phaseItems[0]?.phaseLabel ?? `Fase ${phase}`}
                  </span>
                </div>
                <div className="p-4 space-y-2">
                  {phaseItems.map((item) => (
                    <ItemRow
                      key={item.id}
                      item={item}
                      ctx={ctx}
                      alertsForTicker={alertsByTicker.get(item.ticker) ?? []}
                      creatingAlert={createAlert.isPending}
                      onCreateAlert={({ symbol, condition, thresholdPct }) =>
                        createAlert.mutate({ data: { symbol, condition, thresholdPct } })
                      }
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
