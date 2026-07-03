import { useQuery } from "@tanstack/react-query";
import { Globe, RefreshCw, Building2, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

interface FearGreed {
  score?: number | null;
  ratingEn?: string;
  ratingPt?: string;
  prevClose?: number | null;
  oneWeekAgo?: number | null;
  oneMonthAgo?: number | null;
  oneYearAgo?: number | null;
  error?: string;
}
interface Sector { name: string; ticker: string; changePct?: number | null; }

interface Filing { filingDate: string; accessionNumber: string; url: string }
interface InstitutionalFiler {
  cik: string;
  label: string;
  name?: string;
  latestFiling?: Filing;
  previousFiling?: Filing | null;
  error?: string;
}

function useInstitutionalFilings() {
  return useQuery({
    queryKey: ["institutional-filings"],
    queryFn: async () => {
      const r = await fetch("/api/institutional-filings", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { filers: InstitutionalFiler[] };
    },
    staleTime: 30 * 60_000,
  });
}

function gaugeColor(s?: number | null) {
  if (s == null) return "text-muted-foreground";
  if (s <= 25) return "text-red-500";
  if (s <= 45) return "text-orange-400";
  if (s <= 55) return "text-yellow-400";
  if (s <= 75) return "text-green-400";
  return "text-green-500";
}

function fmtFilingDate(d?: string) {
  if (!d) return "—";
  try {
    return new Date(`${d}T00:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return d;
  }
}

export default function MacroPage() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["macro"],
    queryFn: async () => {
      const r = await fetch("/api/macro", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { fearGreed: FearGreed; sectors: Sector[] };
    },
  });
  const { data: filingsData, isLoading: filingsLoading } = useInstitutionalFilings();

  const fg = data?.fearGreed;
  const sectors = data?.sectors ?? [];
  const filers = filingsData?.filers ?? [];

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Globe className="h-7 w-7 text-primary" /> MACRO
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Sentimento de mercado (Fear &amp; Greed) · performance de setores
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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Fear & Greed */}
          <div className="border border-border rounded-lg bg-card p-6 flex flex-col items-center justify-center text-center">
            <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-3">Fear &amp; Greed Index</p>
            {fg?.error ? (
              <p className="text-xs font-mono text-muted-foreground italic">{fg.error}</p>
            ) : (
              <>
                <div className={cn("text-6xl font-mono font-bold", gaugeColor(fg?.score))}>{fg?.score ?? "—"}</div>
                <div className={cn("text-sm font-mono font-bold uppercase mt-1", gaugeColor(fg?.score))}>{fg?.ratingPt ?? ""}</div>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 mt-5 text-xs font-mono w-full">
                  <span className="text-muted-foreground text-left">Ontem</span><span className="text-right tabular-nums">{fg?.prevClose ?? "—"}</span>
                  <span className="text-muted-foreground text-left">1 semana</span><span className="text-right tabular-nums">{fg?.oneWeekAgo ?? "—"}</span>
                  <span className="text-muted-foreground text-left">1 mês</span><span className="text-right tabular-nums">{fg?.oneMonthAgo ?? "—"}</span>
                  <span className="text-muted-foreground text-left">1 ano</span><span className="text-right tabular-nums">{fg?.oneYearAgo ?? "—"}</span>
                </div>
              </>
            )}
          </div>

          {/* Sectors */}
          <div className="border border-border rounded-lg bg-card p-5 lg:col-span-2">
            <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-4">Setores hoje (ETFs)</p>
            <div className="space-y-2">
              {sectors.map((s) => {
                const pct = s.changePct;
                const width = pct == null ? 0 : Math.min(Math.abs(pct) * 20, 100);
                return (
                  <div key={s.ticker} className="flex items-center gap-3">
                    <span className="font-mono text-xs text-foreground w-32 shrink-0">{s.name}</span>
                    <span className="font-mono text-[10px] text-muted-foreground w-12 shrink-0">{s.ticker}</span>
                    <div className="flex-1 h-2 bg-secondary rounded-full overflow-hidden">
                      <div className={cn("h-full rounded-full", (pct ?? 0) >= 0 ? "bg-green-400" : "bg-red-400")} style={{ width: `${width}%` }} />
                    </div>
                    <span className={cn("font-mono text-xs font-bold w-16 text-right tabular-nums",
                      pct == null ? "text-muted-foreground" : pct >= 0 ? "text-green-400" : "text-red-400")}>
                      {pct != null ? `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%` : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Filings 13F — gestores institucionais acompanhados */}
      <div className="border border-border rounded-lg bg-card p-5">
        <div className="flex items-center gap-2 mb-1">
          <Building2 className="h-4 w-4 text-primary" />
          <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
            Filings 13F — Smart Money
          </p>
        </div>
        <p className="text-xs font-mono text-muted-foreground mb-4">
          Data do último Form 13F-HR (holdings trimestrais) arquivado por cada gestor na SEC.
          Não é um diff de posições — é um lembrete pra ir ler o filing quando sair um novo.
          Lista configurável via env <code className="text-[10px]">INSTITUTIONAL_CIKS</code>.
        </p>
        {filingsLoading ? (
          <p className="text-xs font-mono text-muted-foreground animate-pulse">Carregando...</p>
        ) : filers.length === 0 ? (
          <p className="text-xs font-mono text-muted-foreground">Nenhum gestor configurado.</p>
        ) : (
          <div className="space-y-2">
            {filers.map((f) => (
              <div key={f.cik} className="flex items-center justify-between gap-3 border border-border/40 rounded-md px-3 py-2">
                <div className="min-w-0">
                  <div className="font-mono text-sm font-bold text-foreground truncate">{f.name ?? f.label}</div>
                  {f.error ? (
                    <div className="text-[11px] font-mono text-muted-foreground italic">{f.error}</div>
                  ) : (
                    <div className="text-[11px] font-mono text-muted-foreground">
                      Último: {fmtFilingDate(f.latestFiling?.filingDate)}
                      {f.previousFiling && ` · Anterior: ${fmtFilingDate(f.previousFiling.filingDate)}`}
                    </div>
                  )}
                </div>
                {f.latestFiling && (
                  <a
                    href={f.latestFiling.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 text-[11px] font-mono text-primary hover:underline shrink-0"
                  >
                    Ver filing <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
