import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Eye, Trash2, Plus } from "lucide-react";

interface WatchlistItem {
  id: number;
  ticker: string;
  notes: string | null;
  addedAt: string;
}

async function fetchWatchlist(): Promise<WatchlistItem[]> {
  const r = await fetch("/api/watchlist", { credentials: "include" });
  if (!r.ok) throw new Error("Failed to fetch");
  return r.json();
}

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [notes, setNotes] = useState("");

  const { data, isLoading } = useQuery({ queryKey: ["watchlist"], queryFn: fetchWatchlist });

  const add = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/watchlist", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.trim().toUpperCase(), notes: notes.trim() || undefined }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlist"] });
      setTicker("");
      setNotes("");
    },
  });

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await fetch(`/api/watchlist/${id}`, { method: "DELETE", credentials: "include" });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Eye className="h-7 w-7 text-primary" /> WATCHLIST
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Ativos monitorados</p>
      </div>

      {/* Add form */}
      <div className="border border-border rounded-lg bg-card p-4 space-y-3">
        <p className="text-xs font-mono text-muted-foreground uppercase tracking-widest">Adicionar Ativo</p>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Ticker (ex: AAPL)"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="flex-none w-32 bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <input
            type="text"
            placeholder="Notas (opcional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="flex-1 bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <button
            onClick={() => ticker.trim() && add.mutate()}
            disabled={!ticker.trim() || add.isPending}
            className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50"
          >
            <Plus className="h-4 w-4" /> Add
          </button>
        </div>
        {add.isError && <p className="text-xs text-red-400 font-mono">{String(add.error)}</p>}
      </div>

      {/* List */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </div>
      ) : !data?.length ? (
        <div className="p-12 text-center border border-dashed border-border rounded font-mono text-muted-foreground text-sm">
          Watchlist vazia. Adicione seu primeiro ativo.
        </div>
      ) : (
        <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <thead className="bg-secondary/40 border-b border-border">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs text-muted-foreground uppercase tracking-widest">Ticker</th>
                <th className="text-left px-4 py-2.5 text-xs text-muted-foreground uppercase tracking-widest">Notas</th>
                <th className="text-left px-4 py-2.5 text-xs text-muted-foreground uppercase tracking-widest">Adicionado</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {data.map((item, idx) => (
                <tr key={item.id} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                  <td className="px-4 py-3">
                    <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold">
                      {item.ticker}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{item.notes ?? "—"}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {new Date(item.addedAt).toLocaleDateString("pt-BR")}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => remove.mutate(item.id)}
                      className="text-muted-foreground hover:text-red-400 transition-colors"
                      title="Remover"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
