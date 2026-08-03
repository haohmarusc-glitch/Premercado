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
