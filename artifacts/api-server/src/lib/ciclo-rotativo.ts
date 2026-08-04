/**
 * Rotação da ordem de um lote periódico.
 *
 * Existe por causa de um efeito colateral do descarte por idade da fila Python
 * (ver python-queue.ts). A fila é FIFO: quem entra por último espera mais e,
 * sob pressão, é sempre o primeiro a estourar o prazo e ser descartado. Com
 * ordem fixa isso vira starvation determinística -- o último do lote (hoje o
 * squeeze, que ainda por cima é o mais lento) simplesmente nunca mais rodaria
 * numa manhã movimentada, e sem erro nenhum: só pararia de disparar alertas.
 *
 * Girar o ponto de partida a cada ciclo espalha o descarte entre todos em vez
 * de concentrá-lo sempre no mesmo. Não reduz o descarte -- redistribui.
 *
 * Puro de propósito: é a parte que dá pra testar sem subir db, mailer nem
 * subprocesso (mesmo padrão de background-checkers.ts e portfolio-math.ts).
 */

/** `itens` girado para começar em `inicio` (índice tratado circularmente). */
export function ordemRotacionada<T>(itens: T[], inicio: number): T[] {
  if (itens.length === 0) return [];
  // `%` de JS preserva o sinal do dividendo: sem o ajuste, início negativo
  // devolveria índice negativo e itens undefined.
  const base = ((inicio % itens.length) + itens.length) % itens.length;
  return itens.map((_, i) => itens[(base + i) % itens.length]);
}

/**
 * Quantas tarefas Python uma volta completa do ciclo enfileira.
 *
 * `checkAlerts` enfileira get_quotes e get_technicals (uma cada, quando há
 * alertas do tipo correspondente); `rodarCheckersDeMercado` enfileira o lote
 * run_checkers. Três é o teto de uma volta.
 */
export const TAREFAS_POR_CICLO = 3;

/**
 * O ciclo só dispara se a fila tiver drenado a volta anterior.
 *
 * O descarte por idade da fila (python-queue.ts) é um freio na SAÍDA: a tarefa
 * já esperou o prazo inteiro quando é descartada, e nesse meio tempo ocupou
 * lugar na frente das outras. Este é o freio na ENTRADA, e custa zero.
 *
 * Ele é necessário porque a fila está no limite por construção: get_quotes
 * (60s) + get_technicals (60s) + run_checkers (180s) = 300s de trabalho por
 * ciclo de 300s. Zero folga -- qualquer atraso vira dívida que o ciclo seguinte
 * herda, e empilhar mais um lote em cima só empurra a espera pra frente.
 * Produção 04/08: esperas de 211s e 208s com 5 e 6 tarefas pendentes.
 *
 * Pular não perde informação: o ciclo seguinte vem 5 minutos depois e roda com
 * dado MAIS NOVO que o que este teria produzido.
 */
export function deveDispararCiclo(
  pendentes: number,
  limite: number = TAREFAS_POR_CICLO,
): boolean {
  return pendentes < limite;
}
