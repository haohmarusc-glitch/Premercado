import { useGetTickerQuotes, getGetTickerQuotesQueryKey } from "@workspace/api-client-react";
import { AlertTriangle } from "lucide-react";

/**
 * Faixa de aviso quando as cotações vêm da fonte de contingência.
 *
 * Fica no layout, e não em cada tela, por uma razão do próprio mecanismo: o
 * fallback de cotação é decidido por LOTE (ver get_quotes.aplicar_fallback) —
 * ou o Yahoo respondeu para todos, ou para nenhum. Não existe estado em que
 * uma tela esteja ao vivo e outra atrasada, então um aviso global é mais
 * honesto que sete selos espalhados, e cobre também as telas que ninguém
 * lembrou de instrumentar.
 *
 * Reaproveita a mesma query key do resto do app: o react-query deduplica, então
 * isto não gera requisição nova.
 */
export function AvisoCotacaoAtrasada() {
  const { data: quotes } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), refetchInterval: 60_000, staleTime: 55_000 },
  });

  const atrasadas = (quotes ?? []).filter((q) => q.isDelayed);
  if (atrasadas.length === 0) return null;

  // O aviso do backend já vem escrito com a data do fechamento servido; usar o
  // primeiro evita reescrever aqui uma frase que o Python já sabe montar.
  const detalhe = atrasadas.find((q) => (q.sourceWarnings ?? []).length > 0)?.sourceWarnings?.[0];

  return (
    <div
      role="status"
      className="mb-4 flex items-start gap-2 rounded border border-yellow-500/40 bg-yellow-500/5 px-3 py-2 font-mono text-xs text-yellow-400"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div>
        <span className="font-bold">Cotações atrasadas.</span>{" "}
        A fonte ao vivo não respondeu; os preços vêm do último fechamento pela fonte de
        contingência. Não use para decidir entrada ou saída agora.
        {detalhe && <div className="mt-1 text-yellow-400/70">{detalhe}</div>}
      </div>
    </div>
  );
}
