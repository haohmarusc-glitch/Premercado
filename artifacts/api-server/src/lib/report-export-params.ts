// Validação do corpo de POST /reports/export.
//
// Fica fora da rota pelo mesmo motivo de backtest-params.ts: a rota precisa de
// express + banco + SMTP pra rodar, e as regras aqui são puras — dá pra testar
// cada uma sem subir nada.

/**
 * Telas de análise que podem exportar relatório. Lista fechada de propósito:
 * `reports.mode` alimenta o filtro da tela Histórico e o rótulo do relatório,
 * então aceitar string livre encheria o histórico de modos órfãos que nenhuma
 * tela sabe exibir.
 *
 * Espelha ROTULO_POR_MODO_EXPORTADO no frontend
 * (premarket/src/components/exportar-relatorio.tsx) — os dois andam juntos.
 */
export const MODOS_EXPORTAVEIS = [
  "tela_backtest",
  "tela_radar",
  "tela_cenarios",
  "tela_veredito",
  "tela_earnings_reaction",
  "tela_entry_exit_study",
  "tela_sector_ai",
  "tela_sector_coal",
  "tela_analise_rapida",
] as const;

/**
 * ~256KB de markdown. O maior relatório real (Radar completo, 109 correlações
 * + 51 earnings) dá ~40KB; o teto existe pra um bug de laço no frontend não
 * gravar um blob gigante na tabela nem tentar enviá-lo por SMTP.
 */
export const LIMITE_MARKDOWN = 256 * 1024;

/** Teto de tickers por relatório — o campo é um array livre vindo do cliente. */
export const LIMITE_TICKERS = 50;

export interface ExportValidado {
  titulo: string;
  markdown: string;
  mode: string;
  tickers: string[];
  enviar: boolean;
}

export type ExportParseResult =
  | { ok: true; valor: ExportValidado }
  | { ok: false; status: number; erro: string };

export function parseExportBody(body: unknown): ExportParseResult {
  const b = (body ?? {}) as Record<string, unknown>;

  const titulo = typeof b.titulo === "string" ? b.titulo.trim() : "";
  const markdown = typeof b.markdown === "string" ? b.markdown : "";
  const mode = typeof b.mode === "string" ? b.mode : "";

  if (!titulo) {
    return { ok: false, status: 400, erro: "titulo é obrigatório" };
  }
  // Checa markdown ANTES do mode: quem clicou numa tela sem dados merece
  // "rode a análise antes", não uma reclamação sobre um campo que a tela
  // preenche sozinha e que ele nem sabe que existe.
  if (!markdown.trim()) {
    return { ok: false, status: 400, erro: "Não há dados na tela para exportar — rode a análise antes." };
  }
  if (markdown.length > LIMITE_MARKDOWN) {
    return { ok: false, status: 413, erro: "Relatório grande demais para exportar." };
  }
  if (!(MODOS_EXPORTAVEIS as readonly string[]).includes(mode)) {
    return { ok: false, status: 400, erro: `mode inválido: ${mode || "(vazio)"}` };
  }

  const tickers = Array.isArray(b.tickers)
    ? b.tickers
        .filter((t): t is string => typeof t === "string" && t.trim().length > 0)
        .map((t) => t.trim().toUpperCase())
        .slice(0, LIMITE_TICKERS)
    : [];

  return { ok: true, valor: { titulo, markdown, mode, tickers, enviar: b.enviar === true } };
}
