/**
 * Decisão de provedor sob teto de custo diário.
 *
 * O teto existia mas não segurava nada. O caminho era:
 *
 *   gasto >= teto  ->  AGENT_PROVIDER=gemini  ->  provider.py monta a ordem
 *   [gemini, anthropic, openrouter, openai, kimi]  ->  gemini falha (o modelo
 *   do tier "full" responde 404)  ->  FallbackClient cai para o PRÓXIMO da
 *   ordem, que é justamente o anthropic que o teto queria evitar.
 *
 * Ou seja: estourar o teto trocava uma chamada cara por uma chamada falha MAIS
 * a mesma chamada cara. Visto em produção com spentToday em US$ 3,29 contra
 * teto de US$ 2,00.
 *
 * A correção é a ordem, não o provedor: ao rebaixar por orçamento, o provedor
 * estourado sai da cadeia inteira (AGENT_PROVIDER_ORDER), não só da primeira
 * posição. Consequência aceita e deliberada: se nenhum provedor barato
 * responder, a run FALHA em vez de gastar. É o que "teto" significa -- e a
 * falha é visível (agent_runs.status=failed, sem e-mail), enquanto o estouro
 * silencioso não era.
 *
 * Módulo puro: sem db, sem env, sem relógio. O runner.ts lê as linhas e passa
 * pra cá (mesmo padrão de portfolio-math.ts e report-preflight.ts).
 */

/**
 * Espelho de `_DEFAULT_ORDER` em agent/provider.py. As duas listas PRECISAM
 * bater: aqui montamos a ordem que vai no AGENT_PROVIDER_ORDER, e lá ela é
 * consumida. Um provedor que exista só de um lado silenciosamente sai (ou
 * entra) da cadeia de fallback. Há teste que compara as duas fontes.
 */
export const PROVIDER_FALLBACK_ORDER = ["anthropic", "gemini", "openrouter", "openai", "kimi"];

export const DEFAULT_PRIMARY = "anthropic";

export type RunCostRow = {
  /** numeric do pg chega como string; null = modelo sem preço conhecido. */
  costUsd: number | string | null;
  /** csv de provedores usados na run (uma run pode trocar no meio). */
  llmProvider: string | null;
};

export type BudgetInput = {
  agentProvider: string | null;
  /** null = sem teto configurado. */
  dailyBudgetUsd: number | string | null;
  cheapProvider: string;
  /** Runs de hoje (dia BRT) -- quem filtra por data é o chamador. */
  runsToday: RunCostRow[];
};

export type BudgetDecision = {
  /** Valor de AGENT_PROVIDER; undefined = deixa provider.py usar o default. */
  provider?: string;
  /** Valor de AGENT_PROVIDER_ORDER; undefined = não sobrescreve a ordem. */
  order?: string;
  exceeded: boolean;
  spentToday: number;
  budget: number | null;
  /**
   * Runs de hoje no provedor primário cujo custo veio null (modelo fora do
   * MODEL_PRICING). Elas somam ZERO no gasto -- é um furo real no teto, então
   * o chamador avisa em vez de deixar passar despercebido.
   */
  unpricedRuns: number;
  /**
   * true quando o rebaixamento não muda nada porque o provedor barato É o
   * primário. Nesse caso o teto é decorativo e o usuário precisa saber.
   */
  downgradeIneffective: boolean;
};

function toNumber(v: number | string | null): number | null {
  if (v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Cadeia de fallback para depois do estouro: começa no provedor barato e
 * REMOVE o primário. Remover é o ponto todo desta função -- deixá-lo em
 * qualquer posição devolve o teto ao estado furado.
 */
export function fallbackOrderExcluding(cheapProvider: string, primary: string): string[] {
  const resto = PROVIDER_FALLBACK_ORDER.filter((p) => p !== cheapProvider && p !== primary);
  return [cheapProvider, ...resto];
}

export function decideProvider(input: BudgetInput): BudgetDecision {
  const primary = input.agentProvider || DEFAULT_PRIMARY;
  const budget = toNumber(input.dailyBudgetUsd);

  // Só as runs que passaram pelo provedor primário contam contra o teto dele.
  // llmProvider é csv porque uma run pode trocar de provedor no meio.
  const doPrimario = input.runsToday.filter((r) =>
    (r.llmProvider ?? "").split(",").map((p) => p.trim()).includes(primary),
  );
  const spentToday = doPrimario.reduce((soma, r) => soma + (toNumber(r.costUsd) ?? 0), 0);
  const unpricedRuns = doPrimario.filter((r) => toNumber(r.costUsd) === null).length;

  const base = {
    exceeded: false,
    spentToday,
    budget,
    unpricedRuns,
    downgradeIneffective: false,
  };

  if (budget === null) {
    return { ...base, provider: input.agentProvider ?? undefined };
  }
  if (spentToday < budget) {
    return { ...base, provider: input.agentProvider ?? undefined };
  }

  // Teto estourado.
  if (input.cheapProvider === primary) {
    // Rebaixar pro próprio primário não economiza nada. Não sobrescrevemos a
    // ordem (não há pra onde ir) e sinalizamos que o teto está decorativo.
    return { ...base, exceeded: true, provider: input.cheapProvider, downgradeIneffective: true };
  }

  return {
    ...base,
    exceeded: true,
    provider: input.cheapProvider,
    order: fallbackOrderExcluding(input.cheapProvider, primary).join(","),
  };
}
