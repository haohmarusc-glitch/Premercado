import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Newspaper, RefreshCw, Search } from "lucide-react";
import { cn } from "@/lib/utils";

interface NewsItem { title: string; published?: string; summary?: string; source?: string; }
interface Item { ticker: string; news?: NewsItem[]; error?: string; }

function NewsList({ it }: { it: Item }) {
  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <span className="font-mono font-bold text-primary text-sm">{it.ticker}</span>
        <div className="flex-1 border-t border-border/40" />
        <span className="text-[10px] font-mono text-muted-foreground">{it.news?.length ?? 0} manchetes</span>
      </div>
      {it.error ? (
        <p className="text-xs font-mono text-muted-foreground italic">{it.error}</p>
      ) : !it.news || it.news.length === 0 ? (
        <p className="text-xs font-mono text-muted-foreground italic">Sem notícias recentes.</p>
      ) : (
        <div className="space-y-2">
          {it.news.map((n, i) => (
            <div key={i} className="border border-border rounded-lg bg-card p-3">
              <div className="flex items-start justify-between gap-3 mb-1">
                <p className="font-mono text-sm font-semibold text-foreground leading-snug">{n.title}</p>
              </div>
              {n.summary && <p className="font-mono text-xs text-muted-foreground leading-relaxed">{n.summary}</p>}
              <div className="flex items-center gap-2 mt-1.5 text-[10px] font-mono text-muted-foreground">
                {n.source && <span className="px-1.5 py-0.5 rounded bg-secondary">{n.source}</span>}
                {n.published && <span>{String(n.published).slice(0, 10)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function NewsPage() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["news"],
    queryFn: async () => {
      const r = await fetch("/api/news", { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { items: Item[] };
    },
  });
  const items = data?.items ?? [];

  // Busca avulsa: digite qualquer ticker (não precisa estar na carteira/cobertura)
  // e consulta /api/news?tickers=X direto -- a API já aceita qualquer símbolo,
  // sem whitelist (ver resolveTickers em routes/analysis.ts).
  const [searchInput, setSearchInput] = useState("");
  const [searchTicker, setSearchTicker] = useState<string | null>(null);
  const {
    data: searchData,
    isFetching: isSearching,
    error: searchError,
  } = useQuery({
    queryKey: ["news-search", searchTicker],
    queryFn: async () => {
      const r = await fetch(`/api/news?tickers=${encodeURIComponent(searchTicker!)}`, { credentials: "include" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Falha");
      return j as { items: Item[] };
    },
    enabled: !!searchTicker,
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Newspaper className="h-7 w-7 text-primary" /> NOTÍCIAS
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Manchetes recentes por ativo da carteira
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-secondary hover:bg-secondary/80 font-mono text-xs font-bold disabled:opacity-50 shrink-0">
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          {isFetching ? "ATUALIZANDO..." : "ATUALIZAR"}
        </button>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          const t = searchInput.trim().toUpperCase();
          if (t) setSearchTicker(t);
        }}
        className="flex items-center gap-2"
      >
        <input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Digite um ticker (ex: SMCI, AAPL, PETR4.SA)..."
          className="flex-1 px-3 py-2 rounded-md border border-border bg-secondary font-mono text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button
          type="submit"
          disabled={!searchInput.trim() || isSearching}
          className="flex items-center gap-2 px-4 py-2 rounded-md border border-primary bg-primary text-primary-foreground hover:bg-primary/90 font-mono text-xs font-bold disabled:opacity-50 shrink-0"
        >
          <Search className={cn("h-3.5 w-3.5", isSearching && "animate-spin")} />
          {isSearching ? "BUSCANDO..." : "BUSCAR"}
        </button>
      </form>

      {searchTicker && (
        <div className="border border-primary/30 rounded-lg bg-primary/5 p-4">
          {isSearching ? (
            <div className="p-4 text-center text-muted-foreground font-mono text-sm">Buscando {searchTicker}...</div>
          ) : searchError ? (
            <div className="font-mono text-red-400 text-sm">{String(searchError)}</div>
          ) : (
            (searchData?.items ?? []).map((it) => <NewsList key={it.ticker} it={it} />)
          )}
        </div>
      )}

      {isLoading ? (
        <div className="p-12 text-center text-muted-foreground font-mono text-sm">Carregando...</div>
      ) : error ? (
        <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">{String(error)}</div>
      ) : (
        <div className="space-y-6">
          {items.map((it) => <NewsList key={it.ticker} it={it} />)}
        </div>
      )}
    </div>
  );
}
