/**
 * Converte o `usage` que os scripts Python devolvem numa linha de agent_runs
 * — a tabela que a tela "Gastos com IA" lê.
 *
 * Existe como módulo puro (sem banco) porque a regra tem casos que erram
 * fácil e merecem teste: modelo sem preço tabelado devolve custo `null` (que
 * NÃO é zero — é "não sei quanto custou"), chamada sem tokens não deve virar
 * linha vazia, e falha depois de o provedor já ter cobrado ainda precisa
 * virar registro, marcada como failed.
 */

export interface UsoLlm {
  calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  total_cost_usd?: number | null;
  providers?: { provider?: string; model?: string }[];
}

export interface LinhaGasto {
  startedAt: Date;
  finishedAt: Date;
  status: "success" | "failed";
  trigger: string;
  mode: string;
  durationMs: number;
  errorMessage: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  costUsd: number | null;
  llmProvider: string | null;
  llmModel: string | null;
}

/** Sem uso e sem erro não há o que registrar (ex.: clique que nem chegou ao
 * provedor). Devolver null aqui evita linha fantasma na contabilidade. */
export function linhaDeGasto(
  modo: string,
  usage: UsoLlm | undefined,
  durationMs: number,
  erro?: string | null,
  agora: Date = new Date(),
): LinhaGasto | null {
  const semUso = !usage || !(usage.calls ?? 0);
  if (semUso && !erro) return null;

  const primeiro = usage?.providers?.[0];
  return {
    startedAt: new Date(agora.getTime() - Math.max(0, durationMs)),
    finishedAt: agora,
    status: erro ? "failed" : "success",
    trigger: "manual",
    mode: modo,
    durationMs: Math.max(0, durationMs),
    errorMessage: erro ?? null,
    inputTokens: usage?.input_tokens ?? null,
    outputTokens: usage?.output_tokens ?? null,
    cacheReadTokens: usage?.cache_read_tokens ?? null,
    cacheWriteTokens: usage?.cache_write_tokens ?? null,
    // `?? null` e não `?? 0`: modelo fora da tabela de preços devolve null,
    // que significa "custo desconhecido". Zerar viraria gasto invisível.
    costUsd: usage?.total_cost_usd ?? null,
    llmProvider: primeiro?.provider ?? null,
    llmModel: primeiro?.model ?? null,
  };
}
