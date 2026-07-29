import { useState } from "react";
import { useGetAgentSpendHistory, getGetAgentSpendHistoryQueryKey } from "@workspace/api-client-react";
import type { AiSpendItem } from "@workspace/api-client-react";
import { DollarSign, ShieldAlert, ChevronDown, ChevronRight, Bot, MessageSquare } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { todayBRTDateString } from "@/lib/format";

function formatCost(costUsd: number | null | undefined): string {
  if (costUsd == null) return "—";
  if (costUsd === 0) return "$0";
  if (costUsd < 0.01) return `$${costUsd.toFixed(4)}`;
  return `$${costUsd.toFixed(2)}`;
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function formatDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZone: "America/Sao_Paulo",
  });
}

function formatDayLabel(date: string): string {
  const d = new Date(`${date}T12:00:00`);
  return d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
}

function itemLabel(item: AiSpendItem): string {
  if (item.source === "chat") {
    return `Chat — ${item.chatSessionTitle ?? "conversa"}`;
  }
  const modeLabel: Record<string, string> = {
    daily: "Diário", premarket: "Flash pré-mercado", portfolio: "Carteira",
    coal: "Carvão", ai: "IA", news: "Notícias", exit_plan: "Plano de saída", alerts: "Gestão de alertas",
  };
  const triggerLabel: Record<string, string> = {
    scheduled: "agendado", premarket: "flash", alerts: "alertas", manual: "manual",
  };
  const mode = (item.mode && modeLabel[item.mode]) || item.mode || "run";
  const trigger = (item.trigger && triggerLabel[item.trigger]) || item.trigger;
  return trigger ? `${mode} (${trigger})` : mode;
}

function SourceIcon({ item }: { item: AiSpendItem }) {
  if (item.source === "chat") {
    return <MessageSquare className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
  }
  return <Bot className="h-3.5 w-3.5 text-primary shrink-0" />;
}

function ItemRow({ item }: { item: AiSpendItem }) {
  const tokensIn = (item.inputTokens ?? 0) + (item.cacheReadTokens ?? 0) + (item.cacheWriteTokens ?? 0);
  return (
    <tr className="border-b border-border/40 hover:bg-secondary/20 transition-colors">
      <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">{formatTime(item.timestamp)}</td>
      <td className="px-4 py-2 text-xs">
        <span className="flex items-center gap-1.5">
          <SourceIcon item={item} />
          <span className="text-foreground">{itemLabel(item)}</span>
          {item.status && (
            <span className={
              item.status === "success" ? "text-green-400" :
              item.status === "failed" ? "text-red-400" : "text-primary"
            }>
              · {item.status}
            </span>
          )}
        </span>
      </td>
      <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">
        {item.source === "run" ? formatDuration(item.durationMs) : "—"}
      </td>
      <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap" title={item.llmModel ?? ""}>
        {item.llmModel ?? "—"}
      </td>
      <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">
        {item.inputTokens != null || item.outputTokens != null
          ? `${formatTokens(tokensIn)}↓ ${formatTokens(item.outputTokens)}↑`
          : "—"}
      </td>
      <td className="px-4 py-2 text-xs text-foreground font-bold whitespace-nowrap text-right">
        {formatCost(item.costUsd)}
      </td>
    </tr>
  );
}

export default function AiSpendPage() {
  const { user } = useAuth();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const { data, isLoading } = useGetAgentSpendHistory(
    { days: 30 },
    { query: { queryKey: getGetAgentSpendHistoryQueryKey({ days: 30 }), refetchInterval: 30000, enabled: !!user?.isAdmin } },
  );

  if (!user?.isAdmin) {
    return (
      <div className="border border-border rounded-lg p-12 text-center">
        <ShieldAlert className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
        <p className="font-mono text-muted-foreground text-sm">Acesso restrito ao administrador.</p>
      </div>
    );
  }

  function toggleDay(date: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(date)) next.delete(date); else next.add(date);
      return next;
    });
  }

  const todayTotal = data?.days.find((d) => d.date === todayBRTDateString())?.totalCostUsd ?? null;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <DollarSign className="h-7 w-7 text-primary" /> GASTOS COM IA
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Cada chamada de LLM (runs do agente e chat), com data/hora, duração, tokens e custo — total por dia e geral.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="border border-border rounded-lg p-4 bg-card">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Hoje</div>
          <div className="text-2xl font-bold font-mono text-primary" data-testid="text-spend-today">
            {data?.hasCostData ? formatCost(todayTotal ?? 0) : "—"}
          </div>
        </div>
        <div className="border border-border rounded-lg p-4 bg-card">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Últimos 30 dias</div>
          <div className="text-2xl font-bold font-mono text-primary" data-testid="text-spend-window">
            {data?.hasCostData ? formatCost(data.windowTotalCostUsd) : "—"}
          </div>
        </div>
        <div className="border border-primary/30 rounded-lg p-4 bg-primary/5">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Total geral (todo o período)</div>
          <div className="text-2xl font-bold font-mono text-primary" data-testid="text-spend-all-time">
            {data?.hasCostData ? formatCost(data.allTimeTotalCostUsd) : "—"}
          </div>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center h-40">
          <div className="text-muted-foreground font-mono text-sm animate-pulse">Carregando histórico de gastos...</div>
        </div>
      )}

      {!isLoading && (!data || data.days.length === 0) && (
        <div className="border border-border rounded-lg p-12 text-center">
          <DollarSign className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="font-mono text-muted-foreground text-sm">Nenhum gasto registrado nos últimos 30 dias.</p>
        </div>
      )}

      {data && data.days.length > 0 && (
        <div className="space-y-3">
          {data.days.map((day) => {
            const isCollapsed = collapsed.has(day.date);
            return (
              <div key={day.date} className="border border-border rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleDay(day.date)}
                  className="w-full flex items-center justify-between px-4 py-3 bg-secondary/30 hover:bg-secondary/50 transition-colors"
                  data-testid={`button-toggle-day-${day.date}`}
                >
                  <span className="flex items-center gap-2 font-mono text-sm font-bold text-foreground capitalize">
                    {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    {formatDayLabel(day.date)}
                    <span className="text-muted-foreground font-normal text-xs">({day.items.length} chamada{day.items.length === 1 ? "" : "s"})</span>
                  </span>
                  <span className="font-mono text-sm font-bold text-primary" data-testid={`text-day-total-${day.date}`}>
                    {formatCost(day.totalCostUsd)}
                  </span>
                </button>
                {!isCollapsed && (
                  <div className="overflow-x-auto">
                    <table className="w-full font-mono text-sm">
                      <thead>
                        <tr className="border-b border-border bg-secondary/10">
                          <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Hora</th>
                          <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Chamada</th>
                          <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Duração</th>
                          <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Modelo</th>
                          <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Tokens</th>
                          <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase tracking-widest">Custo</th>
                        </tr>
                      </thead>
                      <tbody>
                        {day.items.map((item) => <ItemRow key={item.id} item={item} />)}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
