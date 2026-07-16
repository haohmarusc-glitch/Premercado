import { useState } from "react";
import { ClipboardList, MinusCircle, PlusCircle, X } from "lucide-react";

// ─── AlertsChangeSummaryCard ─────────────────────────────────────────────────
// Resumo estático de uma rodada específica de ajustes manuais na Gestão de
// Alertas (não é um changelog automático -- não existe log de auditoria de
// criação/remoção de alertas hoje). Fica no topo do Dashboard até o usuário
// dispensar; a escolha fica salva no localStorage pra não voltar a aparecer
// depois de fechado.

const DISMISS_KEY = "premercado:alerts-change-summary:2026-07-16";

interface RemovedAlert {
  ticker: string;
  id: number;
  reason: string;
}

interface NewAlert {
  ticker: string;
  id: number;
  condition: string;
  reason: string;
}

const REMOVED: RemovedAlert[] = [
  {
    ticker: "GOOGL",
    id: 116,
    reason: "Removido por ter disparado recentemente e para evitar duplicidade com um alerta mais calibrado (ID 130).",
  },
];

const CREATED: NewAlert[] = [
  {
    ticker: "TSM",
    id: 131,
    condition: "Abaixo -7,5% (Threshold: -7,5%, calibrado pelo ATR_pct de 4,88% x 1,5 = 7,3%)",
    reason: "Acompanhar sell-off pós-earnings, indicando deterioração estrutural.",
  },
  {
    ticker: "INTC",
    id: 132,
    condition: "Abaixo -14% (Threshold: -14%, calibrado pelo ATR_pct de 9,45% x 1,5 = 14,2%)",
    reason: "Cobrir risco de queda acelerada pré-earnings ou guidance decepcionante.",
  },
  {
    ticker: "SKHY",
    id: 133,
    condition: "Abaixo -10% (Threshold: -10%)",
    reason: "Monitorar a aproximação do suporte de 52 semanas ($151,30) dado o terceiro dia de queda desde o IPO.",
  },
];

export function AlertsChangeSummaryCard() {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISS_KEY) === "1");

  if (dismissed) return null;

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  };

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border bg-secondary/30">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-primary" />
          <span className="font-mono font-bold text-sm tracking-wider uppercase text-muted-foreground">
            Gestão de Alertas — Sumário das Mudanças
          </span>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Dispensar resumo"
          title="Dispensar"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="p-4 space-y-3">
        <div>
          <div className="flex items-center gap-1.5 text-[11px] font-mono font-bold uppercase tracking-widest text-red-400 mb-1.5">
            <MinusCircle className="h-3.5 w-3.5" />
            Alerta Removido
          </div>
          {REMOVED.map((a) => (
            <div key={a.id} className="border border-red-500/30 bg-red-500/5 rounded-md px-3 py-2">
              <span className="font-mono text-xs font-bold text-foreground">
                {a.ticker} <span className="text-muted-foreground font-normal">(ID {a.id})</span>
              </span>
              <p className="text-[11px] font-mono text-muted-foreground mt-0.5 leading-snug">{a.reason}</p>
            </div>
          ))}
        </div>

        <div>
          <div className="flex items-center gap-1.5 text-[11px] font-mono font-bold uppercase tracking-widest text-green-400 mb-1.5">
            <PlusCircle className="h-3.5 w-3.5" />
            Novos Alertas Criados
          </div>
          <div className="space-y-2">
            {CREATED.map((a) => (
              <div key={a.id} className="border border-green-500/30 bg-green-500/5 rounded-md px-3 py-2">
                <span className="font-mono text-xs font-bold text-foreground">
                  {a.ticker} <span className="text-muted-foreground font-normal">(ID {a.id})</span>
                </span>
                <p className="text-[11px] font-mono text-foreground/80 mt-0.5">{a.condition}</p>
                <p className="text-[11px] font-mono text-muted-foreground mt-0.5 leading-snug">Razão: {a.reason}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
