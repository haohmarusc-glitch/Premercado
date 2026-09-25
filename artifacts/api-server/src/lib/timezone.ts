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

/**
 * Data (YYYY-MM-DD) do pregão americano agora, em horário da BOLSA.
 *
 * Por Intl e não por offset fixo: ao contrário de Brasília, Nova York observa
 * horário de verão, então o offset varia entre -4 e -5 ao longo do ano. Um
 * offset fixo erraria a data por uma hora em metade do calendário -- e o uso
 * disto é justamente comparar com a data das barras que o Python devolveu, onde
 * errar o dia significa avaliar um alerta contra o pregão de ontem.
 *
 * `en-CA` porque seu formato de data curta já é YYYY-MM-DD.
 */
export function dataDaBolsa(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}
