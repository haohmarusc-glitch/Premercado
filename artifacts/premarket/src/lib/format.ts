import { format } from "date-fns";

export function formatDate(dateString: string) {
  try {
    return format(new Date(dateString), "MMM dd, yyyy");
  } catch (e) {
    return dateString;
  }
}

export function formatDateTime(dateString: string) {
  try {
    return format(new Date(dateString), "MMM dd, yyyy HH:mm");
  } catch (e) {
    return dateString;
  }
}

// Brasília (America/Sao_Paulo) não observa mais horário de verão desde 2019 —
// offset fixo UTC-3 (mesma convenção usada no backend, ver lib/timezone.ts).
// `new Date().toISOString().split("T")[0]` sozinho usa o dia em UTC, que vira
// 3h antes da meia-noite em horário de Brasília — perto do fim do dia (BRT),
// itens de "hoje" (ex: flash scans) somem cedo demais dessa lista.
const BRT_OFFSET_MS = 3 * 60 * 60 * 1000;

/** Data (YYYY-MM-DD) de "hoje" em horário de Brasília. */
export function todayBRTDateString(now: Date = new Date()): string {
  const brtWallClock = new Date(now.getTime() - BRT_OFFSET_MS);
  return brtWallClock.toISOString().split("T")[0];
}

/**
 * Dias entre hoje (BRT) e uma data "YYYY-MM-DD" -- negativo se já passou.
 * Compara sempre em UTC (mesmo dia calendário, meia-noite) pra não depender
 * do fuso horário/relógio local de quem está vendo a tela: usar `new Date()`
 * local pra "hoje" e o navegador não estar em BRT já bastava pra desalinhar
 * o contador de "vencido Xd"/"em Xd" em exatamente 1 dia.
 */
export function daysUntilBRT(dateStr: string, now: Date = new Date()): number {
  const diffMs = Date.parse(`${dateStr}T00:00:00Z`) - Date.parse(`${todayBRTDateString(now)}T00:00:00Z`);
  return Math.round(diffMs / 86_400_000);
}

/**
 * "YYYY-MM-DD" -> "DD/MM/YYYY" SEM passar por Date: `new Date("2026-08-26")`
 * é meia-noite UTC, e `toLocaleDateString` num navegador em BRT (UTC-3)
 * volta pra 21h do dia 25 — visto em 20/08/2026 no painel de earnings do
 * Veredito: NVDA 26/08 exibida como "25/08/2026" na MESMA linha que dizia
 * "em 6d" (o contador usa daysUntilBRT, que compara em UTC e acertava).
 * String pura não tem fuso pra errar.
 */
export function formatarDataBRT(dateStr: string): string {
  const [y, m, d] = dateStr.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : dateStr;
}
