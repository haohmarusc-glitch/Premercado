import { useState } from "react";
import {
  useListEntryExitStudies, getListEntryExitStudiesQueryKey,
  useCreateEntryExitStudy, useDeleteEntryExitStudy,
  useGetEntryExitStudy, getGetEntryExitStudyQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Plus, Trash2, Target, ChevronDown, ChevronUp, History, TrendingDown, Calendar,
} from "lucide-react";

function fmtPct(n: number | null | undefined) {
  if (n == null) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

function fmtUsd(n: number | null | undefined) {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}

// targetDate/calcDate chegam como "YYYY-MM-DD" puro (sem hora) -- formatar
// direto na string em vez de `new Date(iso)` evita o problema clássico de
// fuso (new Date("2026-09-12") é meia-noite UTC, que em BRT já é o dia
// anterior à tarde).
function fmtDateBR(isoDate: string) {
  const [y, m, d] = isoDate.split("-");
  return `${d}/${m}/${y}`;
}

function probColorClass(p: number | null | undefined) {
  if (p == null) return "text-muted-foreground";
  if (p >= 0.5) return "text-green-400";
  if (p >= 0.25) return "text-yellow-400";
  return "text-red-400";
}

function daysUntil(isoDate: string): number {
  const hoje = new Date();
  const alvo = new Date(isoDate + "T00:00:00");
  return Math.max(0, Math.round((alvo.getTime() - hoje.getTime()) / 86400000));
}

function StudyHistoryTable({ id }: { id: number }) {
  const { data, isLoading } = useGetEntryExitStudy(id, {
    query: { queryKey: getGetEntryExitStudyQueryKey(id), staleTime: 30_000 },
  });

  if (isLoading) {
    return (
      <div className="px-4 pb-3 pt-1">
        <span className="font-mono text-xs text-muted-foreground animate-pulse">Carregando histórico...</span>
      </div>
    );
  }

  const history = data?.history ?? [];
  if (history.length === 0) {
    return (
      <div className="px-4 pb-3 pt-1 flex items-center gap-2 text-xs font-mono text-muted-foreground">
        <History className="h-3 w-3" />
        Ainda sem histórico diário registrado -- o checker roda 1x por dia.
      </div>
    );
  }

  return (
    <div className="px-4 pb-4 pt-1">
      <div className="border border-border/50 rounded-md overflow-hidden overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-border/50 bg-secondary/30">
              <th className="text-left px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Data</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Preço</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Prob. alvo</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Média baixa 6m</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Mín. 12m</th>
            </tr>
          </thead>
          <tbody>
            {[...history].reverse().map((h, i) => (
              <tr
                key={h.id}
                className={`border-b border-border/30 last:border-0 ${i % 2 === 0 ? "" : "bg-secondary/10"}`}
              >
                <td className="px-3 py-1.5 text-muted-foreground">{fmtDateBR(h.calcDate)}</td>
                <td className="px-3 py-1.5 text-right text-foreground">{fmtUsd(h.currentPrice)}</td>
                <td className={`px-3 py-1.5 text-right font-bold ${probColorClass(h.probReachTarget)}`}>
                  {fmtPct(h.probReachTarget)}
                </td>
                <td className="px-3 py-1.5 text-right text-muted-foreground">{fmtUsd(h.avgLow6m)}</td>
                <td className="px-3 py-1.5 text-right text-muted-foreground">{fmtUsd(h.minLow1y)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function EntryExitStudyPage() {
  const qc = useQueryClient();
  const { toast } = useToast();

  const { data, isLoading } = useListEntryExitStudies({
    query: { queryKey: getListEntryExitStudiesQueryKey(), refetchInterval: 60_000 },
  });
  const createStudy = useCreateEntryExitStudy();
  const deleteStudy = useDeleteEntryExitStudy();

  const invalidate = () => qc.invalidateQueries({ queryKey: getListEntryExitStudiesQueryKey() });

  const [ticker, setTicker] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const price = parseFloat(targetPrice);
    if (!ticker.trim() || !targetDate || isNaN(price) || price <= 0) return;

    createStudy.mutate(
      { data: { ticker: ticker.trim().toUpperCase(), targetPrice: price, targetDate } },
      {
        onSuccess: (res) => {
          invalidate();
          setTicker("");
          setTargetPrice("");
          setTargetDate("");
          toast({
            title: "Estudo criado",
            description: `${res.target.ticker}: ${fmtPct(res.calc.probReachTarget)} de chance de bater ${fmtUsd(res.target.targetPrice)} até ${fmtDateBR(res.target.targetDate)}`,
          });
        },
        onError: (err) => toast({
          title: "Erro ao criar estudo",
          description: err instanceof Error ? err.message : String(err),
          variant: "destructive",
        }),
      },
    );
  }

  function handleDelete(id: number) {
    deleteStudy.mutate(
      { id },
      {
        onSuccess: () => {
          if (expandedId === id) setExpandedId(null);
          invalidate();
          toast({ title: "Estudo removido" });
        },
        onError: () => toast({ title: "Erro ao remover", variant: "destructive" }),
      },
    );
  }

  const studies = data?.studies ?? [];
  const canSubmit = ticker.trim() && targetDate && targetPrice && !isNaN(parseFloat(targetPrice)) && parseFloat(targetPrice) > 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-entry-exit-study-title">
          ESTUDO DE ENTRADA E SAÍDA
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Probabilidade de um ticker bater um preço-alvo até uma data, com referência de suporte pra entrada
        </p>
      </div>

      {/* Create form */}
      <div className="border border-border rounded-lg p-6 mb-6 bg-card">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-5">
          <Plus className="h-3.5 w-3.5" />
          Novo estudo
        </div>

        <form onSubmit={handleCreate} className="space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Ticker</label>
              <Input
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                placeholder="SMCI, NVDA…"
                className="font-mono bg-secondary border-border w-32"
                data-testid="input-study-ticker"
              />
            </div>

            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Preço-alvo</label>
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-muted-foreground">$</span>
                <Input
                  value={targetPrice}
                  onChange={(e) => setTargetPrice(e.target.value)}
                  type="number"
                  step="0.01"
                  placeholder="45.00"
                  className="font-mono bg-secondary border-border w-28"
                  data-testid="input-study-target-price"
                />
              </div>
            </div>

            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Data-alvo</label>
              <Input
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                type="date"
                className="font-mono bg-secondary border-border"
                data-testid="input-study-target-date"
              />
            </div>

            <Button
              type="submit"
              disabled={!canSubmit || createStudy.isPending}
              className="font-mono font-bold"
              data-testid="btn-create-study"
            >
              <Plus className="h-4 w-4 mr-1" />
              {createStudy.isPending ? "Calculando..." : "Criar Estudo"}
            </Button>
          </div>

          <p className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2 max-w-2xl">
            Cria 3 alertas de preço automaticamente: saída acima do alvo, entrada abaixo da média das mínimas de
            6 meses, e entrada abaixo da menor mínima de 12 meses. Probabilidade calculada com passeio aleatório
            sem viés direcional (drift zero), ajustado pela volatilidade real do papel e pelo salto histórico de
            earnings quando o balanço cai dentro da janela.
          </p>
        </form>
      </div>

      {/* List */}
      {isLoading && (
        <div className="flex items-center justify-center h-32">
          <span className="font-mono text-sm text-muted-foreground animate-pulse">Carregando estudos...</span>
        </div>
      )}

      {!isLoading && studies.length === 0 && (
        <div className="border border-dashed border-border rounded-lg p-12 text-center">
          <Target className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="font-mono text-muted-foreground text-sm">Nenhum estudo em acompanhamento.</p>
          <p className="font-mono text-muted-foreground text-xs mt-1">
            Crie um estudo acima pra começar a acompanhar a probabilidade dia a dia.
          </p>
        </div>
      )}

      {studies.length > 0 && (
        <div className="space-y-2">
          {studies.map(({ target, latest }) => {
            const isExpanded = expandedId === target.id;
            const dias = daysUntil(target.targetDate);

            return (
              <div
                key={target.id}
                className="border border-border rounded-lg bg-card transition-colors"
                data-testid={`study-row-${target.id}`}
              >
                <div className="flex items-center gap-4 px-4 py-3 flex-wrap">
                  <Target className="h-4 w-4 flex-shrink-0 text-primary" />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-bold text-primary">{target.ticker}</span>
                      <Badge variant="outline" className="font-mono text-xs border-primary/30 text-primary bg-primary/5">
                        alvo {fmtUsd(target.targetPrice)}
                      </Badge>
                      <Badge variant="outline" className="font-mono text-xs text-muted-foreground border-border">
                        <Calendar className="h-3 w-3 mr-1" />
                        {fmtDateBR(target.targetDate)} ({dias}d)
                      </Badge>
                      {latest && (
                        <Badge
                          variant="outline"
                          className={`font-mono text-xs border-current/30 bg-current/5 ${probColorClass(latest.probReachTarget)}`}
                        >
                          {fmtPct(latest.probReachTarget)} de chance
                        </Badge>
                      )}
                    </div>
                    {latest && (
                      <div className="flex items-center gap-3 mt-1 text-[11px] font-mono text-muted-foreground flex-wrap">
                        <span>Preço atual: <span className="text-foreground">{fmtUsd(latest.currentPrice)}</span></span>
                        <span className="flex items-center gap-1">
                          <TrendingDown className="h-3 w-3" />
                          entrada: média 6m {fmtUsd(latest.avgLow6m)} · mín. 12m {fmtUsd(latest.minLow1y)}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      type="button"
                      onClick={() => setExpandedId(isExpanded ? null : target.id)}
                      className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary"
                      data-testid={`history-toggle-${target.id}`}
                      title="Ver histórico diário"
                    >
                      <History className="h-3.5 w-3.5" />
                      {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(target.id)}
                      className="text-muted-foreground hover:text-red-400 transition-colors p-1"
                      data-testid={`delete-study-${target.id}`}
                      aria-label="Parar de acompanhar"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-border/50">
                    <div className="px-4 pt-2 pb-1 flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-wide">
                      <History className="h-3 w-3" />
                      Histórico diário
                    </div>
                    <StudyHistoryTable id={target.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs font-mono text-muted-foreground mt-6">
        Recalculado 1x por dia automaticamente · 3 alertas por estudo (saída + 2 entradas) via Alertas de Preço
      </p>
    </div>
  );
}
