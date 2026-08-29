import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown, Minus, AlertTriangle } from "lucide-react";
import { useStaggerReady } from "@/hooks/use-stagger-ready";

// ─── TrendCard ───────────────────────────────────────────────────────────────
// Consome GET /api/trend (get_trend.py): confluência técnico + notícias.
// Filosofia: calculadora, não decisor — mostra os componentes, não dá ordem.

interface TrendComponents {
  maCruzamento?: string | null;
  maCruzamentoNota?: string | null;
  precoVsSma200?: string | null;
  estrutura?: string | null;
  macd?: string | null;
  rsi?: number | null;
  rsiNota?: string | null;
}

interface TrendNews {
  label: string;
  score: number;
  positivas: number;
  negativas: number;
  // Contagem VISÍVEL de propósito. Um filtro que roda calado devolve
  // "0 negativas" sem dizer que jogou metade do feed fora -- e foi assim que
  // duas manchetes da AMD viraram sentimento da ARM sem ninguém notar.
  ambiguas?: number;
  descartadas?: number;
  // Denominador do score. Sem ele na tela, "1 de 8 analisadas" e "8 de 8"
  // ficam indistinguíveis no rótulo -- foi assim que UMA manchete assinou
  // "alta forte CONFIRMADA por fluxo de notícias positivo" em NVDA.
  classificadas?: number;
  minimoParaRotular?: number;
  analisadas: number;
  destaques: { title: string; tone: string; ts?: number | null }[];
}

export interface TrendItem {
  ticker: string;
  price?: number;
  trend?: string;
  score?: number;
  components?: TrendComponents;
  news?: TrendNews;
  confluence?: string;
  sinal?: string;
  sinalMotivo?: string;
  error?: string;
}

