import { useListAgentRuns, getListAgentRunsQueryKey, useGetAgentStatus, getGetAgentStatusQueryKey } from "@workspace/api-client-react";
import { CheckCircle, XCircle, Clock, Zap, Calendar, ShieldAlert, Power, Timer } from "lucide-react";
import { useAuth } from "@/lib/auth";

function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

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

function usageTooltip(run: {
  inputTokens?: number | null;
  outputTokens?: number | null;
  cacheReadTokens?: number | null;
  cacheWriteTokens?: number | null;
  llmProvider?: string | null;
  llmModel?: string | null;
}): string {
  if (run.inputTokens == null && run.outputTokens == null) return "";
  return [
    `entrada: ${formatTokens(run.inputTokens)}`,
    `saída: ${formatTokens(run.outputTokens)}`,
    `cache lido: ${formatTokens(run.cacheReadTokens)}`,
    `cache gravado: ${formatTokens(run.cacheWriteTokens)}`,
    run.llmModel ? `modelo: ${run.llmModel}` : null,
  ].filter(Boolean).join(" · ");
}

function StatusBadge({ status }: { status: string }) {
  if (status === "success") {
    return (
      <span className="flex items-center gap-1.5 text-green-400 font-mono text-xs" data-testid="badge-success">
        <CheckCircle className="h-3.5 w-3.5" />
        SUCCESS
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="flex items-center gap-1.5 text-red-400 font-mono text-xs" data-testid="badge-failed">
        <XCircle className="h-3.5 w-3.5" />
        FAILED
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-primary font-mono text-xs" data-testid="badge-running">
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
        <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
      </span>
      RUNNING
    </span>
  );
}

function TriggerBadge({ trigger }: { trigger: string }) {
  if (trigger === "scheduled") {
    return (
      <span className="flex items-center gap-1 text-muted-foreground font-mono text-xs">
        <Calendar className="h-3 w-3" />
        scheduled
      </span>
    );
  }
  if (trigger === "premarket") {
    return (
      <span className="flex items-center gap-1 text-primary font-mono text-xs">
        <Zap className="h-3 w-3" />
        premarket
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-muted-foreground font-mono text-xs">
      <Zap className="h-3 w-3" />
      manual
    </span>
  );
}

function ModeBadge({ mode }: { mode?: string }) {
  if (mode === "premarket") {
    return (
      <span className="px-1.5 py-0.5 rounded bg-primary/10 border border-primary/30 text-primary font-mono text-[10px] uppercase">
        flash
      </span>
    );
  }
  return (
    <span className="px-1.5 py-0.5 rounded bg-secondary border border-border text-muted-foreground font-mono text-[10px] uppercase">
      daily
    </span>
  );
}

export default function Runs() {
  const { user } = useAuth();
  const { data: runs, isLoading } = useListAgentRuns(
    {},
    { query: { queryKey: getListAgentRunsQueryKey({}), refetchInterval: 5000, enabled: !!user?.isAdmin } },
  );
  const { data: status } = useGetAgentStatus({
    query: { queryKey: getGetAgentStatusQueryKey(), refetchInterval: 30000, enabled: !!user?.isAdmin },
  });

  if (!user?.isAdmin) {
    return (
      <div className="border border-border rounded-lg p-12 text-center">
        <ShieldAlert className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
        <p className="font-mono text-muted-foreground text-sm">Acesso restrito ao administrador.</p>
      </div>
    );
  }

  const successCount = runs?.filter((r) => r.status === "success").length ?? 0;
  const failedCount = runs?.filter((r) => r.status === "failed").length ?? 0;
  const totalCost = runs?.reduce((sum, r) => sum + (r.costUsd ?? 0), 0) ?? 0;
  const hasCostData = runs?.some((r) => r.costUsd != null) ?? false;
  const avgDuration =
    runs && runs.length > 0
      ? Math.round(
          runs.filter((r) => r.durationMs).reduce((sum, r) => sum + (r.durationMs ?? 0), 0) /
            (runs.filter((r) => r.durationMs).length || 1) / 1000,
        )
      : null;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-runs-title">
          HISTÓRICO DE EXECUÇÕES
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Status de cada rodada do agente — automática e manual
        </p>
      </div>

      {/* Status do sistema — sempre visível, independe de haver histórico */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <div className="border border-border rounded-lg p-4 bg-card">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1 flex items-center gap-1.5">
            <Power className="h-3 w-3" /> Quantidade de Runs
          </div>
          <div className="text-2xl font-bold font-mono" data-testid="text-runs-quantity">{runs?.length ?? "—"}</div>
        </div>
        <div className={`border rounded-lg p-4 ${status?.scheduleEnabled ? "border-green-900/40 bg-green-950/20" : "border-border bg-card"}`}>
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1 flex items-center gap-1.5">
            <Power className="h-3 w-3" /> Agendamento
          </div>
          <div className={`text-2xl font-bold font-mono ${status?.scheduleEnabled ? "text-green-400" : "text-muted-foreground"}`} data-testid="text-schedule-status">
            {status?.scheduleEnabled ? "LIGADO" : "DESLIGADO"}
          </div>
        </div>
        <div className="border border-border rounded-lg p-4 bg-card">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-1 flex items-center gap-1.5">
            <Timer className="h-3 w-3" /> Tempo Ligado (app)
          </div>
          <div className="text-2xl font-bold font-mono" data-testid="text-uptime">{formatUptime(status?.uptimeSeconds)}</div>
        </div>
      </div>

      {/* Summary stats */}
      {runs && runs.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
          <div className="border border-border rounded-lg p-4 bg-card">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Total</div>
            <div className="text-2xl font-bold font-mono" data-testid="text-total-runs">{runs.length}</div>
          </div>
          <div className="border border-green-900/40 rounded-lg p-4 bg-green-950/20">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Sucesso</div>
            <div className="text-2xl font-bold font-mono text-green-400" data-testid="text-success-count">{successCount}</div>
          </div>
          <div className="border border-red-900/40 rounded-lg p-4 bg-red-950/20">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Falhas</div>
            <div className="text-2xl font-bold font-mono text-red-400" data-testid="text-failed-count">{failedCount}</div>
          </div>
          <div className="border border-border rounded-lg p-4 bg-card">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Custo (listado)</div>
            <div className="text-2xl font-bold font-mono text-primary" data-testid="text-total-cost">
              {hasCostData ? formatCost(totalCost) : "—"}
            </div>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center justify-center h-40">
          <div className="text-muted-foreground font-mono text-sm animate-pulse">Carregando histórico...</div>
        </div>
      )}

      {!isLoading && (!runs || runs.length === 0) && (
        <div className="border border-border rounded-lg p-12 text-center">
          <Clock className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="font-mono text-muted-foreground text-sm">Nenhuma execução registrada ainda.</p>
          <p className="font-mono text-muted-foreground text-xs mt-1">Clique em RUN AGENT para iniciar.</p>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-border bg-secondary/50">
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Status</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Início</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Duração</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Gatilho</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Modo</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Custo</th>
                <th className="text-left px-4 py-3 text-xs text-muted-foreground uppercase tracking-widest">Erro</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run, i) => (
                <tr
                  key={run.id}
                  className={`border-b border-border/50 hover:bg-secondary/30 transition-colors ${i % 2 === 0 ? "" : "bg-secondary/10"}`}
                  data-testid={`row-run-${run.id}`}
                >
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-3 text-foreground text-xs">
                    {new Date(run.startedAt).toLocaleString("pt-BR", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                      timeZone: "America/Sao_Paulo",
                    })}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {formatDuration(run.durationMs)}
                  </td>
                  <td className="px-4 py-3">
                    <TriggerBadge trigger={run.trigger} />
                  </td>
                  <td className="px-4 py-3">
                    <ModeBadge mode={run.mode} />
                  </td>
                  <td
                    className="px-4 py-3 text-foreground text-xs whitespace-nowrap"
                    title={usageTooltip(run)}
                    data-testid={`text-cost-${run.id}`}
                  >
                    {formatCost(run.costUsd)}
                    {run.inputTokens != null && (
                      <span className="text-muted-foreground ml-1.5">
                        ({formatTokens((run.inputTokens ?? 0) + (run.cacheReadTokens ?? 0) + (run.cacheWriteTokens ?? 0))}↓ {formatTokens(run.outputTokens)}↑)
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-red-400 text-xs max-w-xs truncate" title={run.errorMessage ?? ""}>
                    {run.errorMessage ? run.errorMessage.slice(0, 80) + (run.errorMessage.length > 80 ? "…" : "") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {avgDuration !== null && (
        <p className="text-xs font-mono text-muted-foreground mt-4 text-right">
          Duração média: {avgDuration}s
        </p>
      )}
    </div>
  );
}
