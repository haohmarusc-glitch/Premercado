import { useQuery } from "@tanstack/react-query";
import { Landmark, Waves } from "lucide-react";

// ─── SmartMoneyCard ────────────────────────────────────────────────────────
// Consome GET /api/alt-data (get_alt_data.py): negociações do Congresso
// (Quiver Quant) + prints de dark pool (Unusual Whales). Ambos os
// provedores são pagos — sem a chave configurada (QUIVER_API_KEY /
// UNUSUAL_WHALES_API_KEY) a seção mostra como ativar em vez de dado nenhum.

interface CongressTrade {
  ticker: string;
  representative?: string | null;
  chamber?: string;
  transaction?: string;
  range?: string;
  transactionDate?: string;
  filedDate?: string;
}

interface DarkPoolTrade {
  ticker: string;
  price?: number | string | null;
  size?: number | null;
  premium?: number | string | null;
  executedAt?: string | null;
}

interface AltDataSection<T> {
  configured: boolean;
  trades?: T[];
  message?: string;
  error?: string;
}

interface AltDataItem {
  congress: AltDataSection<CongressTrade>;
  darkPool: AltDataSection<DarkPoolTrade>;
}

async function fetchAltData(symbol: string): Promise<AltDataItem | null> {
  const res = await fetch(`/api/alt-data?tickers=${encodeURIComponent(symbol)}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`alt-data ${res.status}`);
  // get_alt_data.py já filtra pelos tickers pedidos e devolve um único
  // objeto {congress, darkPool} (não {items: [...]} como os outros scripts) --
  // ok aqui porque este card sempre pede 1 ticker por vez.
  return (await res.json()) as AltDataItem;
}

function useAltData(symbol: string) {
  return useQuery({
    queryKey: ["alt-data", symbol],
    queryFn: () => fetchAltData(symbol),
    staleTime: 15 * 60_000,
    retry: 1,
  });
}

function NotConfigured({ message }: { message?: string }) {
  return <p className="text-[11px] font-mono text-muted-foreground italic">{message ?? "Não configurado."}</p>;
}

export function SmartMoneyCard({ symbol }: { symbol: string }) {
  const { data, isLoading } = useAltData(symbol);

  // Se nenhum dos dois provedores está configurado, não vale a pena ocupar
  // espaço na tela com um card vazio -- omite o card inteiro.
  if (!isLoading && data && !data.congress.configured && !data.darkPool.configured) {
    return null;
  }

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-secondary/30">
        <span className="font-mono font-bold text-sm tracking-wider uppercase text-muted-foreground">
          Smart Money — {symbol}
        </span>
      </div>

      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Congresso */}
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-widest">
            <Landmark className="h-3.5 w-3.5" />
            Congresso (STOCK Act)
          </div>
          {isLoading ? (
            <p className="text-[11px] font-mono text-muted-foreground animate-pulse">Carregando...</p>
          ) : !data?.congress.configured ? (
            <NotConfigured message={data?.congress.message} />
          ) : data.congress.error ? (
            <p className="text-[11px] font-mono text-red-400">{data.congress.error}</p>
          ) : data.congress.trades && data.congress.trades.length > 0 ? (
            <div className="space-y-1.5">
              {data.congress.trades.slice(0, 5).map((t, i) => (
                <div key={i} className="text-[11px] font-mono text-muted-foreground flex items-start gap-1.5">
                  <span className={t.transaction?.toLowerCase().includes("sale") ? "text-red-400" : "text-green-400"}>
                    {t.transaction?.toLowerCase().includes("sale") ? "▼" : "▲"}
                  </span>
                  <span>
                    {t.representative} ({t.chamber === "senate" ? "Senado" : "Câmara"}) — {t.transaction} {t.range}
                    {t.transactionDate ? ` em ${t.transactionDate}` : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] font-mono text-muted-foreground">Nenhuma negociação recente.</p>
          )}
        </div>

        {/* Dark pool */}
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-widest">
            <Waves className="h-3.5 w-3.5" />
            Dark Pool
          </div>
          {isLoading ? (
            <p className="text-[11px] font-mono text-muted-foreground animate-pulse">Carregando...</p>
          ) : !data?.darkPool.configured ? (
            <NotConfigured message={data?.darkPool.message} />
          ) : data.darkPool.error ? (
            <p className="text-[11px] font-mono text-red-400">{data.darkPool.error}</p>
          ) : data.darkPool.trades && data.darkPool.trades.length > 0 ? (
            <div className="space-y-1.5">
              {data.darkPool.trades.slice(0, 5).map((t, i) => (
                <div key={i} className="text-[11px] font-mono text-muted-foreground">
                  {t.size?.toLocaleString("en-US")} @ ${t.price} {t.premium ? `(prêmio $${Number(t.premium).toLocaleString("en-US")})` : ""}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] font-mono text-muted-foreground">Nenhum print recente.</p>
          )}
        </div>
      </div>
    </div>
  );
}
