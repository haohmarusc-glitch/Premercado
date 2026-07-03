// Brasília (America/Sao_Paulo) não observa mais horário de verão desde 2019 —
// offset fixo UTC-3 (mesma convenção já usada em scheduler.ts). Usar estes
// helpers em vez de `new Date(); setHours(0,0,0,0)` ou `toISOString().split("T")[0]`
// direto, que calculam "hoje" no fuso local do processo (UTC nos containers),
// fazendo o dia virar 3h cedo demais para um usuário em horário de Brasília.
const BRT_OFFSET_MS = 3 * 60 * 60 * 1000;

/** Instante UTC correspondente à meia-noite de "hoje" em horário de Brasília. */
export function startOfTodayBRT(now: Date = new Date()): Date {
  const brtWallClock = new Date(now.getTime() - BRT_OFFSET_MS);
  brtWallClock.setUTCHours(0, 0, 0, 0);
  return new Date(brtWallClock.getTime() + BRT_OFFSET_MS);
}

/** Data (YYYY-MM-DD) de "hoje" em horário de Brasília. */
export function todayBRTDateString(now: Date = new Date()): string {
  const brtWallClock = new Date(now.getTime() - BRT_OFFSET_MS);
  return brtWallClock.toISOString().split("T")[0];
}
