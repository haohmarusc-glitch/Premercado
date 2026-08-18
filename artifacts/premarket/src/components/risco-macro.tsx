/**
 * Seção de risco macro setorial (IA/semis) na tela de Macro.
 *
 * O que esta tela precisa comunicar, e que a maioria dos painéis de risco erra:
 * a diferença entre "medi e está calmo" e "não consegui medir". São TRÊS
 * estados por sinal, não dois. Cinza silencioso lido como "tudo bem" num painel
 * de risco é pior que erro na tela -- foi exatamente o bug corrigido no módulo
 * (ver macro_risk.py), e desfazê-lo aqui, no visual, anularia o conserto.
 */
import { useQuery } from "@tanstack/react-query";
import { ShieldAlert, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

const SINAIS: Array<{ chave: string; rotulo: string; explica: string }> = [
  { chave: "RATE_SHOCK", rotulo: "Choque de juros",
    explica: "Yield de 30 anos disparando. Juros longos são veneno direto para growth, cujo valor está em fluxo de caixa distante." },
  { chave: "ASIA_MEMORY_CONTAGION", rotulo: "Contágio Ásia",
    explica: "Coreia fecha 6-8h antes da abertura dos EUA — é leading indicator de verdade, não coincidente." },
  { chave: "PRICED_FOR_PERFECTION", rotulo: "Bateu e caiu",
    explica: "Balanço numericamente bom com a ação despencando: a expectativa estava acima do fundamento." },
  { chave: "CHINA_COMPETITION_RISK", rotulo: "Concorrência China",
    explica: "Notícia atacando a tese de ESCASSEZ que sustenta os múltiplos do setor." },
  { chave: "OVEREXTENDED_SECTOR", rotulo: "Setor esticado",
    explica: "Não é gatilho, é amplificador: com todo mundo no mesmo trade, qualquer arranhão vira avalanche." },
  { chave: "GEOPOLITICAL_OIL_SHOCK", rotulo: "Petróleo/geopolítica",
    explica: "Petróleo e yield subindo juntos: choque de juros vindo de fora, não de inflação doméstica." },
];

type Sinal = {
  active?: boolean;
  status?: "ok" | "sem_dado" | "nao_aplicavel";
  severity?: "low" | "medium" | "high";
  motivo?: string;
  [k: string]: unknown;
};

type Retrato = {
  aggregate_score?: number | null;
  cobertura_pct?: number;
  fontesDegradadas?: Record<string, string>;
  snapshotDate?: string;
  [k: string]: unknown;
};

// Os números que o sinal expõe, por chave. Fora daqui o cartão só mostra o
// estado -- despejar o objeto inteiro encheria a tela de campo interno.
const NUMEROS: Record<string, Array<[string, string, (v: number) => string]>> = {
  RATE_SHOCK: [
    ["yield_30y_today", "30Y", (v) => `${v.toFixed(2)}%`],
    ["delta_bps", "Δ", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(0)}bps`],
  ],
  ASIA_MEMORY_CONTAGION: [
    ["kospi_pct", "Kospi", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`],
    ["sk_hynix_pct", "SK Hynix", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`],
  ],
  OVEREXTENDED_SECTOR: [["retorno_pct", "SOX 9s", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`]],
  GEOPOLITICAL_OIL_SHOCK: [
    ["wti_hoje", "WTI", (v) => `$${v.toFixed(2)}`],
    ["oleo_delta_pct", "Δ", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`],
  ],
  CHINA_COMPETITION_RISK: [["negativas", "negativas", (v) => String(v)]],
  PRICED_FOR_PERFECTION: [
    ["premarket_reaction_pct", "reação", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`],
  ],
};

function numerosDo(chave: string, s: Sinal): string[] {
  return (NUMEROS[chave] ?? [])
    .map(([campo, rotulo, fmt]) => {
      const v = s[campo];
      return typeof v === "number" && Number.isFinite(v) ? `${rotulo} ${fmt(v)}` : null;
    })
    .filter((x): x is string => x !== null);
}

