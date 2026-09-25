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

/** Minutos desde a meia-noite em horário da bolsa (ET). */
export function minutosDoDiaNaBolsa(now: Date = new Date()): number {
  const [h, m] = new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(now).split(":");
  return Number(h) * 60 + Number(m);
}

/**
 * Já passou das 16:00 ET?
 *
 * Usado pela opção "confirmar no fechamento". 16:00 é o fechamento do pregão
 * INTEIRO, e é o corte mesmo nos dias que fecham às 13h: esperar as três horas
 * extras num pregão curto só atrasa a avaliação dentro do MESMO dia, e o dado
 * que ela vai ler já é o do dia completo. A alternativa seria replicar aqui o
 * calendário de pregão curto que mora em `volume_intradiario.py` -- calendário
 * em dois idiomas é a armadilha que a parte 2 acabou de fechar.
 */
export const FECHAMENTO_DO_PREGAO_MIN = 16 * 60;

export function pregaoEncerrado(now: Date = new Date()): boolean {
  return minutosDoDiaNaBolsa(now) >= FECHAMENTO_DO_PREGAO_MIN;
}
