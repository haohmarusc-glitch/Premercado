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