function Cartao({ chave, rotulo, explica, sinal }: {
  chave: string; rotulo: string; explica: string; sinal?: Sinal;
}) {
  const status = sinal?.status ?? "sem_dado";
  const ativo = Boolean(sinal?.active);
  const semDado = status === "sem_dado";
  const naoAplicavel = status === "nao_aplicavel";
  const alta = sinal?.severity === "high";

  return (
    <div
      title={explica}
      className={cn(
        "border rounded-lg p-4 flex flex-col gap-2 transition-colors",
        // Listrado: o estado "sem dado" precisa ser reconhecível de relance,
        // sem ler o texto. Cinza igual ao inativo é o que faz cegueira parecer
        // calmaria.
        semDado && "border-dashed border-amber-500/40 bg-[repeating-linear-gradient(45deg,transparent,transparent_6px,rgba(245,158,11,0.06)_6px,rgba(245,158,11,0.06)_12px)]",
        !semDado && ativo && alta && "border-red-500/50 bg-red-500/5",
        !semDado && ativo && !alta && "border-amber-500/50 bg-amber-500/5",
        !semDado && !ativo && "border-border bg-card",
      )}
    >
      <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">{rotulo}</p>
      <p className={cn(
        "font-mono text-sm font-bold",
        semDado && "text-amber-500/80",
        !semDado && ativo && alta && "text-red-400",
        !semDado && ativo && !alta && "text-amber-400",
        !semDado && !ativo && "text-muted-foreground",
      )}>
        {semDado ? "SEM DADO" : naoAplicavel ? "não se aplica" : ativo ? `ATIVO · ${sinal?.severity}` : "inativo"}
      </p>
      {semDado || naoAplicavel ? (
        <p className="font-mono text-[10px] text-muted-foreground leading-snug">{sinal?.motivo || "—"}</p>
      ) : (
        <p className="font-mono text-[11px] text-muted-foreground">
          {numerosDo(chave, sinal ?? {}).join(" · ") || "—"}
        </p>
      )}
    </div>
  );
}

export function RiscoMacro() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["macro-risk"],
    queryFn: async () => {
      const r = await fetch("/api/macro-risk", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as Retrato;
    },
    // A coleta bate em FRED + 3 tickers + notícias. Refetch a cada foco de aba
    // gastaria rede sem mudar nada: o retrato é do PREGÃO, não do minuto.
    staleTime: 30 * 60_000,
    refetchOnWindowFocus: false,
  });

  const score = data?.aggregate_score;
  const cobertura = data?.cobertura_pct ?? 0;
  const degradadas = Object.keys(data?.fontesDegradadas ?? {}).length;

  return (
    <div className="border border-border rounded-lg bg-card p-6 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest flex items-center gap-2">
            <ShieldAlert className="h-3.5 w-3.5 text-primary" /> Risco macro — IA / semicondutores
          </p>
          <p className="font-mono text-[11px] text-muted-foreground mt-1">
            Não é sinal de compra ou venda: modula o tamanho de posição sugerido.
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-[10px] font-bold disabled:opacity-50 shrink-0">
          <RefreshCw className={cn("h-3 w-3", isFetching && "animate-spin")} />
          {isFetching ? "COLETANDO..." : "COLETAR"}
        </button>
      </div>

      {isLoading ? (
        <p className="font-mono text-sm text-muted-foreground py-6 text-center">Coletando as seis fontes...</p>
      ) : error ? (
        <p className="font-mono text-sm text-red-400">{String(error)}</p>
      ) : (
        <>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="font-mono text-3xl font-bold text-foreground">
              {/* Score e cobertura andam JUNTOS. O número sozinho não diz se foi
                  apurado sobre tudo ou sobre um terço, e as duas coisas se
                  parecem na tela. */}
              {typeof score === "number" ? score : "—"}
              <span className="text-base text-muted-foreground">/100</span>
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              cobertura {cobertura}%
              {typeof score !== "number" && " · cobertura baixa demais para um score"}
              {degradadas > 0 && ` · ${degradadas} fonte(s) sem dado`}
            </span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {SINAIS.map((s) => (
              <Cartao key={s.chave} {...s} sinal={data?.[s.chave] as Sinal | undefined} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
