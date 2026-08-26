// ─── O bloco estruturado da decisão ──────────────────────────────────────────
// O veredito da IA termina com um bloco ```json que lista, ticker a ticker, a
// AÇÃO decidida e os reason_codes que a sustentam. Ele é o insumo do validador
// determinístico (`veredito_validator.py`): a prosa explica, o bloco decide, e
// as checagens cruzam um contra o outro.
//
// Até aqui esse bloco chegava cru na tela, porque o markdown renderiza um fence
// de código como fence de código. Quem lê via o insumo da máquina. Este módulo
// separa o bloco da prosa para que a decisão seja RENDERIZADA como decisão --
// tabela, não JSON.
//
// Regra que não pode ser afrouxada: o parse daqui espelha
// `extrair_bloco_estruturado()` do Python. Se a tela fosse mais permissiva que
// o validador, ela mostraria uma tabela bonita para um bloco que o validador
// considera INEXISTENTE -- e o texto ainda traria o aviso de "leitura
// degradada" logo abaixo, dizendo o contrário da tabela.

/** Vocabulário fechado de `action` (espelha ACOES_VALIDAS). */
export const ACOES_VALIDAS = [
  "COMPRAR", "AUMENTAR", "MANTER", "REDUZIR", "VENDER", "AGUARDAR",
] as const;

/**
 * Vocabulário conhecido de reason_codes (espelha REASON_CODES_CONHECIDOS).
 * Código fora da lista não é erro -- o validador o registra como WARN porque o
 * vocabulário evolui. A tela faz o mesmo: mostra, marcado como desconhecido.
 */
export const REASON_CODES_CONHECIDOS: Record<string, string> = {
  RSI_SOBRECOMPRADO: "RSI sobrecomprado",
  RSI_SOBREVENDIDO: "RSI sobrevendido",
  TENDENCIA_ALTA: "tendência de alta",
  TENDENCIA_BAIXA: "tendência de baixa",
  EARNINGS_PROXIMO: "earnings próximo",
  RISCO_CORRELACAO: "risco de correlação",
  MACRO_ADVERSO: "macro adverso",
  MACRO_FAVORAVEL: "macro favorável",
  SUPORTE_PROXIMO: "suporte próximo",
  RESISTENCIA_PROXIMA: "resistência próxima",
  VOLUME_FRACO: "volume fraco",
  VOLUME_FORTE: "volume forte",
  VALUATION_ESTICADO: "valuation esticado",
  VALUATION_DESCONTADO: "valuation descontado",
  PLANO_DE_SAIDA: "plano de saída",
  SENTIMENTO_EXTREMO: "sentimento extremo",
  CENARIO_EMPATE: "cenário de empate",
  RUNUP_ESTICADO: "run-up esticado",
  CAPEX_ACELERANDO: "capex acelerando",
  CAPEX_DESACELERANDO: "capex desacelerando",
  CAIXA_CURTO: "caixa curto",
  CAIXA_CONFORTAVEL: "caixa confortável",
  BALANCO_REESTRUTURADO: "balanço reestruturado",
};

export interface DecisaoTicker {
  ticker: string;
  /** Normalizada em maiúsculas. Pode estar FORA de ACOES_VALIDAS. */
  action: string;
  /** `null` quando ausente ou fora de [0, 1] -- o validador marca isso. */
  confidence: number | null;
  reasonCodes: string[];
}

export interface BlocoDoVeredito {
  /** O texto sem o bloco consumido. Igual ao original quando não houve bloco. */
  prosa: string;
  /** `null` quando não há bloco válido -- aí a prosa segue intacta, com fence. */
  decisoes: DecisaoTicker[] | null;
}

// Mesmo formato do `_BLOCO_JSON_RE` do Python: fence ```json cujo conteúdo
// abre em `{` e fecha em `}`.
const FENCE_JSON = /```json\s*(\{[\s\S]*?\})\s*```/g;

function normalizarItem(item: unknown): DecisaoTicker | null {
  if (typeof item !== "object" || item === null || Array.isArray(item)) return null;
  const bruto = item as Record<string, unknown>;
  const ticker = typeof bruto.ticker === "string" ? bruto.ticker.trim().toUpperCase() : "";
  if (!ticker) return null;

  const action = typeof bruto.action === "string" ? bruto.action.trim().toUpperCase() : "";

  // `confidence` só vira número quando é número mesmo e cabe em [0, 1]. String
  // numérica NÃO é aceita: o contrato pede número, e converter aqui esconderia
  // da tela o mesmo desvio que o validador aponta como erro.
  const c = bruto.confidence;
  const confidence =
    typeof c === "number" && Number.isFinite(c) && c >= 0 && c <= 1 ? c : null;

  const reasonCodes = Array.isArray(bruto.reason_codes)
    ? bruto.reason_codes
        .filter((r): r is string => typeof r === "string" && r.trim() !== "")
        .map((r) => r.trim().toUpperCase())
    : [];

  return { ticker, action, confidence, reasonCodes };
}

/**
 * Separa o bloco estruturado da prosa.
 *
 * Pega o ÚLTIMO fence ```json que contenha `"tickers"` -- o formato pede o
 * bloco no fim, mas a prosa pode legitimamente trazer outros trechos de código
 * antes (e traz: o veredito às vezes cita payload de ferramenta).
 *
 * Quando não há bloco, ou o JSON não parseia, ou a forma não bate, devolve o
 * texto INTACTO. Melhor mostrar o fence cru do que engolir em silêncio um
 * bloco que ninguém conseguiu ler: o fence cru pelo menos denuncia o problema.
 */
export function extrairBlocoDoVeredito(conteudo: string): BlocoDoVeredito {
  if (!conteudo) return { prosa: conteudo ?? "", decisoes: null };

  const candidatos = [...conteudo.matchAll(FENCE_JSON)].filter((m) =>
    m[1].includes('"tickers"'),
  );
  if (candidatos.length === 0) return { prosa: conteudo, decisoes: null };

  const escolhido = candidatos[candidatos.length - 1];
  let bloco: unknown;
  try {
    bloco = JSON.parse(escolhido[1]);
  } catch {
    return { prosa: conteudo, decisoes: null };
  }
  if (typeof bloco !== "object" || bloco === null || Array.isArray(bloco)) {
    return { prosa: conteudo, decisoes: null };
  }
  const lista = (bloco as Record<string, unknown>).tickers;
  if (!Array.isArray(lista)) return { prosa: conteudo, decisoes: null };

  const decisoes = lista.map(normalizarItem).filter((d): d is DecisaoTicker => d !== null);
  // Lista vazia (ou só de itens sem ticker) não é decisão: renderizar uma
  // tabela vazia no lugar do bloco esconderia que o modelo não decidiu nada.
  if (decisoes.length === 0) return { prosa: conteudo, decisoes: null };

  const inicio = escolhido.index ?? 0;
  const prosa =
    conteudo.slice(0, inicio) + conteudo.slice(inicio + escolhido[0].length);

  return { prosa: prosa.replace(/\n{3,}$/, "\n").trimEnd(), decisoes };
}

/** Rótulo legível de um reason_code; devolve o próprio código se desconhecido. */
export function rotuloDaRazao(codigo: string): string {
  return REASON_CODES_CONHECIDOS[codigo] ?? codigo.toLowerCase().replace(/_/g, " ");
}

export function razaoConhecida(codigo: string): boolean {
  return codigo in REASON_CODES_CONHECIDOS;
}

export function acaoValida(action: string): boolean {
  return (ACOES_VALIDAS as readonly string[]).includes(action);
}