async function fetchTrend(symbol: string): Promise<TrendItem | null> {
  const res = await fetch(`/api/trend?tickers=${encodeURIComponent(symbol)}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`trend ${res.status}`);
  const data = (await res.json()) as { items?: TrendItem[] };
  return data.items?.[0] ?? null;
}

// Busca o sinal de tendência de TODA a cesta de tickers configurada de uma
// vez (mesmo endpoint, sem o parametro `tickers` -> backend usa
// settings.tickers). Usado pelo Swing Screener.
export async function fetchTrendBasket(): Promise<TrendItem[]> {
  const res = await fetch(`/api/trend`, { credentials: "include" });
  if (!res.ok) throw new Error(`trend ${res.status}`);
  const data = (await res.json()) as { items?: TrendItem[] };
  return data.items ?? [];
}

function trendVisual(trend?: string) {
  if (trend === "alta forte") return { color: "#22c55e", Icon: TrendingUp, strong: true };
  if (trend === "alta") return { color: "#22c55e", Icon: TrendingUp, strong: false };
  if (trend === "baixa forte") return { color: "#ef4444", Icon: TrendingDown, strong: true };
  if (trend === "baixa") return { color: "#ef4444", Icon: TrendingDown, strong: false };
  return { color: "#9ca3af", Icon: Minus, strong: false };
}

function ComponentPill({ label, value, good }: { label: string; value: string; good: boolean | null }) {
  const color = good == null ? "text-muted-foreground" : good ? "text-green-500" : "text-red-500";
  return (
    <div className="flex items-center justify-between gap-2 text-[11px] font-mono">
      <span className="text-muted-foreground">{label}</span>
      <span className={color}>{value}</span>
    </div>
  );
}

export function useTrend(symbol: string) {
  // 400ms: chave compartilhada entre TrendCard e o overlay de marcadores do
  // PriceChart -- os dois pedem o MESMO symbol no mesmo mount, então o
  // react-query já funde num fetch só; o atraso é só pra não competir com
  // quotes/market-alerts/exit-plan na primeira leva de vagas do servidor.
  const ready = useStaggerReady(400);
  return useQuery({
    queryKey: ["trend", symbol],
    queryFn: () => fetchTrend(symbol),
    enabled: ready,
    staleTime: 5 * 60_000, // técnico diário não muda a cada segundo
    retry: 1,
  });
}

export function TrendCard({ symbol }: { symbol: string }) {
  const { data, isLoading, isError } = useTrend(symbol);

  if (isLoading) {
    return (
      <div className="border border-border rounded-lg bg-card p-4">
        <span className="text-xs font-mono text-muted-foreground animate-pulse">
          Analisando tendência de {symbol}...
        </span>
      </div>
    );
  }

  if (isError || !data || data.error) {
    return (
      <div className="border border-border rounded-lg bg-card p-4">
        <span className="text-xs font-mono text-muted-foreground">
          Tendência indisponível{data?.error ? ` — ${data.error}` : ""}.
        </span>
      </div>
    );
  }

  const { color, Icon, strong } = trendVisual(data.trend);
  const c = data.components ?? {};
  const news = data.news;
  const diverge = (data.confluence ?? "").includes("DIVERGÊNCIA");

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      {/* Header: direção + score */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-secondary/30">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4" style={{ color }} />
          <span className="font-mono font-bold text-sm tracking-wider uppercase" style={{ color }}>
            {data.trend}
          </span>
          {strong && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border" style={{ color, borderColor: color }}>
              score {data.score}
            </span>
          )}
        </div>
        {data.sinal ? (
          <span
            className="text-[11px] font-mono font-bold px-2 py-1 rounded uppercase tracking-wider"
            title={data.sinalMotivo}
            style={{
              background: data.sinal === "compra" ? "#22c55e22" : data.sinal === "venda" ? "#ef444422" : "transparent",
              color: data.sinal === "compra" ? "#22c55e" : data.sinal === "venda" ? "#ef4444" : "#9ca3af",
              border: `1px solid ${data.sinal === "compra" ? "#22c55e" : data.sinal === "venda" ? "#ef4444" : "#3f3f46"}`,
            }}
          >
            {data.sinal}
          </span>
        ) : (
          <span className="text-[10px] font-mono text-muted-foreground uppercase">Tendência</span>
        )}
      </div>

      <div className="p-4 space-y-3">
        {/* Confluência técnico × notícias */}
        <div
          className={`flex items-start gap-2 text-xs font-mono rounded px-3 py-2 border ${
            diverge ? "border-yellow-600/50 bg-yellow-500/5 text-yellow-500" : "border-border bg-secondary/20"
          }`}
        >
          {diverge && <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />}
          <span>
            {data.confluence}
            {data.sinalMotivo && (
              <span className="block text-muted-foreground mt-0.5">Sinal: {data.sinalMotivo}.</span>
            )}
          </span>
        </div>

        {/* Componentes técnicos */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
          <ComponentPill
            label="SMA20 × SMA50"
            value={c.maCruzamento ?? "—"}
            // Nota presente = nível e direção discordam; sem cor, para não
            // afirmar uma direção que o próprio componente não sustenta.
            good={c.maCruzamentoNota ? null : c.maCruzamento ? c.maCruzamento === "alta" : null}
          />
          <ComponentPill
            label="Preço × SMA200"
            value={c.precoVsSma200 ?? "—"}
            good={c.precoVsSma200 ? c.precoVsSma200 === "acima" : null}
          />
          <ComponentPill
            label="Estrutura"
            value={c.estrutura ?? "—"}
            good={c.estrutura === "indefinida" || !c.estrutura ? null : c.estrutura === "alta"}
          />
          <ComponentPill
            label="MACD"
            value={c.macd ?? "—"}
            good={c.macd ? c.macd === "bullish" : null}
          />
          <ComponentPill
            label="RSI (Wilder)"
            value={c.rsi != null ? String(c.rsi) : "—"}
            good={c.rsi == null ? null : c.rsi <= 70 && c.rsi >= 30}
          />
          {news && (
            <ComponentPill
              label={`Notícias (${news.positivas}+/${news.negativas}-${
                news.ambiguas ? `/${news.ambiguas}~` : ""
              })`}
              value={
                news.classificadas != null && news.minimoParaRotular != null
                && news.classificadas < news.minimoParaRotular
                  // "amostra N" foi mal lido três vezes por leitores atentos
                  // (AOSL 28/08, duas auditorias externas; PDD no mesmo dia):
                  // todos entenderam N como "quantas manchetes apareceram" e
                  // acharam que contradizia o placar ao lado. N é outra coisa
                  // -- é `positivas + negativas`, o DENOMINADOR do score, que
                  // exclui as ambíguas de propósito. Com 0+/1-/5~ ele vale 1,
                  // e "amostra 1" ao lado de seis manchetes parece erro.
                  //
                  // O número estava certo; o rótulo é que não dizia qual
                  // pergunta ele responde. Agora diz -- e diz também por que
                  // o rótulo ficou neutro.
                  // TERCEIRA redação. "amostra N" foi mal lido três vezes;
                  // "N de M com tom definido" foi mal lido de novo, agora por
                  // um auditor externo no MRVL (29/08) -- ele somou 0+/1-/5~,
                  // achou seis manchetes, leu "1 de 3" como fração delas e
                  // reportou como inconsistência.
                  //
                  // O defeito da forma "N de M": "de" anuncia denominador, e
                  // M não é denominador de nada -- é um PISO. Enquanto os
                  // dois números aparecerem ligados por "de", o leitor vai
                  // procurar a razão entre eles.
                  //
                  // Agora eles vêm separados por papel: "só N" diz escassez,
                  // "mínimo M para rotular" diz limiar e para que serve.
                  ? `${news.label} · só ${news.classificadas} com tom definido, mínimo ${news.minimoParaRotular} para rotular`
                  : news.label
              }
              good={news.label === "neutro" || news.label === "misto" ? null : news.label === "positivo"}
            />
          )}
        </div>

        {/* Headlines destacadas */}
        {news && news.destaques.length > 0 && (
          <div className="space-y-1 pt-1 border-t border-border">
            {news.destaques.slice(0, 3).map((d, i) => (
              <div key={i} className="flex items-start gap-1.5 text-[11px] font-mono text-muted-foreground">
                {/* "misto" é um terceiro estado real: manchete com ressalva
                    ("cresceu, MAS o múltiplo...") não é alta nem queda. Sem
                    ele, toda ressalva saía com a seta vermelha de queda. */}
                <span className={
                  d.tone === "positivo" ? "text-green-500"
                  : d.tone === "misto" ? "text-amber-500"
                  : "text-red-500"
                }>
                  {d.tone === "positivo" ? "▲" : d.tone === "misto" ? "◆" : "▼"}
                </span>
                <span className="line-clamp-1">{d.title}</span>
              </div>
            ))}
            {/* Dizer o que ficou de FORA. Sem esta linha o leitor vê "0
                negativas" e não tem como saber que o feed trouxe matéria de
                outra empresa e ela foi descartada. */}
            {!!news.descartadas && (
              <div className="text-[10px] text-muted-foreground/70 pt-0.5">
                {news.descartadas === 1
                  ? "1 manchete era de outro papel e ficou fora da conta"
                  : `${news.descartadas} manchetes eram de outros papéis e ficaram fora da conta`}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
