import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "wouter";
import { Users, RefreshCw, Bell } from "lucide-react";
import { cn } from "@/lib/utils";

interface AnalystBlock {
  consensus?: string | null;
  recommendationMean?: number | null;
  numAnalysts?: number | null;
  targetMean?: number | null;
  targetHigh?: number | null;
  targetLow?: number | null;
  upsidePct?: number | null;
}
interface Item { ticker: string; price?: number | null; analyst?: AnalystBlock; error?: string; }

function fmt(n: number | null | undefined, d = 2) { return n == null ? "—" : `$${n.toFixed(d)}`; }

function consensusColor(c?: string | null) {
  if (!c) return "bg-muted text-muted-foreground";
  if (c.includes("forte") && c.includes("compra")) return "bg-green-500/20 text-green-400";
  if (c.includes("compra")) return "bg-green-500/10 text-green-400";
  if (c.includes("venda")) return "bg-red-500/10 text-red-400";
  return "bg-yellow-500/10 text-yellow-400";
}

// Menu "criar alerta neste alvo" -- mesmo padrão do menu de botão direito no
// gráfico (ver chartMenuEl em dashboard.tsx/portfolio.tsx): navega pra
// /alerts com symbol/price/condition pré-preenchidos, sem persistir nada
// aqui. A condição (acima/abaixo) é calculada contra a cotação atual, não
// fixa por alvo (o alvo baixo às vezes já está acima do preço, e vice-versa).
interface TargetAlertMenuState { ticker: string; price: number; x: number; y: number; targets: { label: string; value: number }[] }

export default function AnalystsPage() {
  const [, navigate] = useLocation();
  const [targetMenu, setTargetMenu] = useState<TargetAlertMenuState | null>(null);

  useEffect(() => {
    if (!targetMenu) return;
    const close = () => setTargetMenu(null);
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [targetMenu]);

  const openTargetMenu = (e: React.MouseEvent, it: Item) => {
    e.stopPropagation();
    if (it.price == null || !it.analyst) return;
    const targets = [
      { label: "Alvo Baixo", value: it.analyst.targetLow },
      { label: "Alvo Médio", value: it.analyst.targetMean },
      { label: "Alvo Alto", value: it.analyst.targetHigh },
    ].filter((t): t is { label: string; value: number } => t.value != null);
    if (!targets.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    // A coluna "Alerta" é a última à direita -- sem o clamp, o menu (min-w
    // 200px) nasce cortado fora da tela pra qualquer botão perto da borda
    // (mesmo ajuste já feito em openChartMenu, dashboard.tsx).
    const x = Math.min(rect.left, window.innerWidth - 210);
    setTargetMenu({ ticker: it.ticker, price: it.price, x, y: rect.bottom + 4, targets });
  };

  const targetMenuEl = targetMenu && (
    <div
      className="fixed z-[60] min-w-[200px] rounded-md border border-border bg-card shadow-lg py-1 font-mono text-xs"
      style={{ left: targetMenu.x, top: targetMenu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="px-3 py-1.5 text-muted-foreground border-b border-border/50">
        {targetMenu.ticker} · alertar no alvo
      </div>
      {targetMenu.targets.map((t) => {
        const condition = t.value >= targetMenu.price ? "above" : "below";
        return (
          <button
            key={t.label}
            type="button"
            className="w-full text-left px-3 py-1.5 hover:bg-secondary transition-colors flex items-center justify-between gap-3"
            onClick={() => {
              navigate(`/alerts?symbol=${targetMenu.ticker}&price=${t.value.toFixed(2)}&condition=${condition}`);
              setTargetMenu(null);
            }}
          >
            <span>{t.label}</span>
            <span className={condition === "above" ? "text-green-400 font-bold" : "text-red-400 font-bold"}>
              {fmt(t.value)}
            </span>
          </button>
        );
      })}
    </div>
  );

  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["fundamentals-analysts"],
    queryFn: async () => {
      const r = await fetch("/api/fundamentals", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { items: Item[] };
    },
  });
  const items = data?.items ?? [];

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Users className="h-7 w-7 text-primary" /> ANALISTAS
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Consenso · preço-alvo · upside potencial
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold disabled:opacity-50 shrink-0">
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          {isFetching ? "ATUALIZANDO..." : "ATUALIZAR"}
        </button>
      </div>

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando...</div>
      ) : error ? (
        <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">{String(error)}</div>
      ) : (
        <div className="border border-border rounded-lg overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/30">
              <tr className="text-[10px] text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-3 py-2.5">Ticker</th>
                <th className="text-right px-3 py-2.5">Preço</th>
                <th className="text-left px-3 py-2.5">Consenso</th>
                <th className="text-right px-3 py-2.5"># Analistas</th>
                <th className="text-right px-3 py-2.5">Alvo Médio</th>
                <th className="text-right px-3 py-2.5">Alvo Baixo</th>
                <th className="text-right px-3 py-2.5">Alvo Alto</th>
                <th className="text-right px-3 py-2.5">Upside</th>
                <th className="text-center px-3 py-2.5">Alerta</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={it.ticker} className={cn("border-t border-border/30", idx % 2 === 0 ? "bg-card" : "bg-secondary/10")}>
                  <td className="px-3 py-2.5 font-bold text-primary">{it.ticker}</td>
                  {it.error || !it.analyst ? (
                    <td colSpan={8} className="px-3 py-2.5 text-muted-foreground italic text-xs">{it.error ?? "sem dados"}</td>
                  ) : (
                    <>
                      <td className="px-3 py-2.5 text-right tabular-nums">{fmt(it.price)}</td>
                      <td className="px-3 py-2.5">
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-semibold uppercase", consensusColor(it.analyst.consensus))}>
                          {it.analyst.consensus ?? "—"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{it.analyst.numAnalysts ?? "—"}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{fmt(it.analyst.targetMean)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{fmt(it.analyst.targetLow)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{fmt(it.analyst.targetHigh)}</td>
                      <td className={cn("px-3 py-2.5 text-right tabular-nums font-bold",
                        it.analyst.upsidePct == null ? "text-muted-foreground"
                        : it.analyst.upsidePct >= 0 ? "text-green-400" : "text-red-400")}>
                        {it.analyst.upsidePct != null ? `${it.analyst.upsidePct >= 0 ? "+" : ""}${it.analyst.upsidePct.toFixed(1)}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <button
                          type="button"
                          onClick={(e) => openTargetMenu(e, it)}
                          disabled={it.price == null}
                          title="Criar alerta num alvo de analista"
                          className="p-1.5 rounded-md border border-border text-muted-foreground hover:text-primary hover:bg-secondary transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                        >
                          <Bell className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {targetMenuEl}
      <p className="text-xs font-mono text-muted-foreground">
        <span className="text-green-400">Upside positivo</span> = preço-alvo médio acima da cotação atual (potencial de alta). Consenso "compra forte" com muitos analistas é o sinal mais robusto.
      </p>
    </div>
  );
}
